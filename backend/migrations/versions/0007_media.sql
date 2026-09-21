-- Paid media work starts only after an explicit versioned budget reservation.
-- The original content, evidence and human approval guards remain authoritative.
ALTER TABLE skill_runs DROP CONSTRAINT skill_runs_provider_check;
ALTER TABLE skill_runs ADD CHECK(provider IN ('mock','deterministic','elevenlabs','heygen'));
ALTER TABLE skill_runs ALTER COLUMN cost DROP NOT NULL;
ALTER TABLE skill_runs DROP CONSTRAINT skill_runs_cost_check;
ALTER TABLE skill_runs ADD CHECK(
 (provider IN ('mock','deterministic') AND cost IS NOT NULL AND cost=0)
 OR (provider IN ('elevenlabs','heygen') AND (cost IS NULL OR cost>=0)));
ALTER TABLE cost_events DROP CONSTRAINT cost_events_provider_check;
ALTER TABLE cost_events ADD CHECK(provider IN ('mock','deterministic','elevenlabs','heygen'));
ALTER TABLE cost_events ALTER COLUMN cost DROP NOT NULL;
ALTER TABLE cost_events DROP CONSTRAINT cost_events_cost_check;
ALTER TABLE cost_events ADD CHECK(
 (provider IN ('mock','deterministic') AND cost IS NOT NULL AND cost=0)
 OR (provider IN ('elevenlabs','heygen') AND (cost IS NULL OR cost>=0)));
ALTER TABLE visual_config_versions ADD UNIQUE(tenant_id,id,influencer_id);

