-- Descriptive historical platform observations; no publishing or strategy authority.
ALTER TABLE social_insight_snapshots ADD UNIQUE(tenant_id,id,publish_run_id,workflow_run_id);
CREATE TABLE social_learning_reports (
 id uuid PRIMARY KEY DEFAULT gen_random_uuid(), tenant_id uuid NOT NULL REFERENCES tenants,
 publish_run_id uuid NOT NULL, workflow_run_id uuid NOT NULL,
 baseline_snapshot_id uuid NOT NULL, current_snapshot_id uuid NOT NULL,
 provenance text NOT NULL DEFAULT 'PLATFORM' CHECK(provenance='PLATFORM'),
 algorithm_version text NOT NULL DEFAULT 'platform-descriptive-v1' CHECK(algorithm_version='platform-descriptive-v1'),
 status text NOT NULL CHECK(status IN ('DESCRIPTIVE','INSUFFICIENT_DATA')),
 payload jsonb NOT NULL, content_hash text NOT NULL CHECK(content_hash=private.visual_hash(payload)),
 input_hash text NOT NULL CHECK(input_hash ~ '^[0-9a-f]{64}$'),
 idempotency_key text NOT NULL CHECK(length(idempotency_key) BETWEEN 1 AND 128),
 skill_run_id uuid NOT NULL, created_by uuid NOT NULL, correlation_id uuid NOT NULL,
 created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
 UNIQUE(tenant_id,id), UNIQUE(tenant_id,idempotency_key),
 FOREIGN KEY(tenant_id,publish_run_id,workflow_run_id) REFERENCES social_publish_runs(tenant_id,id,workflow_run_id),
 FOREIGN KEY(tenant_id,baseline_snapshot_id,publish_run_id,workflow_run_id) REFERENCES social_insight_snapshots(tenant_id,id,publish_run_id,workflow_run_id),
 FOREIGN KEY(tenant_id,current_snapshot_id,publish_run_id,workflow_run_id) REFERENCES social_insight_snapshots(tenant_id,id,publish_run_id,workflow_run_id),
 FOREIGN KEY(tenant_id,skill_run_id,workflow_run_id) REFERENCES skill_runs(tenant_id,id,workflow_run_id),
 FOREIGN KEY(tenant_id,created_by) REFERENCES tenant_memberships(tenant_id,principal_id),
 CHECK(baseline_snapshot_id<>current_snapshot_id),
 CHECK(jsonb_typeof(payload)='object' AND octet_length(payload::text)<=262144),
 CHECK(payload->'provenance' IS NOT DISTINCT FROM '"PLATFORM"'::jsonb),
 CHECK(payload->'causal_claim' IS NOT DISTINCT FROM 'false'::jsonb),
 CHECK(payload->'policy_updated' IS NOT DISTINCT FROM 'false'::jsonb),
 CHECK(payload->'network_performed' IS NOT DISTINCT FROM 'false'::jsonb)
);
CREATE INDEX social_learning_history ON social_learning_reports(tenant_id,publish_run_id,created_at);
ALTER TABLE social_learning_reports ENABLE ROW LEVEL SECURITY;
ALTER TABLE social_learning_reports FORCE ROW LEVEL SECURITY;
CREATE POLICY tenant_boundary ON social_learning_reports USING(tenant_id=public.context_tenant()) WITH CHECK(tenant_id=public.context_tenant());
CREATE TRIGGER immutable BEFORE UPDATE OR DELETE ON social_learning_reports FOR EACH ROW EXECUTE FUNCTION immutable_record();
REVOKE ALL ON social_learning_reports FROM PUBLIC,mediaos_runtime;
GRANT SELECT ON social_learning_reports TO mediaos_runtime;

