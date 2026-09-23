-- Thirty-day price review horizon, matching the existing text-AI policy horizon.
-- Existing history is preserved; only new paid reservations require current rates.
CREATE FUNCTION private.media_price_max_age() RETURNS interval
 LANGUAGE sql IMMUTABLE SET search_path=pg_catalog AS $$ SELECT interval '30 days' $$;
REVOKE ALL ON FUNCTION private.media_price_max_age() FROM PUBLIC,mediaos_runtime;

ALTER TABLE public.media_jobs ADD COLUMN failure_request_id text;
ALTER TABLE public.media_jobs ADD CONSTRAINT media_failure_request_id_bounded CHECK(
 failure_request_id IS NULL OR (failure_request_id ~ '^[A-Za-z0-9_.:-]{1,255}$'
 AND status IN ('FAILED','UNKNOWN_OUTCOME') AND provider<>'deterministic'));
COMMENT ON COLUMN public.media_jobs.failure_request_id IS
 'Sanitized provider correlation ID for failed or uncertain calls; not a replay authorization.';


CREATE OR REPLACE FUNCTION private.guard_media_history() RETURNS trigger LANGUAGE plpgsql SET search_path=pg_catalog,public AS $$
 BEGIN
 IF TG_OP='DELETE' THEN RAISE EXCEPTION 'Media history is immutable' USING ERRCODE='23514'; END IF;
 IF TG_TABLE_NAME='media_runs' THEN
 IF (to_jsonb(OLD)-ARRAY['status','manifest','manifest_hash','qa_result','next_poll_at','error_category','updated_at','ended_at'])
 IS DISTINCT FROM (to_jsonb(NEW)-ARRAY['status','manifest','manifest_hash','qa_result','next_poll_at','error_category','updated_at','ended_at'])
 OR OLD.status IN ('APPROVED','REJECTED','BLOCKED','FAILED','UNKNOWN_OUTCOME')
 OR (OLD.status='AWAITING_APPROVAL' AND NEW.status NOT IN ('APPROVED','REJECTED'))
 OR (OLD.manifest IS NOT NULL AND NEW.manifest IS DISTINCT FROM OLD.manifest) THEN
 RAISE EXCEPTION 'Media run lineage and completed output are immutable' USING ERRCODE='23514'; END IF;
 ELSE
 IF OLD.status<>'RUNNING' OR (to_jsonb(OLD)-ARRAY['status','result','result_hash','actual_cost','retryable','retry_at','error_category','ended_at','failure_request_id'])
 IS DISTINCT FROM (to_jsonb(NEW)-ARRAY['status','result','result_hash','actual_cost','retryable','retry_at','error_category','ended_at','failure_request_id']) THEN
 RAISE EXCEPTION 'Media job history is immutable' USING ERRCODE='23514'; END IF;
 END IF;
 RETURN NEW; END $$;