CREATE TABLE media_profiles (
 id uuid PRIMARY KEY DEFAULT gen_random_uuid(), tenant_id uuid NOT NULL REFERENCES tenants,
 influencer_id uuid NOT NULL, visual_config_version_id uuid NOT NULL,
 version int NOT NULL CHECK(version>0), schema_version int NOT NULL CHECK(schema_version=1),
 payload jsonb NOT NULL, content_hash text NOT NULL CHECK(content_hash=private.visual_hash(payload)),
 created_by uuid NOT NULL, created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
 UNIQUE(tenant_id,id), UNIQUE(tenant_id,influencer_id,version),
 UNIQUE(tenant_id,id,influencer_id,visual_config_version_id),
 FOREIGN KEY(tenant_id,influencer_id) REFERENCES influencers(tenant_id,id),
 FOREIGN KEY(tenant_id,visual_config_version_id,influencer_id) REFERENCES visual_config_versions(tenant_id,id,influencer_id),
 FOREIGN KEY(tenant_id,created_by) REFERENCES tenant_memberships(tenant_id,principal_id)
);
CREATE TABLE media_spend_policies (
 id uuid PRIMARY KEY DEFAULT gen_random_uuid(), tenant_id uuid NOT NULL REFERENCES tenants,
 version int NOT NULL CHECK(version>0), schema_version int NOT NULL CHECK(schema_version=1),
 enabled boolean NOT NULL, per_run_usd numeric NOT NULL CHECK(per_run_usd>0 AND per_run_usd<=1000),
 per_day_usd numeric NOT NULL CHECK(per_day_usd>0 AND per_day_usd<=10000), expires_at timestamptz NOT NULL,
 payload jsonb NOT NULL, content_hash text NOT NULL CHECK(content_hash=private.visual_hash(payload)),
 created_by uuid NOT NULL, created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
 UNIQUE(tenant_id,id), UNIQUE(tenant_id,version),
 FOREIGN KEY(tenant_id,created_by) REFERENCES tenant_memberships(tenant_id,principal_id)
);
CREATE TABLE media_runs (
 id uuid PRIMARY KEY DEFAULT gen_random_uuid(), tenant_id uuid NOT NULL REFERENCES tenants,
 sequence bigint GENERATED ALWAYS AS IDENTITY, workflow_run_id uuid NOT NULL, influencer_id uuid NOT NULL,
 asset_version_id uuid NOT NULL, research_version_id uuid NOT NULL, qa_report_id uuid NOT NULL,
 content_approval_record_id uuid NOT NULL, profile_id uuid NOT NULL, visual_config_version_id uuid NOT NULL,
 selected_paths jsonb NOT NULL, script jsonb NOT NULL, script_hash text NOT NULL CHECK(script_hash=private.visual_hash(script)),
 idempotency_key text NOT NULL CHECK(length(idempotency_key) BETWEEN 1 AND 128),
 input_hash text NOT NULL CHECK(input_hash ~ '^[0-9a-f]{64}$'),
 status text NOT NULL DEFAULT 'CREATED' CHECK(status IN ('CREATED','SPEECH_READY','IMAGE_READY','ASSETS_READY',
 'AVATAR_PENDING','AVATAR_READY','AWAITING_APPROVAL','APPROVED','REJECTED','BLOCKED','FAILED','UNKNOWN_OUTCOME')),
 manifest jsonb, manifest_hash text, qa_result jsonb, next_poll_at timestamptz,
 error_category text CHECK(error_category ~ '^[A-Z][A-Z0-9_]{0,79}$'),
 created_by uuid NOT NULL, correlation_id uuid NOT NULL,
 created_at timestamptz NOT NULL DEFAULT clock_timestamp(), updated_at timestamptz NOT NULL DEFAULT clock_timestamp(),
 ended_at timestamptz,
 UNIQUE(tenant_id,id), UNIQUE(tenant_id,idempotency_key), UNIQUE(sequence),
 UNIQUE(tenant_id,id,workflow_run_id),
 UNIQUE(tenant_id,id,workflow_run_id,asset_version_id,research_version_id,qa_report_id,manifest_hash),
 FOREIGN KEY(tenant_id,asset_version_id,workflow_run_id,research_version_id)
 REFERENCES content_asset_versions(tenant_id,id,workflow_run_id,research_version_id),
 FOREIGN KEY(tenant_id,qa_report_id,workflow_run_id,asset_version_id,research_version_id)
 REFERENCES qa_reports(tenant_id,id,workflow_run_id,asset_version_id,research_version_id),
 FOREIGN KEY(tenant_id,content_approval_record_id,workflow_run_id,asset_version_id,research_version_id,qa_report_id)
 REFERENCES approval_records(tenant_id,id,workflow_run_id,asset_version_id,research_version_id,qa_report_id),
 FOREIGN KEY(tenant_id,profile_id,influencer_id,visual_config_version_id)
 REFERENCES media_profiles(tenant_id,id,influencer_id,visual_config_version_id),
 FOREIGN KEY(tenant_id,created_by) REFERENCES tenant_memberships(tenant_id,principal_id),
 CHECK((manifest IS NULL)=(manifest_hash IS NULL)),
 CHECK(manifest IS NULL OR manifest_hash=private.visual_hash(manifest)),
 CHECK((manifest IS NULL)=(qa_result IS NULL)),
 CHECK((status IN ('AWAITING_APPROVAL','APPROVED','REJECTED'))=(manifest IS NOT NULL)),
 CHECK((status IN ('AWAITING_APPROVAL','APPROVED','REJECTED','BLOCKED','FAILED','UNKNOWN_OUTCOME'))=(ended_at IS NOT NULL))
);
CREATE TABLE media_jobs (
 id uuid PRIMARY KEY DEFAULT gen_random_uuid(), tenant_id uuid NOT NULL REFERENCES tenants,
 media_run_id uuid NOT NULL, workflow_run_id uuid NOT NULL,
 stage text NOT NULL CHECK(stage IN ('SPEECH','IMAGE_UPLOAD','AUDIO_UPLOAD','AVATAR_SUBMIT','AVATAR_POLL','COMPOSE')),
 attempt int NOT NULL CHECK(attempt BETWEEN 1 AND 90), provider text NOT NULL CHECK(provider IN ('elevenlabs','heygen','deterministic')),
 input jsonb NOT NULL, input_hash text NOT NULL CHECK(input_hash=private.visual_hash(input)),
 expected_max_cost numeric NOT NULL CHECK(expected_max_cost>=0), actual_cost numeric CHECK(actual_cost>=0),
 spend_policy_id uuid, skill_run_id uuid NOT NULL, request_id uuid NOT NULL DEFAULT gen_random_uuid(),
 status text NOT NULL CHECK(status IN ('RUNNING','SUCCEEDED','FAILED','UNKNOWN_OUTCOME')),
 retryable boolean NOT NULL DEFAULT false, retry_at timestamptz,
 error_category text CHECK(error_category ~ '^[A-Z][A-Z0-9_]{0,79}$'),
 result jsonb, result_hash text, started_at timestamptz NOT NULL DEFAULT clock_timestamp(), ended_at timestamptz,
 UNIQUE(tenant_id,id), UNIQUE(tenant_id,media_run_id,stage,attempt), UNIQUE(tenant_id,skill_run_id), UNIQUE(request_id),
 FOREIGN KEY(tenant_id,media_run_id,workflow_run_id) REFERENCES media_runs(tenant_id,id,workflow_run_id),
 FOREIGN KEY(tenant_id,skill_run_id,workflow_run_id) REFERENCES skill_runs(tenant_id,id,workflow_run_id),
 FOREIGN KEY(tenant_id,spend_policy_id) REFERENCES media_spend_policies(tenant_id,id),
 CHECK((status='RUNNING')=(ended_at IS NULL)),
 CHECK((status='SUCCEEDED')=(result IS NOT NULL)), CHECK((result IS NULL)=(result_hash IS NULL)),
 CHECK(result IS NULL OR result_hash=private.visual_hash(result)),
 CHECK(provider<>'deterministic' OR actual_cost=0)
);
CREATE TABLE media_approval_records (
 id uuid PRIMARY KEY DEFAULT gen_random_uuid(), tenant_id uuid NOT NULL REFERENCES tenants,
 media_run_id uuid NOT NULL, workflow_run_id uuid NOT NULL, asset_version_id uuid NOT NULL,
 research_version_id uuid NOT NULL, qa_report_id uuid NOT NULL, content_approval_record_id uuid NOT NULL,
 manifest_hash text NOT NULL, approver_id uuid NOT NULL, decision text NOT NULL CHECK(decision IN ('APPROVE','REJECT')),
 human_checks jsonb NOT NULL, comment text CHECK(length(comment)<=2000),
 created_at timestamptz NOT NULL DEFAULT clock_timestamp(), UNIQUE(tenant_id,id), UNIQUE(tenant_id,media_run_id),
 FOREIGN KEY(tenant_id,media_run_id,workflow_run_id,asset_version_id,research_version_id,qa_report_id,manifest_hash)
 REFERENCES media_runs(tenant_id,id,workflow_run_id,asset_version_id,research_version_id,qa_report_id,manifest_hash),
 FOREIGN KEY(tenant_id,content_approval_record_id,workflow_run_id,asset_version_id,research_version_id,qa_report_id)
 REFERENCES approval_records(tenant_id,id,workflow_run_id,asset_version_id,research_version_id,qa_report_id),
 FOREIGN KEY(tenant_id,approver_id) REFERENCES tenant_memberships(tenant_id,principal_id)
);
CREATE INDEX media_run_history ON media_runs(tenant_id,workflow_run_id,sequence);
CREATE INDEX media_job_history ON media_jobs(tenant_id,media_run_id,stage,attempt);
CREATE UNIQUE INDEX media_one_active_job ON media_jobs(tenant_id,media_run_id) WHERE status='RUNNING';
CREATE INDEX media_daily_reservations ON media_jobs(tenant_id,started_at) WHERE expected_max_cost>0;
CREATE TRIGGER immutable BEFORE UPDATE OR DELETE ON media_profiles FOR EACH ROW EXECUTE FUNCTION immutable_record();
CREATE TRIGGER immutable BEFORE UPDATE OR DELETE ON media_spend_policies FOR EACH ROW EXECUTE FUNCTION immutable_record();
CREATE TRIGGER immutable BEFORE UPDATE OR DELETE ON media_approval_records FOR EACH ROW EXECUTE FUNCTION immutable_record();
CREATE FUNCTION private.guard_media_history() RETURNS trigger LANGUAGE plpgsql SET search_path=pg_catalog,public AS $$
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
 IF OLD.status<>'RUNNING' OR (to_jsonb(OLD)-ARRAY['status','result','result_hash','actual_cost','retryable','retry_at','error_category','ended_at'])
 IS DISTINCT FROM (to_jsonb(NEW)-ARRAY['status','result','result_hash','actual_cost','retryable','retry_at','error_category','ended_at']) THEN
 RAISE EXCEPTION 'Media job history is immutable' USING ERRCODE='23514'; END IF;
 END IF;
 RETURN NEW; END $$;
