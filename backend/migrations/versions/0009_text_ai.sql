-- Only the creator's fact/template selection is model-based. QA stays deterministic.
ALTER TABLE skill_runs DROP CONSTRAINT skill_runs_provider_check;
ALTER TABLE skill_runs ADD CHECK(provider IN ('mock','deterministic','elevenlabs','heygen','openai'));
ALTER TABLE cost_events DROP CONSTRAINT cost_events_provider_check;
ALTER TABLE cost_events ADD CHECK(provider IN ('mock','deterministic','elevenlabs','heygen','openai'));
ALTER TABLE skill_runs DROP CONSTRAINT skill_runs_check1;
ALTER TABLE skill_runs ADD CONSTRAINT skill_runs_cost_check CHECK((provider IN ('mock','deterministic') AND cost IS NOT NULL AND cost=0)
 OR (provider IN ('elevenlabs','heygen','openai') AND (cost IS NULL OR cost>=0)));
ALTER TABLE cost_events DROP CONSTRAINT cost_events_check;
ALTER TABLE cost_events ADD CONSTRAINT cost_events_cost_check CHECK((provider IN ('mock','deterministic') AND cost IS NOT NULL AND cost=0)
 OR (provider IN ('elevenlabs','heygen','openai') AND (cost IS NULL OR cost>=0)));
ALTER TABLE skill_runs ALTER COLUMN input_tokens DROP NOT NULL;
ALTER TABLE skill_runs ALTER COLUMN output_tokens DROP NOT NULL;
ALTER TABLE skill_runs ADD CHECK(provider='openai' OR (input_tokens IS NOT NULL AND output_tokens IS NOT NULL));
ALTER TABLE cost_events ALTER COLUMN input_tokens DROP NOT NULL;
ALTER TABLE cost_events ALTER COLUMN output_tokens DROP NOT NULL;
ALTER TABLE cost_events DROP CONSTRAINT cost_events_input_tokens_check;
ALTER TABLE cost_events DROP CONSTRAINT cost_events_output_tokens_check;
ALTER TABLE cost_events ADD CHECK((provider='openai' AND (input_tokens IS NULL OR input_tokens>=0)) OR (provider<>'openai' AND input_tokens IS NOT NULL AND input_tokens=0));
ALTER TABLE cost_events ADD CHECK((provider='openai' AND (output_tokens IS NULL OR output_tokens>=0)) OR (provider<>'openai' AND output_tokens IS NOT NULL AND output_tokens=0));