CREATE OR REPLACE FUNCTION public.create_media_profile(influencer uuid,visual uuid,p jsonb) RETURNS uuid
 LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,public AS $$
 DECLARE cfg public.visual_config_versions; result uuid; n int; checked timestamptz;
 BEGIN
 IF NOT public.has_role('ADMIN') THEN RAISE EXCEPTION 'Administrator required' USING ERRCODE='42501'; END IF;
 IF NOT private.media_keys(p,ARRAY['schema_version','voice_id','tts_model','presenter_provider','tts_usd_per_1000_characters','avatar_usd_per_second','price_reference','price_checked_at'])
 OR p->'schema_version' IS DISTINCT FROM '1'::jsonb OR jsonb_typeof(p->'voice_id') IS DISTINCT FROM 'string'
 OR p->>'voice_id' !~ '^[A-Za-z0-9_-]{1,128}$' OR jsonb_typeof(p->'tts_model') IS DISTINCT FROM 'string'
 OR p->>'tts_model' NOT IN ('eleven_multilingual_v2','eleven_v3')
 OR p->>'presenter_provider' IS DISTINCT FROM 'heygen' OR jsonb_typeof(p->'price_reference') IS DISTINCT FROM 'string'
 OR length(btrim(p->>'price_reference')) NOT BETWEEN 1 AND 1000 THEN
 RAISE EXCEPTION 'Invalid media profile' USING ERRCODE='23514'; END IF;
 PERFORM private.media_decimal(p,'tts_usd_per_1000_characters',1000);
 PERFORM private.media_decimal(p,'avatar_usd_per_second',1000);
 checked:=private.media_utc(p,'price_checked_at');
 IF checked>clock_timestamp() OR checked<=clock_timestamp()-private.media_price_max_age() THEN
 RAISE EXCEPTION 'Media prices must have been checked within the last 30 days' USING ERRCODE='23514'; END IF;
 PERFORM 1 FROM public.influencers WHERE tenant_id=public.context_tenant() AND id=influencer FOR UPDATE;
 IF NOT FOUND THEN RAISE EXCEPTION 'Influencer not found' USING ERRCODE='P0002'; END IF;
 SELECT * INTO cfg FROM public.visual_config_versions WHERE tenant_id=public.context_tenant() AND id=visual AND influencer_id=influencer;
 IF cfg.id IS NULL OR cfg.reference_sha256 IS NULL OR EXISTS(SELECT 1 FROM public.visual_config_versions newer
 WHERE newer.tenant_id=cfg.tenant_id AND newer.influencer_id=influencer AND newer.version>cfg.version) THEN
 RAISE EXCEPTION 'Latest owned visual reference required' USING ERRCODE='23514'; END IF;
 SELECT coalesce(max(version),0)+1 INTO n FROM public.media_profiles WHERE tenant_id=cfg.tenant_id AND influencer_id=influencer;
 INSERT INTO public.media_profiles(tenant_id,influencer_id,visual_config_version_id,version,schema_version,payload,content_hash,created_by)
 VALUES(cfg.tenant_id,influencer,visual,n,1,p,private.visual_hash(p),public.context_principal()) RETURNING id INTO result;
 RETURN result; END $$;