CREATE FUNCTION private.checked_social_observation(s public.social_insight_snapshots,r public.social_publish_runs,c public.social_connections)
 RETURNS void LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,public AS $$
 DECLARE name text; value jsonb; n int; BEGIN
 IF NOT private.media_keys(s.payload,ARRAY['schema_version','media_id','captured_at','api_version','raw','raw_hash','metrics','definitions'])
 OR s.payload->'schema_version' IS DISTINCT FROM '1'::jsonb OR jsonb_typeof(s.payload->'raw') IS DISTINCT FROM 'object'
 OR s.tenant_id IS DISTINCT FROM r.tenant_id OR s.publish_run_id IS DISTINCT FROM r.id OR s.workflow_run_id IS DISTINCT FROM r.workflow_run_id
 OR s.payload->>'media_id' IS DISTINCT FROM r.post_id OR s.payload->>'api_version' IS DISTINCT FROM c.api_version
 OR s.content_hash IS DISTINCT FROM private.visual_hash(s.payload)
 OR s.payload->>'raw_hash' IS DISTINCT FROM private.visual_hash(s.payload->'raw')
 OR private.media_utc(s.payload,'captured_at') IS DISTINCT FROM s.captured_at
 OR jsonb_typeof(s.payload->'metrics') IS DISTINCT FROM 'object' OR jsonb_typeof(s.payload->'definitions') IS DISTINCT FROM 'object'
 OR NOT EXISTS(SELECT 1 FROM public.social_publish_jobs j JOIN public.skill_runs k ON k.tenant_id=j.tenant_id AND k.id=j.skill_run_id
 WHERE j.tenant_id=r.tenant_id AND j.id=s.job_id AND j.publish_run_id=r.id AND j.workflow_run_id=r.workflow_run_id
 AND j.stage='INSIGHTS' AND j.status='SUCCEEDED' AND j.result=s.payload AND k.status='SUCCEEDED' AND k.provider='instagram'
 AND k.skill_identifier='social.insights' AND k.workflow_run_id=r.workflow_run_id) THEN
 RAISE EXCEPTION 'Exact connector-observed snapshot required' USING ERRCODE='23514'; END IF;
 SELECT count(*) INTO n FROM jsonb_object_keys(s.payload->'metrics');
 IF n NOT BETWEEN 1 AND 50 OR
 (SELECT array_agg(key ORDER BY key) FROM jsonb_object_keys(s.payload->'metrics') AS keys(key)) IS DISTINCT FROM
 (SELECT array_agg(key ORDER BY key) FROM jsonb_object_keys(s.payload->'definitions') AS keys(key)) THEN
 RAISE EXCEPTION 'Exact bounded metric definitions required' USING ERRCODE='23514'; END IF;
 FOR name,value IN SELECT item.key,item.value FROM jsonb_each(s.payload->'metrics') AS item LOOP
 IF name !~ '^[a-z][a-z0-9_]{0,63}$'
 OR (value<>'null'::jsonb AND (jsonb_typeof(value)<>'number' OR value::text !~ '^(0|[1-9][0-9]*)$')) THEN
 RAISE EXCEPTION 'Counter must be a nonnegative integer or unknown' USING ERRCODE='23514'; END IF;
 IF value<>'null'::jsonb AND value::numeric>9007199254740991 THEN
 RAISE EXCEPTION 'Counter exceeds exact supported range' USING ERRCODE='23514'; END IF;
 IF jsonb_typeof(s.payload->'definitions'->name) IS DISTINCT FROM 'string'
 OR length(btrim(s.payload->'definitions'->>name)) NOT BETWEEN 1 AND 2000 THEN
 RAISE EXCEPTION 'Nonblank metric definition required' USING ERRCODE='23514'; END IF;
 END LOOP;
 END $$;