CREATE TABLE text_ai_policies (
 id uuid PRIMARY KEY DEFAULT gen_random_uuid(), tenant_id uuid NOT NULL REFERENCES tenants,
 version int NOT NULL CHECK(version>0), payload jsonb NOT NULL,
 content_hash text NOT NULL CHECK(content_hash=private.visual_hash(payload)), created_by uuid NOT NULL,
 created_at timestamptz NOT NULL DEFAULT clock_timestamp(), UNIQUE(tenant_id,id), UNIQUE(tenant_id,version),
 FOREIGN KEY(tenant_id,created_by) REFERENCES tenant_memberships(tenant_id,principal_id)
);
CREATE TABLE text_ai_attempts (
 id uuid PRIMARY KEY DEFAULT gen_random_uuid(), tenant_id uuid NOT NULL REFERENCES tenants,
 workflow_run_id uuid NOT NULL, skill_run_id uuid NOT NULL, policy_id uuid NOT NULL,
 research_version_id uuid NOT NULL, brief_id uuid NOT NULL, influencer_version_id uuid NOT NULL,
 attempt int NOT NULL CHECK(attempt BETWEEN 1 AND 3), context jsonb NOT NULL, context_hash text NOT NULL,
 request jsonb NOT NULL, request_hash text NOT NULL CHECK(request_hash=private.visual_hash(request)),
 reserved_input_tokens int NOT NULL CHECK(reserved_input_tokens>0), reserved_output_tokens int NOT NULL CHECK(reserved_output_tokens>0),
 expected_max_cost numeric NOT NULL CHECK(expected_max_cost>0),
 status text NOT NULL CHECK(status IN ('RUNNING','SUCCEEDED','FAILED','UNKNOWN_OUTCOME')),
 retryable boolean NOT NULL DEFAULT false, retry_at timestamptz, error_category text,
 result jsonb, execution jsonb, asset_version_id uuid,
 started_at timestamptz NOT NULL DEFAULT clock_timestamp(), ended_at timestamptz,
 UNIQUE(tenant_id,id), UNIQUE(tenant_id,workflow_run_id,attempt), UNIQUE(tenant_id,skill_run_id),
 FOREIGN KEY(tenant_id,workflow_run_id) REFERENCES workflow_runs(tenant_id,id),
 FOREIGN KEY(tenant_id,skill_run_id,workflow_run_id) REFERENCES skill_runs(tenant_id,id,workflow_run_id),
 FOREIGN KEY(tenant_id,policy_id) REFERENCES text_ai_policies(tenant_id,id),
 FOREIGN KEY(tenant_id,research_version_id,workflow_run_id) REFERENCES research_pack_versions(tenant_id,id,workflow_run_id),
 FOREIGN KEY(tenant_id,brief_id,workflow_run_id) REFERENCES content_briefs(tenant_id,id,workflow_run_id),
 FOREIGN KEY(tenant_id,influencer_version_id) REFERENCES influencer_versions(tenant_id,id),
 FOREIGN KEY(tenant_id,asset_version_id,workflow_run_id) REFERENCES content_asset_versions(tenant_id,id,workflow_run_id),
 CHECK(context_hash=private.visual_hash(context)),
 CHECK((status='RUNNING')=(ended_at IS NULL)), CHECK((status='SUCCEEDED')=(result IS NOT NULL)),
 CHECK((status='SUCCEEDED')=(asset_version_id IS NOT NULL)),
 CHECK(error_category IS NULL OR error_category ~ '^[A-Z][A-Z0-9_]{0,79}$')
);
CREATE UNIQUE INDEX text_ai_one_running ON text_ai_attempts(tenant_id,workflow_run_id) WHERE status='RUNNING';
CREATE INDEX text_ai_daily ON text_ai_attempts(tenant_id,started_at);
CREATE TRIGGER immutable BEFORE UPDATE OR DELETE ON text_ai_policies FOR EACH ROW EXECUTE FUNCTION immutable_record();
DO $$ DECLARE t text; BEGIN
 FOREACH t IN ARRAY ARRAY['text_ai_policies','text_ai_attempts'] LOOP
 EXECUTE format('ALTER TABLE %I ENABLE ROW LEVEL SECURITY',t);
 EXECUTE format('ALTER TABLE %I FORCE ROW LEVEL SECURITY',t);
 EXECUTE format('CREATE POLICY tenant_boundary ON %I USING(tenant_id=public.context_tenant()) WITH CHECK(tenant_id=public.context_tenant())',t);
 EXECUTE format('REVOKE ALL ON %I FROM PUBLIC,mediaos_runtime',t);
 EXECUTE format('GRANT SELECT ON %I TO mediaos_runtime',t);
 END LOOP;
END $$;

CREATE FUNCTION private.guard_text_ai_history() RETURNS trigger LANGUAGE plpgsql SET search_path=pg_catalog,public AS $$
 BEGIN
 IF TG_OP='DELETE' OR OLD.status<>'RUNNING' OR
 (to_jsonb(OLD)-ARRAY['status','retryable','retry_at','error_category','result','execution','asset_version_id','ended_at'])
 IS DISTINCT FROM (to_jsonb(NEW)-ARRAY['status','retryable','retry_at','error_category','result','execution','asset_version_id','ended_at']) THEN
 RAISE EXCEPTION 'Model attempt history is immutable' USING ERRCODE='23514'; END IF;
 RETURN NEW; END $$;
CREATE TRIGGER text_ai_history BEFORE UPDATE OR DELETE ON text_ai_attempts FOR EACH ROW EXECUTE FUNCTION private.guard_text_ai_history();
CREATE FUNCTION private.guard_openai_write() RETURNS trigger LANGUAGE plpgsql SET search_path=pg_catalog,public AS $$
 BEGIN
 IF current_user='mediaos_runtime' AND (NEW.provider='openai' OR (TG_OP='UPDATE' AND OLD.provider='openai')) THEN
 RAISE EXCEPTION 'Model telemetry requires a guarded reservation' USING ERRCODE='42501'; END IF;
 RETURN NEW; END $$;