CREATE TRIGGER media_history BEFORE UPDATE OR DELETE ON media_runs FOR EACH ROW EXECUTE FUNCTION private.guard_media_history();
CREATE TRIGGER media_history BEFORE UPDATE OR DELETE ON media_jobs FOR EACH ROW EXECUTE FUNCTION private.guard_media_history();
DO $$ DECLARE t text; BEGIN
 FOREACH t IN ARRAY ARRAY['media_profiles','media_spend_policies','media_runs','media_jobs','media_approval_records'] LOOP
 EXECUTE format('ALTER TABLE %I ENABLE ROW LEVEL SECURITY',t);
 EXECUTE format('ALTER TABLE %I FORCE ROW LEVEL SECURITY',t);
 EXECUTE format('CREATE POLICY tenant_boundary ON %I USING(tenant_id=public.context_tenant()) WITH CHECK(tenant_id=public.context_tenant())',t);
 EXECUTE format('REVOKE ALL ON %I FROM PUBLIC,mediaos_runtime',t);
 EXECUTE format('GRANT SELECT ON %I TO mediaos_runtime',t);
 END LOOP;
 END $$;

CREATE FUNCTION private.media_keys(p jsonb,keys text[]) RETURNS boolean LANGUAGE sql IMMUTABLE SET search_path=pg_catalog,public AS $$
 SELECT p IS NOT NULL AND jsonb_typeof(p)='object' AND p ?& keys AND p-keys='{}'::jsonb $$;
CREATE FUNCTION private.media_decimal(p jsonb,name text,maximum numeric) RETURNS numeric
 LANGUAGE plpgsql IMMUTABLE SET search_path=pg_catalog,public AS $$
 DECLARE value numeric;
 BEGIN
 IF jsonb_typeof(p->name) IS DISTINCT FROM 'string' OR p->>name !~ '^(0|[1-9][0-9]{0,5})(\.[0-9]{1,6})?$' THEN
 RAISE EXCEPTION 'Positive bounded decimal string required' USING ERRCODE='23514'; END IF;
 value:=(p->>name)::numeric;
 IF value<=0 OR value>maximum THEN RAISE EXCEPTION 'Price or budget exceeds allowed range' USING ERRCODE='23514'; END IF;
 RETURN value; END $$;
CREATE FUNCTION private.media_utc(p jsonb,name text) RETURNS timestamptz
 LANGUAGE plpgsql IMMUTABLE SET search_path=pg_catalog,public AS $$
 BEGIN
 IF jsonb_typeof(p->name) IS DISTINCT FROM 'string' OR p->>name !~ '^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(\.\d{1,6})?(Z|\+00:00)$' THEN
 RAISE EXCEPTION 'UTC timestamp required' USING ERRCODE='23514'; END IF;
 RETURN (p->>name)::timestamptz;
 EXCEPTION WHEN datetime_field_overflow OR invalid_datetime_format THEN
 RAISE EXCEPTION 'Invalid UTC timestamp' USING ERRCODE='23514'; END $$;
CREATE FUNCTION private.audit_media(r public.media_runs,event text,details jsonb DEFAULT '{}'::jsonb)
 RETURNS void LANGUAGE sql SECURITY DEFINER SET search_path=pg_catalog,public AS $$
 INSERT INTO public.audit_events(tenant_id,workflow_run_id,actor_id,event_type,correlation_id,details)
 VALUES(r.tenant_id,r.workflow_run_id,public.context_principal(),event,r.correlation_id,
 jsonb_build_object('media_run_id',r.id,'state',r.status)||details) $$;

CREATE FUNCTION public.create_media_profile(influencer uuid,visual uuid,p jsonb) RETURNS uuid
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
 IF checked>clock_timestamp() THEN RAISE EXCEPTION 'Price check cannot be in future' USING ERRCODE='23514'; END IF;
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

CREATE FUNCTION public.set_media_spend_policy(p jsonb) RETURNS uuid
 LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,public AS $$
 DECLARE result uuid; n int; run_limit numeric; day_limit numeric; expiry timestamptz;
 BEGIN
 IF NOT public.has_role('ADMIN') THEN RAISE EXCEPTION 'Administrator required' USING ERRCODE='42501'; END IF;
 IF NOT private.media_keys(p,ARRAY['schema_version','per_run_usd','per_day_usd','expires_at','enabled'])
 OR p->'schema_version' IS DISTINCT FROM '1'::jsonb OR jsonb_typeof(p->'enabled') IS DISTINCT FROM 'boolean' THEN
 RAISE EXCEPTION 'Invalid media spend policy' USING ERRCODE='23514'; END IF;
 run_limit:=private.media_decimal(p,'per_run_usd',1000); day_limit:=private.media_decimal(p,'per_day_usd',10000);
 expiry:=private.media_utc(p,'expires_at');
 IF (p->>'enabled')::boolean AND expiry<=clock_timestamp() THEN RAISE EXCEPTION 'Enabled spend policy must be unexpired' USING ERRCODE='23514'; END IF;
 PERFORM pg_advisory_xact_lock(hashtextextended('media-spend:'||public.context_tenant()::text,0));
 SELECT coalesce(max(version),0)+1 INTO n FROM public.media_spend_policies WHERE tenant_id=public.context_tenant();
 INSERT INTO public.media_spend_policies(tenant_id,version,schema_version,enabled,per_run_usd,per_day_usd,expires_at,payload,content_hash,created_by)
 VALUES(public.context_tenant(),n,1,(p->>'enabled')::boolean,run_limit,day_limit,expiry,p,private.visual_hash(p),public.context_principal()) RETURNING id INTO result;
 RETURN result; END $$;

CREATE FUNCTION private.media_script(asset jsonb,paths jsonb) RETURNS jsonb
 LANGUAGE plpgsql IMMUTABLE SET search_path=pg_catalog,public AS $$
 DECLARE path text; block jsonb; blocks jsonb:='[]'; narration text:='';
 BEGIN
 IF paths IS NULL OR jsonb_typeof(paths)<>'array' OR jsonb_array_length(paths) NOT BETWEEN 1 AND 10
 OR EXISTS(SELECT 1 FROM jsonb_array_elements(paths) p WHERE jsonb_typeof(p)<>'string')
 OR (SELECT count(*) FROM jsonb_array_elements(paths))<>(SELECT count(DISTINCT value) FROM jsonb_array_elements(paths)) THEN
 RAISE EXCEPTION 'Select one to ten unique approved text paths' USING ERRCODE='23514'; END IF;
 FOR path IN SELECT value FROM jsonb_array_elements_text(paths) LOOP
 IF path NOT IN ('caption','cta') AND path !~ '^slides\.(0|[1-9][0-9]?)\.(headline|body)$' THEN
 RAISE EXCEPTION 'Unsupported narration path' USING ERRCODE='23514'; END IF;
 block:=asset #> string_to_array(path,'.');
 IF block IS NULL OR block->>'kind' NOT IN ('FACT','CREATIVE') OR jsonb_typeof(block->'text')<>'string'
 OR length(btrim(block->>'text'))=0 THEN RAISE EXCEPTION 'Selected approved text is missing' USING ERRCODE='23514'; END IF;
 blocks:=blocks||jsonb_build_array(jsonb_build_object('path',path,'kind',block->>'kind','text',block->>'text','fact_ids',block->'fact_ids'));
 narration:=narration||CASE WHEN narration='' THEN '' ELSE E'\n' END||(block->>'text');
 END LOOP;
 IF coalesce(length(btrim(asset->>'disclosure')),0)=0 THEN RAISE EXCEPTION 'Mandatory AI disclosure required' USING ERRCODE='23514'; END IF;
 narration:=narration||E'\n'||(asset->>'disclosure');
 IF length(narration)>5000 THEN RAISE EXCEPTION 'Narration exceeds 5000 characters' USING ERRCODE='23514'; END IF;
 RETURN jsonb_build_object('schema_version',1,'language',asset->>'language','blocks',blocks,'disclosure',asset->>'disclosure','text',narration);
 END $$;