CREATE OR REPLACE FUNCTION public.reserve_media_job(runid uuid,stage_name text,p jsonb) RETURNS uuid
 LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,public AS $$
 DECLARE r public.media_runs; profile public.media_profiles; cfg public.visual_config_versions;
 budget public.media_spend_policies; previous public.media_jobs; wid uuid; n int; maximum_attempts int;
 speech jsonb; image jsonb; audio jsonb; avatar jsonb; derived jsonb; expected numeric:=0;
 day_total numeric; run_total numeric; provider_name text; model_name text; skillid uuid; result uuid;
 BEGIN
 IF NOT public.has_role('OPERATOR') THEN RAISE EXCEPTION 'Operator required' USING ERRCODE='42501'; END IF;
 SELECT workflow_run_id INTO wid FROM public.media_runs WHERE tenant_id=public.context_tenant() AND id=runid;
 IF wid IS NULL THEN RAISE EXCEPTION 'Media run not found' USING ERRCODE='P0002'; END IF;
 PERFORM 1 FROM public.workflow_runs WHERE tenant_id=public.context_tenant() AND id=wid FOR UPDATE;
 SELECT * INTO r FROM public.media_runs WHERE tenant_id=public.context_tenant() AND id=runid FOR UPDATE;
 IF NOT private.media_keys(p,ARRAY['schema_version','script_hash']) OR p->'schema_version' IS DISTINCT FROM '1'::jsonb
 OR p->>'script_hash' IS DISTINCT FROM r.script_hash OR stage_name IS NULL
 OR stage_name NOT IN ('SPEECH','IMAGE_UPLOAD','AUDIO_UPLOAD','AVATAR_SUBMIT','AVATAR_POLL','COMPOSE') THEN
 RAISE EXCEPTION 'Invalid media stage input' USING ERRCODE='23514'; END IF;
 IF r.status IN ('AWAITING_APPROVAL','APPROVED','REJECTED','BLOCKED','FAILED','UNKNOWN_OUTCOME')
 OR EXISTS(SELECT 1 FROM public.media_jobs WHERE tenant_id=r.tenant_id AND media_run_id=r.id AND status IN ('RUNNING','UNKNOWN_OUTCOME')) THEN
 RAISE EXCEPTION 'Media stage is terminal, already running or has an unknown outcome' USING ERRCODE='23514'; END IF;
 PERFORM private.check_media_current(r);
 SELECT * INTO profile FROM public.media_profiles WHERE tenant_id=r.tenant_id AND id=r.profile_id;
 SELECT * INTO cfg FROM public.visual_config_versions WHERE tenant_id=r.tenant_id AND id=r.visual_config_version_id;
 SELECT * INTO previous FROM public.media_jobs WHERE tenant_id=r.tenant_id AND media_run_id=r.id AND stage=stage_name ORDER BY attempt DESC LIMIT 1;
 n:=coalesce(previous.attempt,0)+1; maximum_attempts:=CASE WHEN stage_name='AVATAR_POLL' THEN 90 ELSE 3 END;
 IF n>maximum_attempts OR previous.status='UNKNOWN_OUTCOME'
 OR (previous.status='FAILED' AND (NOT previous.retryable OR previous.retry_at>clock_timestamp()))
 OR (stage_name<>'AVATAR_POLL' AND previous.status='SUCCEEDED') THEN
 RAISE EXCEPTION 'Stage retry limit, cooldown or completed checkpoint prevents execution' USING ERRCODE='23514'; END IF;
 IF (stage_name='SPEECH' AND r.status<>'CREATED')
 OR (stage_name='IMAGE_UPLOAD' AND r.status<>'SPEECH_READY')
 OR (stage_name='AUDIO_UPLOAD' AND r.status<>'IMAGE_READY')
 OR (stage_name='AVATAR_SUBMIT' AND r.status<>'ASSETS_READY')
 OR (stage_name='AVATAR_POLL' AND (r.status<>'AVATAR_PENDING' OR r.next_poll_at>clock_timestamp()))
 OR (stage_name='COMPOSE' AND r.status<>'AVATAR_READY') THEN
 RAISE EXCEPTION 'Media stage does not match current checkpoint or poll cooldown' USING ERRCODE='23514'; END IF;
 speech:=private.media_success(r.id,'SPEECH'); image:=private.media_success(r.id,'IMAGE_UPLOAD');
 audio:=private.media_success(r.id,'AUDIO_UPLOAD'); avatar:=private.media_success(r.id,'AVATAR_SUBMIT');
 provider_name:=CASE WHEN stage_name='SPEECH' THEN 'elevenlabs' WHEN stage_name='COMPOSE' THEN 'deterministic' ELSE 'heygen' END;
 model_name:=CASE WHEN stage_name='SPEECH' THEN profile.payload->>'tts_model' WHEN stage_name='COMPOSE' THEN 'ffmpeg' ELSE 'photo-avatar' END;
 IF stage_name='SPEECH' THEN
 expected:=ceil(length(r.script->>'text')*(profile.payload->>'tts_usd_per_1000_characters')::numeric/1000*1000000)/1000000;
 ELSIF stage_name='AVATAR_SUBMIT' THEN
 IF speech IS NULL OR (speech->>'duration_seconds')::numeric NOT BETWEEN 0.001 AND 300 THEN
 RAISE EXCEPTION 'Measured speech duration required before avatar reservation' USING ERRCODE='23514'; END IF;
 expected:=ceil((speech->>'duration_seconds')::numeric)*(profile.payload->>'avatar_usd_per_second')::numeric;
 END IF;
 -- Lock even free stages against policy changes only when a reservation is needed.
 -- Every reservation is retained: failure or uncertain provider response is not a refund.
 IF expected>0 THEN
 -- Rate validity is rechecked for each paid reservation, not only when the profile is created.
 IF private.media_utc(profile.payload,'price_checked_at')>clock_timestamp()
 OR private.media_utc(profile.payload,'price_checked_at')<=clock_timestamp()-private.media_price_max_age() THEN
 RAISE EXCEPTION 'Media prices must have been checked within the last 30 days' USING ERRCODE='23514'; END IF;
 PERFORM pg_advisory_xact_lock(hashtextextended('media-spend:'||r.tenant_id::text,0));
 SELECT * INTO budget FROM public.media_spend_policies WHERE tenant_id=r.tenant_id ORDER BY version DESC LIMIT 1;
 IF budget.id IS NULL OR NOT budget.enabled OR budget.expires_at<=clock_timestamp() THEN
 RAISE EXCEPTION 'An enabled unexpired administrator spend policy is required' USING ERRCODE='23514'; END IF;
 SELECT coalesce(sum(expected_max_cost),0) INTO run_total FROM public.media_jobs WHERE tenant_id=r.tenant_id AND media_run_id=r.id;
 SELECT coalesce(sum(expected_max_cost),0) INTO day_total FROM public.media_jobs WHERE tenant_id=r.tenant_id
 AND started_at>=date_trunc('day',clock_timestamp() AT TIME ZONE 'UTC') AT TIME ZONE 'UTC';
 IF run_total+expected>budget.per_run_usd OR day_total+expected>budget.per_day_usd THEN
 RAISE EXCEPTION 'Media spend reservation would exceed the configured ceiling' USING ERRCODE='23514'; END IF;
 END IF;
 derived:=jsonb_build_object('schema_version',1,'stage',stage_name,'script_hash',r.script_hash,
 'profile_hash',profile.content_hash,'visual_reference_sha256',cfg.reference_sha256,
 'speech_sha256',speech->>'audio_sha256','image_asset_id',image->>'asset_id',
 'audio_asset_id',audio->>'asset_id','avatar_request_id',avatar->>'request_id');
 INSERT INTO public.skill_runs(tenant_id,workflow_run_id,skill_identifier,skill_version,input_schema_version,output_schema_version,
 provider,model,adapter,attempt,step_key,input_hash,status,cost,is_mock)
 VALUES(r.tenant_id,r.workflow_run_id,'media.'||lower(stage_name),'1.0.0',1,1,provider_name,model_name,'speaking-media-v1',n,
 'media.'||lower(stage_name)||':'||r.id||CASE WHEN stage_name='AVATAR_POLL' THEN ':observation:'||n::text ELSE '' END,
 private.visual_hash(derived),'RUNNING',CASE WHEN provider_name='deterministic' THEN 0 ELSE NULL END,false)
 RETURNING id INTO skillid;
 INSERT INTO public.cost_events(tenant_id,workflow_run_id,skill_run_id,provider,model,input_tokens,output_tokens,cost,currency,price_version)
 VALUES(r.tenant_id,r.workflow_run_id,skillid,provider_name,model_name,0,0,CASE WHEN provider_name='deterministic' THEN 0 ELSE NULL END,
 'USD',CASE WHEN provider_name='deterministic' THEN 'deterministic-zero-v1' ELSE 'media-profile:'||profile.id::text END);
 INSERT INTO public.media_jobs(tenant_id,media_run_id,workflow_run_id,stage,attempt,provider,input,input_hash,expected_max_cost,
 actual_cost,spend_policy_id,skill_run_id,status)
 VALUES(r.tenant_id,r.id,r.workflow_run_id,stage_name,n,provider_name,derived,private.visual_hash(derived),expected,
 CASE WHEN provider_name='deterministic' THEN 0 ELSE NULL END,budget.id,skillid,'RUNNING') RETURNING id INTO result;
 PERFORM private.audit_media(r,'MEDIA_JOB_RESERVED',jsonb_build_object('media_job_id',result,'skill_run_id',skillid,'stage',stage_name,
 'attempt',n,'expected_max_cost',expected,'spend_policy_id',budget.id));
 RETURN result; END $$;