CREATE TRIGGER openai_guard BEFORE INSERT OR UPDATE ON skill_runs FOR EACH ROW EXECUTE FUNCTION private.guard_openai_write();
CREATE TRIGGER openai_guard BEFORE INSERT OR UPDATE ON cost_events FOR EACH ROW EXECUTE FUNCTION private.guard_openai_write();
DROP TRIGGER immutable ON cost_events;
CREATE FUNCTION private.guard_text_ai_cost() RETURNS trigger LANGUAGE plpgsql SET search_path=pg_catalog,public AS $$
 BEGIN
 IF TG_OP='DELETE' OR current_user='mediaos_runtime' OR OLD.provider<>'openai'
 OR OLD.input_tokens IS NOT NULL OR OLD.output_tokens IS NOT NULL
 OR (to_jsonb(OLD)-ARRAY['input_tokens','output_tokens']) IS DISTINCT FROM (to_jsonb(NEW)-ARRAY['input_tokens','output_tokens']) THEN
 RAISE EXCEPTION 'Cost evidence is immutable' USING ERRCODE='23514'; END IF;
 RETURN NEW; END $$;
CREATE TRIGGER immutable BEFORE UPDATE OR DELETE ON cost_events FOR EACH ROW EXECUTE FUNCTION private.guard_text_ai_cost();

CREATE FUNCTION public.set_text_ai_policy(p jsonb) RETURNS uuid LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,public AS $$
 DECLARE result uuid; n int; checked timestamptz; expiry timestamptz;
 BEGIN
 IF NOT public.has_role('ADMIN') THEN RAISE EXCEPTION 'Administrator required' USING ERRCODE='42501'; END IF;
 IF NOT private.media_keys(p,ARRAY['schema_version','enabled','model','max_input_bytes','max_output_tokens','max_calls_per_run','max_calls_per_day',
 'input_usd_per_million_tokens','output_usd_per_million_tokens','per_run_usd','per_day_usd','price_reference','price_checked_at','expires_at'])
 OR p->'schema_version' IS DISTINCT FROM '1'::jsonb OR jsonb_typeof(p->'enabled') IS DISTINCT FROM 'boolean'
 OR jsonb_typeof(p->'model') IS DISTINCT FROM 'string' OR p->>'model' !~ '^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$'
 OR NOT private.media_integer(p,'max_input_bytes',4096,131072) OR NOT private.media_integer(p,'max_output_tokens',256,8192)
 OR NOT private.media_integer(p,'max_calls_per_run',1,3) OR NOT private.media_integer(p,'max_calls_per_day',1,1000)
 OR jsonb_typeof(p->'price_reference') IS DISTINCT FROM 'string' OR length(btrim(p->>'price_reference')) NOT BETWEEN 1 AND 1000 THEN
 RAISE EXCEPTION 'Invalid text model policy' USING ERRCODE='23514'; END IF;
 PERFORM private.media_decimal(p,'input_usd_per_million_tokens',1000); PERFORM private.media_decimal(p,'output_usd_per_million_tokens',1000);
 PERFORM private.media_decimal(p,'per_run_usd',1000); PERFORM private.media_decimal(p,'per_day_usd',10000);
 checked:=private.media_utc(p,'price_checked_at'); expiry:=private.media_utc(p,'expires_at');
 IF checked>clock_timestamp() OR expiry<=checked OR expiry>checked+interval '30 days'
 OR ((p->>'enabled')::boolean AND expiry<=clock_timestamp()) THEN
 RAISE EXCEPTION 'Current dated prices and bounded policy expiry required' USING ERRCODE='23514'; END IF;
 PERFORM pg_advisory_xact_lock(hashtextextended('text-ai-budget:'||public.context_tenant()::text,0));
 SELECT coalesce(max(version),0)+1 INTO n FROM public.text_ai_policies WHERE tenant_id=public.context_tenant();
 INSERT INTO public.text_ai_policies(tenant_id,version,payload,content_hash,created_by)
 VALUES(public.context_tenant(),n,p,private.visual_hash(p),public.context_principal()) RETURNING id INTO result;
 RETURN result; END $$;