CREATE FUNCTION private.check_media_current(r public.media_runs) RETURNS void
 LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,public AS $$
 DECLARE w public.workflow_runs; p public.media_profiles; cfg public.visual_config_versions;
 BEGIN
 IF r.tenant_id IS DISTINCT FROM public.context_tenant() THEN RAISE EXCEPTION 'Tenant mismatch' USING ERRCODE='42501'; END IF;
 SELECT * INTO w FROM public.workflow_runs WHERE tenant_id=r.tenant_id AND id=r.workflow_run_id FOR UPDATE;
 PERFORM 1 FROM public.influencers WHERE tenant_id=r.tenant_id AND id=w.influencer_id FOR UPDATE;
 SELECT * INTO p FROM public.media_profiles WHERE tenant_id=r.tenant_id AND id=r.profile_id;
 SELECT * INTO cfg FROM public.visual_config_versions WHERE tenant_id=r.tenant_id AND id=r.visual_config_version_id;
 IF w.id IS NULL OR w.state IS DISTINCT FROM 'APPROVED' OR w.asset_version_id IS DISTINCT FROM r.asset_version_id
 OR w.research_version_id IS DISTINCT FROM r.research_version_id OR w.qa_report_id IS DISTINCT FROM r.qa_report_id
 OR w.influencer_id IS DISTINCT FROM r.influencer_id OR p.id IS NULL OR cfg.id IS NULL OR cfg.reference_sha256 IS NULL
 OR p.influencer_id IS DISTINCT FROM w.influencer_id OR p.visual_config_version_id IS DISTINCT FROM cfg.id
 OR EXISTS(SELECT 1 FROM public.media_profiles newer WHERE newer.tenant_id=r.tenant_id AND newer.influencer_id=w.influencer_id AND newer.version>p.version)
 OR EXISTS(SELECT 1 FROM public.visual_config_versions newer WHERE newer.tenant_id=r.tenant_id AND newer.influencer_id=w.influencer_id AND newer.version>cfg.version)
 OR EXISTS(SELECT 1 FROM public.media_runs newer WHERE newer.tenant_id=r.tenant_id AND newer.workflow_run_id=r.workflow_run_id AND newer.sequence>r.sequence)
 OR NOT EXISTS(SELECT 1 FROM public.approval_records a WHERE a.tenant_id=r.tenant_id AND a.id=r.content_approval_record_id
 AND a.workflow_run_id=w.id AND a.asset_version_id=w.asset_version_id AND a.research_version_id=w.research_version_id
 AND a.qa_report_id=w.qa_report_id AND a.decision='APPROVE')
 OR NOT EXISTS(SELECT 1 FROM public.qa_reports q WHERE q.tenant_id=r.tenant_id AND q.id=r.qa_report_id AND q.status='PASS')
 OR EXISTS(SELECT 1 FROM public.qa_reports q WHERE q.tenant_id=r.tenant_id AND q.asset_version_id=r.asset_version_id AND q.status='BLOCKED')
 OR jsonb_array_length(public.qa_findings(w.id))>0 THEN
 RAISE EXCEPTION 'Latest media run, exact approved content, profile, visual reference and fresh evidence required' USING ERRCODE='23514'; END IF;
 END $$;

CREATE FUNCTION public.start_media_run(workflowid uuid,profileid uuid,paths jsonb,key text,inputhash text) RETURNS uuid
 LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,public AS $$
 DECLARE w public.workflow_runs; p public.media_profiles; r public.media_runs; a public.content_asset_versions; approval uuid; script jsonb;
 BEGIN
 IF NOT public.has_role('OPERATOR') THEN RAISE EXCEPTION 'Operator required' USING ERRCODE='42501'; END IF;
 IF key IS NULL OR length(key) NOT BETWEEN 1 AND 128 OR inputhash IS DISTINCT FROM private.visual_hash(jsonb_build_object(
 'workflow_run_id',workflowid,'profile_id',profileid,'selected_paths',paths)) THEN RAISE EXCEPTION 'Invalid media idempotency request' USING ERRCODE='23514'; END IF;
 SELECT * INTO w FROM public.workflow_runs WHERE tenant_id=public.context_tenant() AND id=workflowid FOR UPDATE;
 IF w.id IS NULL THEN RAISE EXCEPTION 'Workflow not found' USING ERRCODE='P0002'; END IF;
 PERFORM pg_advisory_xact_lock(hashtextextended('media-create:'||w.tenant_id::text||':'||key,0));
 SELECT * INTO r FROM public.media_runs WHERE tenant_id=w.tenant_id AND idempotency_key=key;
 IF r.id IS NOT NULL THEN
 IF r.input_hash IS DISTINCT FROM inputhash THEN RAISE EXCEPTION 'Media idempotency conflict' USING ERRCODE='23505'; END IF;
 RETURN r.id; END IF;
 SELECT * INTO p FROM public.media_profiles WHERE tenant_id=w.tenant_id AND id=profileid AND influencer_id=w.influencer_id;
 IF p.id IS NULL THEN RAISE EXCEPTION 'Media profile not found' USING ERRCODE='P0002'; END IF;
 SELECT id INTO approval FROM public.approval_records WHERE tenant_id=w.tenant_id AND workflow_run_id=w.id
 AND asset_version_id=w.asset_version_id AND research_version_id=w.research_version_id AND qa_report_id=w.qa_report_id AND decision='APPROVE';
 r.tenant_id:=w.tenant_id; r.workflow_run_id:=w.id; r.influencer_id:=w.influencer_id; r.asset_version_id:=w.asset_version_id;
 r.research_version_id:=w.research_version_id; r.qa_report_id:=w.qa_report_id; r.content_approval_record_id:=approval;
 r.profile_id:=p.id; r.visual_config_version_id:=p.visual_config_version_id;
 PERFORM private.check_media_current(r);
 SELECT * INTO a FROM public.content_asset_versions WHERE tenant_id=w.tenant_id AND id=w.asset_version_id;
 script:=private.media_script(a.payload,paths);
 INSERT INTO public.media_runs(tenant_id,workflow_run_id,influencer_id,asset_version_id,research_version_id,qa_report_id,
 content_approval_record_id,profile_id,visual_config_version_id,selected_paths,script,script_hash,idempotency_key,input_hash,created_by,correlation_id)
 VALUES(w.tenant_id,w.id,w.influencer_id,w.asset_version_id,w.research_version_id,w.qa_report_id,approval,p.id,p.visual_config_version_id,
 paths,script,private.visual_hash(script),key,inputhash,public.context_principal(),w.correlation_id) RETURNING * INTO r;
 PERFORM private.audit_media(r,'MEDIA_RUN_CREATED');
 RETURN r.id; END $$;