CREATE FUNCTION public.build_social_learning(pid uuid,baselineid uuid,currentid uuid,key text,inputhash text) RETURNS uuid
 LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,public AS $$
 DECLARE r public.social_publish_runs; c public.social_connections; b public.social_insight_snapshots; a public.social_insight_snapshots;
 report public.social_learning_reports; rid uuid:=gen_random_uuid(); sid uuid; started timestamptz; finished timestamptz;
 name text; old_value bigint; new_value bigint; delta bigint; direction text; comparisons jsonb:='{}'::jsonb;
 result jsonb; result_status text; comparable int:=0;
 BEGIN
 PERFORM private.metrics_request(key,inputhash,jsonb_build_object('publish_run_id',pid,'baseline_snapshot_id',baselineid,
 'current_snapshot_id',currentid,'algorithm_version','platform-descriptive-v1'));
 SELECT * INTO r FROM public.social_publish_runs WHERE tenant_id=public.context_tenant() AND id=pid;
 IF r.id IS NULL THEN RAISE EXCEPTION 'Publish run not found' USING ERRCODE='P0002'; END IF;
 PERFORM pg_advisory_xact_lock(hashtextextended('social-learning:'||r.tenant_id::text||':'||key,0));
 SELECT * INTO report FROM public.social_learning_reports WHERE tenant_id=r.tenant_id AND idempotency_key=key;
 IF report.id IS NOT NULL THEN
 IF report.input_hash<>inputhash OR report.publish_run_id<>pid THEN RAISE EXCEPTION 'Learning idempotency conflict' USING ERRCODE='23505'; END IF;
 RETURN report.id; END IF;
 SELECT * INTO c FROM public.social_connections WHERE tenant_id=r.tenant_id AND id=r.connection_id;
 SELECT * INTO b FROM public.social_insight_snapshots WHERE tenant_id=r.tenant_id AND id=baselineid AND publish_run_id=r.id;
 SELECT * INTO a FROM public.social_insight_snapshots WHERE tenant_id=r.tenant_id AND id=currentid AND publish_run_id=r.id;
 IF b.id IS NULL OR a.id IS NULL THEN RAISE EXCEPTION 'Owned snapshots of the same post required' USING ERRCODE='P0002'; END IF;
 IF r.status<>'PUBLISHED' OR c.id IS NULL OR c.account_id IS DISTINCT FROM r.account_id OR baselineid=currentid OR b.captured_at>=a.captured_at THEN
 RAISE EXCEPTION 'Published post and strictly ordered snapshots required' USING ERRCODE='23514'; END IF;
 PERFORM private.checked_social_observation(b,r,c); PERFORM private.checked_social_observation(a,r,c);
 IF b.payload->'definitions' IS DISTINCT FROM a.payload->'definitions' THEN
 RAISE EXCEPTION 'Comparable metric definitions and API version required' USING ERRCODE='23514'; END IF;
 started:=clock_timestamp();
 INSERT INTO public.skill_runs(tenant_id,workflow_run_id,skill_identifier,skill_version,input_schema_version,output_schema_version,
 provider,model,adapter,attempt,step_key,input_hash,started_at,status,input_tokens,output_tokens,cost,is_mock)
 VALUES(r.tenant_id,r.workflow_run_id,'learning.platform_describe','1.0.0',1,1,'deterministic','none','platform-descriptive-v1',1,
 'learning.platform_describe:'||rid::text,inputhash,started,'RUNNING',0,0,0,false) RETURNING id INTO sid;
 FOR name IN SELECT jsonb_object_keys(b.payload->'metrics') ORDER BY 1 LOOP
 old_value:=(b.payload->'metrics'->>name)::bigint; new_value:=(a.payload->'metrics'->>name)::bigint;
 delta:=NULL; direction:='UNKNOWN';
 IF old_value IS NOT NULL AND new_value IS NOT NULL THEN
 delta:=new_value-old_value; comparable:=comparable+1;
 direction:=CASE WHEN delta>0 THEN 'INCREASED' WHEN delta<0 THEN 'DECREASED' ELSE 'UNCHANGED' END;
 END IF;
 comparisons:=comparisons||jsonb_build_object(name,jsonb_build_object('baseline',old_value,'current',new_value,'delta',delta,'direction',direction));
 END LOOP;
 result_status:=CASE WHEN comparable=0 THEN 'INSUFFICIENT_DATA' ELSE 'DESCRIPTIVE' END;
 result:=jsonb_build_object('schema_version',1,'algorithm_version','platform-descriptive-v1','provenance','PLATFORM','scope','LIFETIME_CUMULATIVE',
 'publish_run_id',r.id,'workflow_run_id',r.workflow_run_id,'connection_id',c.id,'account_id',c.account_id,'media_id',r.post_id,'api_version',c.api_version,
 'baseline_snapshot_id',b.id,'current_snapshot_id',a.id,'baseline_snapshot_hash',b.content_hash,'current_snapshot_hash',a.content_hash,
 'baseline_observed_at',b.payload->>'captured_at','current_observed_at',a.payload->>'captured_at','definitions',b.payload->'definitions',
 'metrics',comparisons,'status',result_status,'limitations',jsonb_build_array('PLATFORM_OBSERVATION','DESCRIPTIVE_ONLY','NO_CAUSAL_INFERENCE'),
 'causal_claim',false,'policy_updated',false,'network_performed',false);
 INSERT INTO public.social_learning_reports(id,tenant_id,publish_run_id,workflow_run_id,baseline_snapshot_id,current_snapshot_id,status,payload,
 content_hash,input_hash,idempotency_key,skill_run_id,created_by,correlation_id)
 VALUES(rid,r.tenant_id,r.id,r.workflow_run_id,b.id,a.id,result_status,result,private.visual_hash(result),inputhash,key,sid,public.context_principal(),r.correlation_id)
 RETURNING * INTO report;
 finished:=clock_timestamp();
 UPDATE public.skill_runs SET status='SUCCEEDED',ended_at=finished,latency_ms=greatest(0,extract(epoch FROM finished-started)*1000),output=result
 WHERE tenant_id=r.tenant_id AND id=sid;
 INSERT INTO public.cost_events(tenant_id,workflow_run_id,skill_run_id,provider,model,input_tokens,output_tokens,cost,currency,price_version)
 VALUES(r.tenant_id,r.workflow_run_id,sid,'deterministic','none',0,0,0,'USD','deterministic-zero-v1');
 PERFORM private.social_audit(r,'SOCIAL_LEARNING_CREATED',jsonb_build_object('learning_report_id',rid,'skill_run_id',sid,
 'baseline_snapshot_id',b.id,'current_snapshot_id',a.id,'content_hash',report.content_hash,'status',result_status,'policy_updated',false,'causal_claim',false));
 RETURN rid; END $$;

REVOKE ALL ON FUNCTION private.checked_social_observation(public.social_insight_snapshots,public.social_publish_runs,public.social_connections) FROM PUBLIC,mediaos_runtime;
REVOKE ALL ON FUNCTION public.build_social_learning(uuid,uuid,uuid,text,text) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION public.build_social_learning(uuid,uuid,uuid,text,text) TO mediaos_runtime;