CREATE FUNCTION private.text_ai_context(wid uuid) RETURNS jsonb LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,public AS $$
 DECLARE w public.workflow_runs; rp public.research_pack_versions; b public.content_briefs; iv public.influencer_versions; sources jsonb;
 BEGIN
 SELECT * INTO w FROM public.workflow_runs WHERE tenant_id=public.context_tenant() AND id=wid FOR UPDATE;
 IF w.id IS NULL THEN RAISE EXCEPTION 'Workflow not found' USING ERRCODE='P0002'; END IF;
 SELECT * INTO rp FROM public.research_pack_versions WHERE tenant_id=w.tenant_id AND id=w.research_version_id;
 SELECT * INTO b FROM public.content_briefs WHERE tenant_id=w.tenant_id AND id=w.brief_id;
 SELECT * INTO iv FROM public.influencer_versions WHERE tenant_id=w.tenant_id AND id=w.influencer_version_id;
 PERFORM 1 FROM public.source_snapshots s JOIN public.research_pack_sources rs ON rs.tenant_id=s.tenant_id AND rs.source_snapshot_id=s.id
 WHERE rs.tenant_id=w.tenant_id AND rs.research_version_id=rp.id ORDER BY s.id FOR UPDATE OF s;
 IF w.state NOT IN ('CONTENT_GENERATING','CONTENT_COMPLETE') OR rp.id IS NULL OR b.id IS NULL
 OR b.research_version_id IS DISTINCT FROM rp.id OR rp.verification_status<>'VERIFIED'
 OR jsonb_array_length(public.intelligence_findings(w.id))>0
 OR EXISTS(SELECT 1 FROM public.research_pack_versions newer WHERE newer.tenant_id=w.tenant_id AND newer.research_pack_id=rp.research_pack_id AND newer.version>rp.version)
 OR NOT EXISTS(SELECT 1 FROM public.brief_facts WHERE tenant_id=w.tenant_id AND brief_id=b.id)
 OR EXISTS(SELECT 1 FROM public.brief_facts bf JOIN public.facts f ON f.tenant_id=bf.tenant_id AND f.id=bf.fact_id
 JOIN public.source_snapshots s ON s.tenant_id=f.tenant_id AND s.id=f.source_snapshot_id
 WHERE bf.tenant_id=w.tenant_id AND bf.brief_id=b.id AND (f.verification_status<>'VERIFIED' OR s.verification_status<>'VERIFIED' OR s.is_fixture
 OR s.checksum<>encode(public.digest(s.raw_content,'sha256'),'hex') OR btrim(substring(s.raw_content FROM f.span_start+1 FOR f.span_end-f.span_start))<>f.statement
 OR (f.fact_type='GRANT' AND (coalesce(f.structured_data->>'deadline','')='' OR coalesce(f.structured_data->>'opening_date','')=''
 OR f.structured_data->>'eligibility_status' IS DISTINCT FROM 'VERIFIED' OR (f.structured_data->>'deadline')::date<timezone('UTC',now())::date)))) THEN
 RAISE EXCEPTION 'Current verified source-backed research and brief required' USING ERRCODE='23514'; END IF;
 SELECT jsonb_agg(jsonb_build_object('id',s.id,'checksum',s.checksum,'verification',s.verification_status,'fixture',s.is_fixture) ORDER BY s.id)
 INTO sources FROM public.source_snapshots s JOIN public.research_pack_sources rs ON rs.tenant_id=s.tenant_id AND rs.source_snapshot_id=s.id
 WHERE rs.tenant_id=w.tenant_id AND rs.research_version_id=rp.id;
 RETURN jsonb_build_object('research_version_id',rp.id,'research_hash',rp.content_hash,'brief_id',b.id,'brief_hash',b.content_hash,
 'influencer_version_id',iv.id,'character_config_version_id',iv.character_config_version_id,'mission_id',w.mission_id,'sources',sources);
 END $$;