CREATE FUNCTION private.media_success(runid uuid,stage_name text) RETURNS jsonb
 LANGUAGE sql STABLE SECURITY DEFINER SET search_path=pg_catalog,public AS $$
 SELECT result FROM public.media_jobs WHERE tenant_id=public.context_tenant() AND media_run_id=runid
 AND stage=stage_name AND status='SUCCEEDED' ORDER BY attempt DESC LIMIT 1 $$;

CREATE FUNCTION public.reserve_media_job(runid uuid,stage_name text,p jsonb) RETURNS uuid
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

CREATE FUNCTION private.media_digest(p jsonb,name text) RETURNS boolean LANGUAGE sql IMMUTABLE SET search_path=pg_catalog,public AS $$
 SELECT coalesce(jsonb_typeof(p->name)='string' AND p->>name ~ '^[0-9a-f]{64}$',false) $$;
CREATE FUNCTION private.media_integer(p jsonb,name text,minimum bigint,maximum bigint) RETURNS boolean
 LANGUAGE plpgsql IMMUTABLE SET search_path=pg_catalog,public AS $$
 BEGIN
 IF jsonb_typeof(p->name) IS DISTINCT FROM 'number' OR p->>name !~ '^(0|[1-9][0-9]{0,12})$' THEN RETURN false; END IF;
 RETURN (p->>name)::bigint BETWEEN minimum AND maximum; END $$;
CREATE FUNCTION private.media_duration(p jsonb,name text) RETURNS boolean
 LANGUAGE plpgsql IMMUTABLE SET search_path=pg_catalog,public AS $$
 BEGIN
 IF jsonb_typeof(p->name) IS DISTINCT FROM 'number' THEN RETURN false; END IF;
 RETURN (p->>name)::numeric>0 AND (p->>name)::numeric<=300; END $$;
CREATE FUNCTION private.media_identifier(p jsonb,name text) RETURNS boolean LANGUAGE sql IMMUTABLE SET search_path=pg_catalog,public AS $$
 SELECT coalesce(jsonb_typeof(p->name)='string' AND p->>name ~ '^[A-Za-z0-9_.:-]{1,200}$',false) $$;