CREATE OR REPLACE FUNCTION public.fail_media_job(jobid uuid,category text,retryable boolean,unknown boolean,retry_after_seconds integer,provider_request_id text) RETURNS void
 LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,public AS $$
 DECLARE j public.media_jobs; r public.media_runs; wid uuid; runid uuid; finished timestamptz:=clock_timestamp();
 nextstatus text; retry_time timestamptz; safe_retry boolean;
 BEGIN
 IF NOT public.has_role('OPERATOR') THEN RAISE EXCEPTION 'Operator required' USING ERRCODE='42501'; END IF;
 IF category IS NULL OR category !~ '^[A-Z][A-Z0-9_]{0,79}$' OR retryable IS NULL OR unknown IS NULL
 OR (unknown AND retryable) OR (category='POLICY_BLOCKED' AND retryable)
 OR (retry_after_seconds IS NOT NULL AND retry_after_seconds NOT BETWEEN 0 AND 86400)
 OR (provider_request_id IS NOT NULL AND provider_request_id !~ '^[A-Za-z0-9_.:-]{1,255}$') THEN
 RAISE EXCEPTION 'Safe failure category and outcome classification required' USING ERRCODE='23514'; END IF;
 SELECT workflow_run_id,media_run_id INTO wid,runid FROM public.media_jobs WHERE tenant_id=public.context_tenant() AND id=jobid;
 IF wid IS NULL THEN RAISE EXCEPTION 'Media job not found' USING ERRCODE='P0002'; END IF;
 PERFORM 1 FROM public.workflow_runs WHERE tenant_id=public.context_tenant() AND id=wid FOR UPDATE;
 SELECT * INTO r FROM public.media_runs WHERE tenant_id=public.context_tenant() AND id=runid FOR UPDATE;
 SELECT * INTO j FROM public.media_jobs WHERE tenant_id=public.context_tenant() AND id=jobid FOR UPDATE;
 IF j.status<>'RUNNING' OR r.status IN ('AWAITING_APPROVAL','APPROVED','REJECTED','BLOCKED','FAILED','UNKNOWN_OUTCOME') THEN
 RAISE EXCEPTION 'Only an active job may fail' USING ERRCODE='23514'; END IF;
 IF provider_request_id IS NOT NULL AND j.provider='deterministic' THEN
 RAISE EXCEPTION 'Only provider jobs may have a provider request ID' USING ERRCODE='23514'; END IF;
 safe_retry:=retryable AND NOT unknown AND j.attempt<CASE WHEN j.stage='AVATAR_POLL' THEN 90 ELSE 3 END;
 nextstatus:=CASE WHEN unknown THEN 'UNKNOWN_OUTCOME' WHEN category='POLICY_BLOCKED' THEN 'BLOCKED'
 WHEN safe_retry THEN r.status ELSE 'FAILED' END;
 IF safe_retry THEN retry_time:=finished+make_interval(secs=>greatest(coalesce(retry_after_seconds,0),least(60,power(2,least(j.attempt-1,6))::int))); END IF;
 UPDATE public.media_jobs SET status=CASE WHEN unknown THEN 'UNKNOWN_OUTCOME' ELSE 'FAILED' END,
 retryable=safe_retry,retry_at=retry_time,error_category=category,ended_at=finished,failure_request_id=provider_request_id WHERE tenant_id=j.tenant_id AND id=j.id;
 UPDATE public.skill_runs SET status='FAILED',ended_at=finished,latency_ms=greatest(0,extract(epoch FROM finished-started_at)*1000),
 retryable=safe_retry,retry_at=retry_time,error_category=category WHERE tenant_id=j.tenant_id AND id=j.skill_run_id;
 UPDATE public.media_runs SET status=nextstatus,error_category=category,updated_at=finished,
 ended_at=CASE WHEN nextstatus IN ('BLOCKED','FAILED','UNKNOWN_OUTCOME') THEN finished ELSE NULL END
 WHERE tenant_id=r.tenant_id AND id=r.id RETURNING * INTO r;
 PERFORM private.audit_media(r,'MEDIA_JOB_FAILED',jsonb_build_object('media_job_id',j.id,'skill_run_id',j.skill_run_id,
 'stage',j.stage,'error_category',category,'retryable',safe_retry,'unknown_outcome',unknown,'provider_request_id',provider_request_id));
 END $$;

-- Preserve the historical four/five-argument contract with no fabricated provider ID.
CREATE OR REPLACE FUNCTION public.fail_media_job(jobid uuid,category text,retryable boolean,unknown boolean,retry_after_seconds integer DEFAULT NULL) RETURNS void
 LANGUAGE sql SECURITY DEFINER SET search_path=pg_catalog,public AS $$
 SELECT public.fail_media_job(jobid,category,retryable,unknown,retry_after_seconds,NULL::text) $$;
REVOKE ALL ON FUNCTION public.fail_media_job(uuid,text,boolean,boolean,integer,text) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION public.fail_media_job(uuid,text,boolean,boolean,integer,text) TO mediaos_runtime;