CREATE FUNCTION public.reserve_text_ai_attempt(wid uuid,policy uuid,p jsonb,inputhash text) RETURNS uuid LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,public AS $$
 DECLARE w public.workflow_runs; cfg public.text_ai_policies; prev public.text_ai_attempts; ctx jsonb; n int; result uuid; sid uuid;
 input_bound int; output_bound int; expected numeric; run_total numeric; day_total numeric; run_calls int; day_calls int;
 BEGIN
 IF NOT public.has_role('OPERATOR') THEN RAISE EXCEPTION 'Operator required' USING ERRCODE='42501'; END IF;
 ctx:=private.text_ai_context(wid);
 SELECT * INTO w FROM public.workflow_runs WHERE tenant_id=public.context_tenant() AND id=wid;
 IF w.state<>'CONTENT_GENERATING' OR inputhash IS DISTINCT FROM private.visual_hash(p) THEN RAISE EXCEPTION 'Creator checkpoint and exact input required' USING ERRCODE='23514'; END IF;
 PERFORM pg_advisory_xact_lock(hashtextextended('text-ai-budget:'||w.tenant_id::text,0));
 SELECT * INTO cfg FROM public.text_ai_policies WHERE tenant_id=w.tenant_id ORDER BY version DESC LIMIT 1;
 IF cfg.id IS DISTINCT FROM policy OR NOT (cfg.payload->>'enabled')::boolean OR (cfg.payload->>'expires_at')::timestamptz<=clock_timestamp() THEN
 RAISE EXCEPTION 'Current enabled model policy required' USING ERRCODE='23514'; END IF;
 IF NOT private.media_keys(p,ARRAY['model','store','max_output_tokens','input','text']) OR p->'store' IS DISTINCT FROM 'false'::jsonb
 OR p->>'model' IS DISTINCT FROM cfg.payload->>'model' OR p->'max_output_tokens' IS DISTINCT FROM cfg.payload->'max_output_tokens'
 OR octet_length(p::text)>(cfg.payload->>'max_input_bytes')::int OR p->'text'->'format'->'strict' IS DISTINCT FROM 'true'::jsonb
 OR p->'text'->'format'->>'type' IS DISTINCT FROM 'json_schema' THEN RAISE EXCEPTION 'Bounded structured request required' USING ERRCODE='23514'; END IF;
 SELECT * INTO prev FROM public.text_ai_attempts WHERE tenant_id=w.tenant_id AND workflow_run_id=w.id ORDER BY attempt DESC LIMIT 1;
 IF prev.id IS NOT NULL AND (prev.status<>'FAILED' OR NOT prev.retryable OR prev.retry_at>clock_timestamp()
 OR prev.policy_id<>policy OR prev.request_hash<>inputhash OR prev.context_hash<>private.visual_hash(ctx)) THEN
 RAISE EXCEPTION 'Attempt cannot be replayed or inputs changed' USING ERRCODE='23514'; END IF;
 IF EXISTS(SELECT 1 FROM public.skill_runs WHERE tenant_id=w.tenant_id AND workflow_run_id=w.id AND step_key='content.carousel' AND provider<>'openai') THEN
 RAISE EXCEPTION 'An existing creator checkpoint cannot switch providers' USING ERRCODE='23514'; END IF;
 n:=coalesce(prev.attempt,0)+1;
 input_bound:=octet_length(p::text)+4096; output_bound:=(cfg.payload->>'max_output_tokens')::int;
 expected:=ceil((input_bound*(cfg.payload->>'input_usd_per_million_tokens')::numeric
 +output_bound*(cfg.payload->>'output_usd_per_million_tokens')::numeric))/1000000;
 SELECT coalesce(sum(expected_max_cost),0),count(*) INTO run_total,run_calls FROM public.text_ai_attempts WHERE tenant_id=w.tenant_id AND workflow_run_id=w.id;
 SELECT coalesce(sum(expected_max_cost),0),count(*) INTO day_total,day_calls FROM public.text_ai_attempts WHERE tenant_id=w.tenant_id
 AND started_at>=date_trunc('day',clock_timestamp() AT TIME ZONE 'UTC') AT TIME ZONE 'UTC';
 IF n>(cfg.payload->>'max_calls_per_run')::int OR day_calls>=(cfg.payload->>'max_calls_per_day')::int
 OR run_total+expected>(cfg.payload->>'per_run_usd')::numeric OR day_total+expected>(cfg.payload->>'per_day_usd')::numeric THEN
 RAISE EXCEPTION 'Model reservation exceeds explicit budget' USING ERRCODE='23514'; END IF;
 INSERT INTO public.skill_runs(tenant_id,workflow_run_id,step_key,skill_identifier,skill_version,input_schema_version,output_schema_version,
 prompt_version,provider,model,adapter,attempt,input_hash,status,is_mock,input_tokens,output_tokens,cost)
 VALUES(w.tenant_id,w.id,'content.carousel','content.carousel','2.0.0',1,1,'creator-selection-v1','openai',cfg.payload->>'model','openai-responses-selection-v1',n,inputhash,'RUNNING',false,NULL,NULL,NULL) RETURNING id INTO sid;
 INSERT INTO public.cost_events(tenant_id,workflow_run_id,skill_run_id,provider,model,input_tokens,output_tokens,cost,currency,price_version)
 VALUES(w.tenant_id,w.id,sid,'openai',cfg.payload->>'model',NULL,NULL,NULL,'USD','text-ai-policy:'||cfg.id::text);
 INSERT INTO public.text_ai_attempts(tenant_id,workflow_run_id,skill_run_id,policy_id,research_version_id,brief_id,influencer_version_id,
 attempt,context,context_hash,request,request_hash,reserved_input_tokens,reserved_output_tokens,expected_max_cost,status)
 VALUES(w.tenant_id,w.id,sid,cfg.id,w.research_version_id,w.brief_id,w.influencer_version_id,n,ctx,private.visual_hash(ctx),p,inputhash,input_bound,output_bound,expected,'RUNNING') RETURNING id INTO result;
 INSERT INTO public.audit_events(tenant_id,workflow_run_id,actor_id,event_type,correlation_id,details)
 VALUES(w.tenant_id,w.id,public.context_principal(),'TEXT_AI_RESERVED',w.correlation_id,jsonb_build_object('attempt_id',result,'skill_run_id',sid,'attempt',n,'expected_max_cost',expected));
 RETURN result; END $$;