CREATE FUNCTION public.finish_media_job(jobid uuid,result jsonb) RETURNS void
 LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,public AS $$
 DECLARE j public.media_jobs; r public.media_runs; wid uuid; runid uuid; profile public.media_profiles;
 cfg public.visual_config_versions; speech jsonb; avatar jsonb; poll jsonb; nextstatus text; prepared_manifest jsonb;
 finished timestamptz:=clock_timestamp(); narration_hash text; newqa jsonb; nextpoll timestamptz;
 BEGIN
 IF NOT public.has_role('OPERATOR') THEN RAISE EXCEPTION 'Operator required' USING ERRCODE='42501'; END IF;
 SELECT workflow_run_id,media_run_id INTO wid,runid FROM public.media_jobs WHERE tenant_id=public.context_tenant() AND id=jobid;
 IF wid IS NULL THEN RAISE EXCEPTION 'Media job not found' USING ERRCODE='P0002'; END IF;
 PERFORM 1 FROM public.workflow_runs WHERE tenant_id=public.context_tenant() AND id=wid FOR UPDATE;
 SELECT * INTO r FROM public.media_runs WHERE tenant_id=public.context_tenant() AND id=runid FOR UPDATE;
 SELECT * INTO j FROM public.media_jobs WHERE tenant_id=public.context_tenant() AND id=jobid FOR UPDATE;
 IF j.status<>'RUNNING' OR r.status IN ('APPROVED','REJECTED','BLOCKED','FAILED','UNKNOWN_OUTCOME','AWAITING_APPROVAL') THEN
 RAISE EXCEPTION 'Job is not an active media attempt' USING ERRCODE='23514'; END IF;
 PERFORM private.check_media_current(r);
 IF result IS NULL OR jsonb_typeof(result)<>'object' OR result->'schema_version' IS DISTINCT FROM '1'::jsonb
 OR octet_length(result::text)>30000 THEN RAISE EXCEPTION 'Invalid media result envelope' USING ERRCODE='23514'; END IF;
 SELECT * INTO profile FROM public.media_profiles WHERE tenant_id=r.tenant_id AND id=r.profile_id;
 SELECT * INTO cfg FROM public.visual_config_versions WHERE tenant_id=r.tenant_id AND id=r.visual_config_version_id;
 speech:=private.media_success(r.id,'SPEECH'); avatar:=private.media_success(r.id,'AVATAR_SUBMIT');
 poll:=private.media_success(r.id,'AVATAR_POLL');
 narration_hash:=encode(public.digest(r.script->>'text','sha256'),'hex');
 nextstatus:=r.status;
 CASE j.stage
 WHEN 'SPEECH' THEN
 IF r.status<>'CREATED' OR NOT private.media_keys(result,ARRAY['schema_version','provider','model','audio_sha256','audio_size_bytes','alignment_sha256','narration_sha256','duration_seconds','request_id','character_count'])
 OR result->>'provider' IS DISTINCT FROM 'elevenlabs' OR result->>'model' IS DISTINCT FROM profile.payload->>'tts_model'
 OR NOT private.media_digest(result,'audio_sha256') OR NOT private.media_digest(result,'alignment_sha256')
 OR result->>'narration_sha256' IS DISTINCT FROM narration_hash OR NOT private.media_integer(result,'audio_size_bytes',1,50000000)
 OR NOT private.media_duration(result,'duration_seconds')
 OR (result->'request_id'<>'null'::jsonb AND NOT private.media_identifier(result,'request_id'))
 OR (result->'character_count'<>'null'::jsonb AND NOT private.media_integer(result,'character_count',1,100000)) THEN
 RAISE EXCEPTION 'Speech result does not match the exact narration and profile' USING ERRCODE='23514'; END IF;
 nextstatus:='SPEECH_READY';
 WHEN 'IMAGE_UPLOAD','AUDIO_UPLOAD' THEN
 IF NOT private.media_keys(result,ARRAY['schema_version','provider','asset_id','mime_type','size_bytes','sha256'])
 OR result->>'provider' IS DISTINCT FROM 'heygen' OR NOT private.media_identifier(result,'asset_id')
 OR NOT private.media_integer(result,'size_bytes',1,50000000) OR NOT private.media_digest(result,'sha256') THEN
 RAISE EXCEPTION 'Invalid provider asset receipt' USING ERRCODE='23514'; END IF;
 IF j.stage='IMAGE_UPLOAD' THEN
 IF r.status<>'SPEECH_READY' OR result->>'sha256' IS DISTINCT FROM cfg.reference_sha256
 OR jsonb_typeof(result->'mime_type') IS DISTINCT FROM 'string' OR result->>'mime_type' NOT IN ('image/png','image/jpeg') THEN
 RAISE EXCEPTION 'Uploaded image must match the pinned reference' USING ERRCODE='23514'; END IF;
 nextstatus:='IMAGE_READY';
 ELSE
 IF r.status<>'IMAGE_READY' OR result->>'sha256' IS DISTINCT FROM speech->>'audio_sha256'
 OR result->>'mime_type' IS DISTINCT FROM 'audio/mpeg'
 OR result->'size_bytes' IS DISTINCT FROM speech->'audio_size_bytes' THEN
 RAISE EXCEPTION 'Uploaded audio must match the completed speech' USING ERRCODE='23514'; END IF;
 nextstatus:='ASSETS_READY'; END IF;
 WHEN 'AVATAR_SUBMIT' THEN
 IF r.status<>'ASSETS_READY' OR NOT private.media_keys(result,ARRAY['schema_version','provider','request_id','status'])
 OR result->>'provider' IS DISTINCT FROM 'heygen' OR NOT private.media_identifier(result,'request_id')
 OR jsonb_typeof(result->'status') IS DISTINCT FROM 'string' OR result->>'status' NOT IN ('QUEUED','RUNNING') THEN
 RAISE EXCEPTION 'Invalid avatar submission receipt' USING ERRCODE='23514'; END IF;
 nextstatus:='AVATAR_PENDING'; nextpoll:=finished+interval '2 seconds';
 WHEN 'AVATAR_POLL' THEN
 IF r.status<>'AVATAR_PENDING' OR NOT private.media_keys(result,ARRAY['schema_version','provider','request_id','status','output_url','duration_seconds','error_category'])
 OR result->>'provider' IS DISTINCT FROM 'heygen' OR result->>'request_id' IS DISTINCT FROM avatar->>'request_id'
 OR jsonb_typeof(result->'status') IS DISTINCT FROM 'string' OR result->>'status' NOT IN ('QUEUED','RUNNING','COMPLETED','FAILED','BLOCKED','CANCELED')
 OR (result->'duration_seconds'<>'null'::jsonb AND NOT private.media_duration(result,'duration_seconds'))
 OR (result->'error_category'<>'null'::jsonb AND (jsonb_typeof(result->'error_category') IS DISTINCT FROM 'string' OR result->>'error_category' !~ '^[A-Z][A-Z0-9_]{0,79}$')) THEN
 RAISE EXCEPTION 'Avatar poll does not identify the exact submitted request' USING ERRCODE='23514'; END IF;
 IF result->>'status'='COMPLETED' THEN
 IF jsonb_typeof(result->'output_url') IS DISTINCT FROM 'string' OR length(result->>'output_url')>4000
 OR result->>'output_url' !~ '^https://[^/@[:space:]]+(/[^[:space:]]*)?$'
 OR NOT private.media_duration(result,'duration_seconds') OR result->'error_category'<>'null'::jsonb THEN
 RAISE EXCEPTION 'Completed avatar requires bounded HTTPS output and measured duration' USING ERRCODE='23514'; END IF;
 nextstatus:='AVATAR_READY';
 ELSE
 IF result->'output_url'<>'null'::jsonb THEN RAISE EXCEPTION 'Incomplete avatar cannot supply a final video' USING ERRCODE='23514'; END IF;
 IF result->>'status' IN ('FAILED','CANCELED','BLOCKED') THEN
 nextstatus:=CASE WHEN result->>'status'='BLOCKED' THEN 'BLOCKED' ELSE 'FAILED' END;
 ELSE
 IF j.attempt>=90 THEN nextstatus:='FAILED'; ELSE nextpoll:=finished+interval '2 seconds'; END IF;
 END IF;
 END IF;
 WHEN 'COMPOSE' THEN
 IF r.status<>'AVATAR_READY' OR NOT private.media_keys(result,ARRAY['schema_version','script_hash','narration_sha256','portrait_sha256','speech_sha256',
 'alignment_sha256','source_video_sha256','video_sha256','video_size_bytes','captions_sha256','caption_text','disclosure',
 'width','height','fps','duration_seconds','audio_present','video_codec','audio_codec','sample_rate','disclosure_burned_in'])
 OR result->>'script_hash' IS DISTINCT FROM r.script_hash OR result->>'narration_sha256' IS DISTINCT FROM narration_hash
 OR result->>'portrait_sha256' IS DISTINCT FROM cfg.reference_sha256 OR result->>'speech_sha256' IS DISTINCT FROM speech->>'audio_sha256'
 OR result->>'alignment_sha256' IS DISTINCT FROM speech->>'alignment_sha256'
 OR NOT private.media_digest(result,'source_video_sha256') OR NOT private.media_digest(result,'video_sha256')
 OR NOT private.media_digest(result,'captions_sha256') OR NOT private.media_integer(result,'video_size_bytes',1,200000000)
 OR result->>'caption_text' IS DISTINCT FROM r.script->>'text' OR result->>'disclosure' IS DISTINCT FROM r.script->>'disclosure'
 OR result->'width' IS DISTINCT FROM '1080'::jsonb OR result->'height' IS DISTINCT FROM '1920'::jsonb
 OR result->'fps' IS DISTINCT FROM '24'::jsonb OR result->'sample_rate' IS DISTINCT FROM '48000'::jsonb
 OR result->'audio_present' IS DISTINCT FROM 'true'::jsonb OR result->'disclosure_burned_in' IS DISTINCT FROM 'true'::jsonb
 OR result->>'video_codec' IS DISTINCT FROM 'h264' OR result->>'audio_codec' IS DISTINCT FROM 'aac'
 OR NOT private.media_duration(result,'duration_seconds') OR poll->>'status' IS DISTINCT FROM 'COMPLETED'
 OR abs((result->>'duration_seconds')::numeric-(speech->>'duration_seconds')::numeric)>0.5 THEN
 RAISE EXCEPTION 'Composed video must match exact speech, portrait, disclosure and technical policy' USING ERRCODE='23514'; END IF;
 prepared_manifest:=result; newqa:=jsonb_build_object('schema_version',1,'status','PASS','findings','[]'::jsonb);
 nextstatus:='AWAITING_APPROVAL';
 ELSE RAISE EXCEPTION 'Invalid media stage' USING ERRCODE='23514';
 END CASE;
 UPDATE public.media_jobs SET status='SUCCEEDED',result=finish_media_job.result,result_hash=private.visual_hash(finish_media_job.result),ended_at=finished
 WHERE tenant_id=j.tenant_id AND id=j.id;
 UPDATE public.skill_runs SET status='SUCCEEDED',output=finish_media_job.result,ended_at=finished,
 latency_ms=greatest(0,extract(epoch FROM finished-started_at)*1000),asset_version_id=r.asset_version_id,research_version_id=r.research_version_id
 WHERE tenant_id=j.tenant_id AND id=j.skill_run_id;
 UPDATE public.media_runs SET status=nextstatus,manifest=prepared_manifest,
 manifest_hash=CASE WHEN prepared_manifest IS NULL THEN NULL ELSE private.visual_hash(prepared_manifest) END,
 qa_result=newqa,next_poll_at=nextpoll,updated_at=finished,
 error_category=CASE WHEN nextstatus IN ('FAILED','BLOCKED') THEN coalesce(result->>'error_category','PROVIDER_TERMINAL') ELSE NULL END,
 ended_at=CASE WHEN nextstatus IN ('AWAITING_APPROVAL','FAILED','BLOCKED') THEN finished ELSE NULL END
 WHERE tenant_id=r.tenant_id AND id=r.id RETURNING * INTO r;
 PERFORM private.audit_media(r,'MEDIA_JOB_COMPLETED',jsonb_build_object('media_job_id',j.id,'skill_run_id',j.skill_run_id,
 'stage',j.stage,'result_hash',private.visual_hash(result)));
 END $$;

CREATE FUNCTION public.fail_media_job(jobid uuid,category text,retryable boolean,unknown boolean,retry_after_seconds integer DEFAULT NULL) RETURNS void
 LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,public AS $$
 DECLARE j public.media_jobs; r public.media_runs; wid uuid; runid uuid; finished timestamptz:=clock_timestamp();
 nextstatus text; retry_time timestamptz; safe_retry boolean;
 BEGIN
 IF NOT public.has_role('OPERATOR') THEN RAISE EXCEPTION 'Operator required' USING ERRCODE='42501'; END IF;
 IF category IS NULL OR category !~ '^[A-Z][A-Z0-9_]{0,79}$' OR retryable IS NULL OR unknown IS NULL
 OR (unknown AND retryable) OR (category='POLICY_BLOCKED' AND retryable)
 OR (retry_after_seconds IS NOT NULL AND retry_after_seconds NOT BETWEEN 0 AND 86400) THEN
 RAISE EXCEPTION 'Safe failure category and outcome classification required' USING ERRCODE='23514'; END IF;
 SELECT workflow_run_id,media_run_id INTO wid,runid FROM public.media_jobs WHERE tenant_id=public.context_tenant() AND id=jobid;
 IF wid IS NULL THEN RAISE EXCEPTION 'Media job not found' USING ERRCODE='P0002'; END IF;
 PERFORM 1 FROM public.workflow_runs WHERE tenant_id=public.context_tenant() AND id=wid FOR UPDATE;
 SELECT * INTO r FROM public.media_runs WHERE tenant_id=public.context_tenant() AND id=runid FOR UPDATE;
 SELECT * INTO j FROM public.media_jobs WHERE tenant_id=public.context_tenant() AND id=jobid FOR UPDATE;
 IF j.status<>'RUNNING' OR r.status IN ('AWAITING_APPROVAL','APPROVED','REJECTED','BLOCKED','FAILED','UNKNOWN_OUTCOME') THEN
 RAISE EXCEPTION 'Only an active job may fail' USING ERRCODE='23514'; END IF;
 safe_retry:=retryable AND NOT unknown AND j.attempt<CASE WHEN j.stage='AVATAR_POLL' THEN 90 ELSE 3 END;
 nextstatus:=CASE WHEN unknown THEN 'UNKNOWN_OUTCOME' WHEN category='POLICY_BLOCKED' THEN 'BLOCKED'
 WHEN safe_retry THEN r.status ELSE 'FAILED' END;
 IF safe_retry THEN retry_time:=finished+make_interval(secs=>greatest(coalesce(retry_after_seconds,0),least(60,power(2,least(j.attempt-1,6))::int))); END IF;
 UPDATE public.media_jobs SET status=CASE WHEN unknown THEN 'UNKNOWN_OUTCOME' ELSE 'FAILED' END,
 retryable=safe_retry,retry_at=retry_time,error_category=category,ended_at=finished WHERE tenant_id=j.tenant_id AND id=j.id;
 UPDATE public.skill_runs SET status='FAILED',ended_at=finished,latency_ms=greatest(0,extract(epoch FROM finished-started_at)*1000),
 retryable=safe_retry,retry_at=retry_time,error_category=category WHERE tenant_id=j.tenant_id AND id=j.skill_run_id;
 UPDATE public.media_runs SET status=nextstatus,error_category=category,updated_at=finished,
 ended_at=CASE WHEN nextstatus IN ('BLOCKED','FAILED','UNKNOWN_OUTCOME') THEN finished ELSE NULL END
 WHERE tenant_id=r.tenant_id AND id=r.id RETURNING * INTO r;
 PERFORM private.audit_media(r,'MEDIA_JOB_FAILED',jsonb_build_object('media_job_id',j.id,'skill_run_id',j.skill_run_id,
 'stage',j.stage,'error_category',category,'retryable',safe_retry,'unknown_outcome',unknown));
 END $$;