CREATE FUNCTION public.check_text_ai_attempt(aid uuid) RETURNS void LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,public AS $$
 DECLARE a public.text_ai_attempts; ctx jsonb;
 BEGIN
 IF NOT public.has_role('OPERATOR') THEN RAISE EXCEPTION 'Operator required' USING ERRCODE='42501'; END IF;
 SELECT * INTO a FROM public.text_ai_attempts WHERE tenant_id=public.context_tenant() AND id=aid;
 IF a.id IS NULL THEN RAISE EXCEPTION 'Attempt not found' USING ERRCODE='P0002'; END IF;
 ctx:=private.text_ai_context(a.workflow_run_id);
 IF a.status<>'RUNNING' OR a.context_hash<>private.visual_hash(ctx) THEN
 RAISE EXCEPTION 'Current exact model input required' USING ERRCODE='23514'; END IF;
 END $$;

CREATE FUNCTION public.finish_text_ai_attempt(aid uuid,p jsonb,category text DEFAULT NULL,retry boolean DEFAULT false,delay_seconds int DEFAULT NULL) RETURNS void
 LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,public AS $$
 DECLARE a public.text_ai_attempts; w public.workflow_runs; cfg public.text_ai_policies; meta jsonb; inp int; outp int; terminal text;
 can_retry boolean:=false; completed timestamptz:=clock_timestamp(); backoff timestamptz; asset uuid;
 BEGIN
 IF NOT public.has_role('OPERATOR') THEN RAISE EXCEPTION 'Operator required' USING ERRCODE='42501'; END IF;
 SELECT workflow_run_id INTO asset FROM public.text_ai_attempts WHERE tenant_id=public.context_tenant() AND id=aid;
 IF asset IS NULL THEN RAISE EXCEPTION 'Attempt not found' USING ERRCODE='P0002'; END IF;
 SELECT * INTO w FROM public.workflow_runs WHERE tenant_id=public.context_tenant() AND id=asset FOR UPDATE;
 SELECT * INTO a FROM public.text_ai_attempts WHERE tenant_id=w.tenant_id AND id=aid FOR UPDATE;
 SELECT * INTO cfg FROM public.text_ai_policies WHERE tenant_id=w.tenant_id AND id=a.policy_id;
 IF a.status<>'RUNNING' OR retry IS NULL OR (category IS NOT NULL AND category !~ '^[A-Z][A-Z0-9_]{0,79}$') THEN
 RAISE EXCEPTION 'Active attempt and safe result required' USING ERRCODE='23514'; END IF;
 meta:=p->'execution';
 IF meta IS NOT NULL AND meta<>'null'::jsonb THEN
 IF meta->>'provider' IS DISTINCT FROM 'openai' OR meta->>'model' IS DISTINCT FROM cfg.payload->>'model'
 OR meta->'cost' IS DISTINCT FROM 'null'::jsonb OR octet_length(meta::text)>10000 THEN
 RAISE EXCEPTION 'Invalid model execution metadata' USING ERRCODE='23514'; END IF;
 IF meta->'input_tokens'<>'null'::jsonb AND NOT private.media_integer(meta,'input_tokens',0,1000000000) THEN RAISE EXCEPTION 'Invalid usage' USING ERRCODE='23514'; END IF;
 IF meta->'output_tokens'<>'null'::jsonb AND NOT private.media_integer(meta,'output_tokens',0,1000000000) THEN RAISE EXCEPTION 'Invalid usage' USING ERRCODE='23514'; END IF;
 inp:=(meta->>'input_tokens')::int; outp:=(meta->>'output_tokens')::int;
 END IF;
 IF category IS NULL THEN
 PERFORM public.check_text_ai_attempt(aid);
 IF w.state<>'CONTENT_COMPLETE' OR NOT private.media_keys(p,ARRAY['plan','draft','execution'])
 OR p->'draft' IS DISTINCT FROM (SELECT payload FROM public.content_asset_versions WHERE tenant_id=w.tenant_id AND id=w.asset_version_id) THEN
 RAISE EXCEPTION 'Exact persisted creator artifact required' USING ERRCODE='23514'; END IF;
 terminal:='SUCCEEDED'; asset:=w.asset_version_id;
 ELSE
 IF retry AND category<>'RATE_LIMIT' THEN RAISE EXCEPTION 'Only an explicit rate rejection may retry' USING ERRCODE='23514'; END IF;
 terminal:=CASE WHEN category='UNKNOWN_OUTCOME' THEN 'UNKNOWN_OUTCOME' ELSE 'FAILED' END; asset:=NULL;
 can_retry:=retry AND a.attempt<(cfg.payload->>'max_calls_per_run')::int;
 IF can_retry THEN backoff:=completed+make_interval(secs=>greatest(power(2,a.attempt)::int,least(86400,coalesce(delay_seconds,0)))); END IF;
 END IF;
 UPDATE public.text_ai_attempts SET status=terminal,retryable=can_retry,retry_at=backoff,error_category=category,
 result=CASE WHEN terminal='SUCCEEDED' THEN p ELSE NULL END,execution=meta,asset_version_id=asset,ended_at=completed WHERE id=a.id;
 UPDATE public.skill_runs SET status=CASE WHEN terminal='SUCCEEDED' THEN 'SUCCEEDED' ELSE 'FAILED' END,
 ended_at=completed,latency_ms=extract(epoch FROM completed-a.started_at)*1000,error_category=category,retryable=can_retry,retry_at=backoff,
 input_tokens=inp,output_tokens=outp,cost=NULL,output=CASE WHEN terminal='SUCCEEDED' THEN p->'draft' ELSE NULL END,
 research_version_id=a.research_version_id,brief_id=a.brief_id,asset_version_id=asset WHERE tenant_id=a.tenant_id AND id=a.skill_run_id;
 UPDATE public.cost_events SET input_tokens=inp,output_tokens=outp WHERE tenant_id=a.tenant_id AND skill_run_id=a.skill_run_id;
 IF terminal<>'SUCCEEDED' AND NOT can_retry THEN PERFORM public.checkpoint(w.id,'FAILED',NULL); END IF;
 INSERT INTO public.audit_events(tenant_id,workflow_run_id,actor_id,event_type,correlation_id,details)
 VALUES(w.tenant_id,w.id,public.context_principal(),'TEXT_AI_FINISHED',w.correlation_id,
 jsonb_build_object('attempt_id',a.id,'skill_run_id',a.skill_run_id,'attempt',a.attempt,'status',terminal,'error_category',category,'retryable',can_retry));
 END $$;

REVOKE ALL ON FUNCTION private.guard_text_ai_history(),private.guard_openai_write(),private.guard_text_ai_cost(),private.text_ai_context(uuid) FROM PUBLIC;
REVOKE ALL ON FUNCTION set_text_ai_policy(jsonb),reserve_text_ai_attempt(uuid,uuid,jsonb,text),check_text_ai_attempt(uuid),finish_text_ai_attempt(uuid,jsonb,text,boolean,int) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION set_text_ai_policy(jsonb),reserve_text_ai_attempt(uuid,uuid,jsonb,text),check_text_ai_attempt(uuid),finish_text_ai_attempt(uuid,jsonb,text,boolean,int) TO mediaos_runtime;