CREATE FUNCTION public.approve_media_run(runid uuid,manifesthash text,decision text,checks jsonb,comment text DEFAULT NULL) RETURNS uuid
 LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,public AS $$
 DECLARE r public.media_runs; wid uuid; result uuid;
 BEGIN
 IF NOT public.has_role('APPROVER') THEN RAISE EXCEPTION 'Approver required' USING ERRCODE='42501'; END IF;
 SELECT workflow_run_id INTO wid FROM public.media_runs WHERE tenant_id=public.context_tenant() AND id=runid;
 IF wid IS NULL THEN RAISE EXCEPTION 'Media run not found' USING ERRCODE='P0002'; END IF;
 PERFORM 1 FROM public.workflow_runs WHERE tenant_id=public.context_tenant() AND id=wid FOR UPDATE;
 SELECT * INTO r FROM public.media_runs WHERE tenant_id=public.context_tenant() AND id=runid FOR UPDATE;
 IF decision IS NULL OR decision NOT IN ('APPROVE','REJECT') OR r.status<>'AWAITING_APPROVAL'
 OR manifesthash IS DISTINCT FROM r.manifest_hash OR r.manifest_hash IS NULL
 OR r.qa_result IS DISTINCT FROM jsonb_build_object('schema_version',1,'status','PASS','findings','[]'::jsonb)
 OR length(comment)>2000 OR NOT private.media_keys(checks,ARRAY['identity','voice','lip_sync','captions','disclosure'])
 OR EXISTS(SELECT 1 FROM jsonb_each(checks) kv WHERE jsonb_typeof(kv.value)<>'boolean')
 OR (decision='APPROVE' AND checks IS DISTINCT FROM '{"identity":true,"voice":true,"lip_sync":true,"captions":true,"disclosure":true}'::jsonb)
 OR EXISTS(SELECT 1 FROM public.media_runs newer WHERE newer.tenant_id=r.tenant_id AND newer.workflow_run_id=r.workflow_run_id AND newer.sequence>r.sequence)
 OR EXISTS(SELECT 1 FROM public.media_approval_records WHERE tenant_id=r.tenant_id AND media_run_id=r.id) THEN
 RAISE EXCEPTION 'Exact current media QA and explicit human checks are required' USING ERRCODE='23514'; END IF;
 PERFORM private.check_media_current(r);
 INSERT INTO public.media_approval_records(tenant_id,media_run_id,workflow_run_id,asset_version_id,research_version_id,qa_report_id,
 content_approval_record_id,manifest_hash,approver_id,decision,human_checks,comment)
 VALUES(r.tenant_id,r.id,r.workflow_run_id,r.asset_version_id,r.research_version_id,r.qa_report_id,r.content_approval_record_id,
 r.manifest_hash,public.context_principal(),decision,checks,comment) RETURNING id INTO result;
 UPDATE public.media_runs SET status=CASE WHEN decision='APPROVE' THEN 'APPROVED' ELSE 'REJECTED' END,
 updated_at=clock_timestamp() WHERE tenant_id=r.tenant_id AND id=r.id RETURNING * INTO r;
 PERFORM private.audit_media(r,'MEDIA_HUMAN_DECISION',jsonb_build_object('media_approval_record_id',result,'decision',decision,'manifest_hash',r.manifest_hash));
 RETURN result; END $$;

CREATE FUNCTION public.check_media_access(runid uuid,require_approved boolean DEFAULT false) RETURNS void
 LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,public AS $$
 DECLARE r public.media_runs; wid uuid;
 BEGIN
 IF public.context_principal() IS NULL OR require_approved IS NULL THEN
 RAISE EXCEPTION 'Authenticated media access required' USING ERRCODE='42501'; END IF;
 SELECT workflow_run_id INTO wid FROM public.media_runs WHERE tenant_id=public.context_tenant() AND id=runid;
 IF wid IS NULL THEN RAISE EXCEPTION 'Media run not found' USING ERRCODE='P0002'; END IF;
 PERFORM 1 FROM public.workflow_runs WHERE tenant_id=public.context_tenant() AND id=wid FOR UPDATE;
 SELECT * INTO r FROM public.media_runs WHERE tenant_id=public.context_tenant() AND id=runid FOR UPDATE;
 IF r.status NOT IN ('AWAITING_APPROVAL','APPROVED') OR r.manifest_hash IS NULL
 OR r.manifest_hash IS DISTINCT FROM private.visual_hash(r.manifest)
 OR r.qa_result IS DISTINCT FROM jsonb_build_object('schema_version',1,'status','PASS','findings','[]'::jsonb)
 OR EXISTS(SELECT 1 FROM public.media_runs newer WHERE newer.tenant_id=r.tenant_id AND newer.workflow_run_id=r.workflow_run_id AND newer.sequence>r.sequence)
 OR (require_approved AND (r.status<>'APPROVED' OR NOT EXISTS(SELECT 1 FROM public.media_approval_records a
 WHERE a.tenant_id=r.tenant_id AND a.media_run_id=r.id AND a.manifest_hash=r.manifest_hash AND a.decision='APPROVE'))) THEN
 RAISE EXCEPTION 'Current passing media and exact required approval are unavailable' USING ERRCODE='23514'; END IF;
 PERFORM private.check_media_current(r);
 END $$;

REVOKE ALL ON FUNCTION private.guard_media_history(),private.media_keys(jsonb,text[]),private.media_decimal(jsonb,text,numeric),
 private.media_utc(jsonb,text),private.audit_media(public.media_runs,text,jsonb),private.media_script(jsonb,jsonb),
 private.check_media_current(public.media_runs),private.media_success(uuid,text),private.media_digest(jsonb,text),
 private.media_integer(jsonb,text,bigint,bigint),private.media_duration(jsonb,text),private.media_identifier(jsonb,text)
 FROM PUBLIC,mediaos_runtime;
REVOKE ALL ON FUNCTION public.create_media_profile(uuid,uuid,jsonb),public.set_media_spend_policy(jsonb),
 public.start_media_run(uuid,uuid,jsonb,text,text),public.reserve_media_job(uuid,text,jsonb),public.finish_media_job(uuid,jsonb),
 public.fail_media_job(uuid,text,boolean,boolean,integer),public.approve_media_run(uuid,text,text,jsonb,text),
 public.check_media_access(uuid,boolean) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION public.create_media_profile(uuid,uuid,jsonb),public.set_media_spend_policy(jsonb),
 public.start_media_run(uuid,uuid,jsonb,text,text),public.reserve_media_job(uuid,text,jsonb),public.finish_media_job(uuid,jsonb),
 public.fail_media_job(uuid,text,boolean,boolean,integer),public.approve_media_run(uuid,text,text,jsonb,text),
 public.check_media_access(uuid,boolean) TO mediaos_runtime;
