-- Cancellation preserves earlier authorization; only never-dispatched plans can
-- be cancelled. A rejection does not authorize stale content or erase history.
ALTER TABLE social_publish_decisions DROP CONSTRAINT social_publish_decisions_tenant_id_publish_run_id_key;
ALTER TABLE social_publish_decisions ADD UNIQUE(tenant_id,publish_run_id,decision);
CREATE OR REPLACE FUNCTION public.social_decide_publish(pid uuid,p jsonb) RETURNS uuid
 LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,public AS $$
 DECLARE r public.social_publish_runs;result uuid; BEGIN
 IF NOT public.has_role('APPROVER') THEN RAISE EXCEPTION 'Approver required' USING ERRCODE='42501'; END IF;
 SELECT * INTO r FROM public.social_publish_runs WHERE tenant_id=public.context_tenant() AND id=pid;
 IF r.id IS NULL THEN RAISE EXCEPTION 'Publish run not found' USING ERRCODE='P0002'; END IF;
 PERFORM private.conversion_object(p,ARRAY['plan_hash','decision','reviewed_images','reviewed_caption','confirmed_account','comment']);
 IF p->>'plan_hash' IS DISTINCT FROM r.plan_hash OR p->>'decision' IS NULL
 OR p->>'decision' NOT IN ('AUTHORIZE_PUBLISH','REJECT') THEN
 RAISE EXCEPTION 'Exact reviewed publish plan required' USING ERRCODE='23514'; END IF;
 IF p->>'decision'='AUTHORIZE_PUBLISH' THEN PERFORM private.social_publish_current(r);
 ELSE PERFORM private.social_workflow_lock(r.tenant_id,r.workflow_run_id); END IF;
 SELECT * INTO r FROM public.social_publish_runs WHERE tenant_id=r.tenant_id AND id=r.id FOR UPDATE;
 IF (p->>'decision'='AUTHORIZE_PUBLISH' AND (r.status<>'AWAITING_PUBLISH_APPROVAL'
 OR p->'reviewed_images' IS DISTINCT FROM 'true'::jsonb OR p->'reviewed_caption' IS DISTINCT FROM 'true'::jsonb
 OR p->'confirmed_account' IS DISTINCT FROM 'true'::jsonb))
 OR (p->>'decision'='REJECT' AND (r.status NOT IN ('AWAITING_PUBLISH_APPROVAL','AUTHORIZED')
 OR EXISTS(SELECT 1 FROM public.social_publish_jobs WHERE tenant_id=r.tenant_id AND publish_run_id=r.id))) THEN
 RAISE EXCEPTION 'Fresh authorization or undispatched cancellation required' USING ERRCODE='23514'; END IF;
 INSERT INTO public.social_publish_decisions(tenant_id,publish_run_id,plan_hash,decision,created_by,comment)
 VALUES(r.tenant_id,r.id,r.plan_hash,p->>'decision',public.context_principal(),p->>'comment') RETURNING id INTO result;
 UPDATE public.social_publish_runs SET status=CASE WHEN p->>'decision'='AUTHORIZE_PUBLISH' THEN 'AUTHORIZED' ELSE 'REJECTED' END,
 updated_at=clock_timestamp() WHERE id=r.id RETURNING * INTO r;
 PERFORM private.social_audit(r,'SOCIAL_PUBLISH_DECISION'); RETURN result; END $$;

-- Historical observations retain publication identity/API semantics while using
-- the latest explicitly connected credential for that same account and creator.
ALTER TABLE social_publish_jobs ADD COLUMN read_connection_id uuid;
UPDATE social_publish_jobs j SET read_connection_id=r.connection_id FROM social_publish_runs r
 WHERE (r.tenant_id,r.id)=(j.tenant_id,j.publish_run_id) AND j.stage IN ('INSIGHTS','RECONCILE');
ALTER TABLE social_publish_jobs ADD FOREIGN KEY(tenant_id,read_connection_id) REFERENCES social_connections(tenant_id,id);
ALTER TABLE social_publish_jobs ADD CHECK((stage IN ('INSIGHTS','RECONCILE'))=(read_connection_id IS NOT NULL));

CREATE FUNCTION private.social_read_connection(r public.social_publish_runs,stage_name text) RETURNS uuid
 LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,public AS $$
 DECLARE original public.social_connections;current_connection public.social_connections; BEGIN
 PERFORM private.social_workflow_lock(r.tenant_id,r.workflow_run_id);
 PERFORM private.social_account_lock(r.tenant_id,r.account_id);
 SELECT * INTO original FROM public.social_connections WHERE tenant_id=r.tenant_id AND id=r.connection_id;
 SELECT * INTO current_connection FROM public.social_connections WHERE tenant_id=r.tenant_id AND account_id=r.account_id
 ORDER BY version DESC LIMIT 1;
 IF stage_name IS NULL OR stage_name NOT IN ('INSIGHTS','RECONCILE') OR original.id IS NULL OR current_connection.id IS NULL
 OR current_connection.influencer_id IS DISTINCT FROM original.influencer_id
 OR current_connection.api_version IS DISTINCT FROM original.api_version
 OR NOT (current_connection.scopes ? 'instagram_business_basic')
 OR (stage_name='INSIGHTS' AND NOT (current_connection.scopes ? 'instagram_business_manage_insights')) THEN
 RAISE EXCEPTION 'Compatible current account authorization required' USING ERRCODE='23514'; END IF;
 PERFORM private.social_connection_current(current_connection.id);
 RETURN current_connection.id; END $$;

CREATE FUNCTION public.social_read_job_connection(jobid uuid) RETURNS uuid
 LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,public AS $$
 DECLARE j public.social_publish_jobs;r public.social_publish_runs;cid uuid; BEGIN
 IF NOT public.has_role('SOCIAL') THEN RAISE EXCEPTION 'Connector required' USING ERRCODE='42501'; END IF;
 SELECT * INTO j FROM public.social_publish_jobs WHERE tenant_id=public.context_tenant() AND id=jobid;
 SELECT * INTO r FROM public.social_publish_runs WHERE tenant_id=public.context_tenant() AND id=j.publish_run_id;
 IF j.status IS DISTINCT FROM 'RUNNING' OR j.stage NOT IN ('INSIGHTS','RECONCILE') OR j.read_connection_id IS NULL THEN
 RAISE EXCEPTION 'Reserved historical read required' USING ERRCODE='23514'; END IF;
 cid:=private.social_read_connection(r,j.stage);
 IF cid IS DISTINCT FROM j.read_connection_id THEN
 RAISE EXCEPTION 'Read account authorization changed after reservation' USING ERRCODE='23514'; END IF;
 RETURN cid; END $$;
REVOKE ALL ON FUNCTION private.social_read_connection(public.social_publish_runs,text) FROM PUBLIC,mediaos_runtime;
REVOKE ALL ON FUNCTION public.social_read_job_connection(uuid) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION public.social_read_job_connection(uuid) TO mediaos_runtime;

CREATE OR REPLACE FUNCTION public.social_reserve_job(pid uuid,stage_name text,step text,inputhash text) RETURNS uuid
 LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,public AS $$
 DECLARE r public.social_publish_runs; j public.social_publish_jobs; sid uuid; result uuid; attempts int; read_connection uuid; BEGIN
 IF NOT public.has_role('SOCIAL') THEN RAISE EXCEPTION 'Connector required' USING ERRCODE='42501'; END IF;
 SELECT * INTO r FROM public.social_publish_runs WHERE tenant_id=public.context_tenant() AND id=pid;
 IF r.id IS NULL THEN RAISE EXCEPTION 'Publish run not found' USING ERRCODE='P0002'; END IF;
 IF stage_name NOT IN ('INSIGHTS','RECONCILE') THEN PERFORM private.social_publish_current(r);
 ELSE read_connection:=private.social_read_connection(r,stage_name); END IF;
 SELECT * INTO r FROM public.social_publish_runs WHERE tenant_id=r.tenant_id AND id=r.id FOR UPDATE;
 IF NOT EXISTS(SELECT 1 FROM public.social_publish_decisions WHERE tenant_id=r.tenant_id
 AND publish_run_id=r.id AND decision='AUTHORIZE_PUBLISH' AND plan_hash=r.plan_hash)
 OR stage_name IS NULL OR stage_name NOT IN ('CHILD','CONTAINER','POLL','PUBLISH','INSIGHTS','RECONCILE')
 OR step IS NULL OR inputhash IS NULL THEN RAISE EXCEPTION 'Authorized operation required' USING ERRCODE='23514'; END IF;
 IF (stage_name='INSIGHTS' AND r.status<>'PUBLISHED')
 OR (stage_name='RECONCILE' AND (r.status<>'UNKNOWN_OUTCOME' OR NOT EXISTS(
 SELECT 1 FROM public.social_publish_jobs WHERE tenant_id=r.tenant_id AND publish_run_id=r.id
 AND stage='PUBLISH' AND status='UNKNOWN_OUTCOME')))
 OR (stage_name NOT IN ('INSIGHTS','RECONCILE') AND r.status NOT IN ('AUTHORIZED','PREPARING','READY')) THEN
 RAISE EXCEPTION 'Invalid social stage' USING ERRCODE='23514'; END IF;
 IF stage_name='RECONCILE' AND (NOT EXISTS(SELECT 1 FROM public.social_reconcile_requests
 WHERE tenant_id=r.tenant_id AND publish_run_id=r.id AND input_hash=inputhash)
 OR (SELECT count(*) FROM public.social_publish_jobs WHERE tenant_id=r.tenant_id AND publish_run_id=r.id AND stage='RECONCILE')>=10) THEN
 RAISE EXCEPTION 'Bounded human reconciliation required' USING ERRCODE='23514'; END IF;
 SELECT * INTO j FROM public.social_publish_jobs WHERE tenant_id=r.tenant_id AND publish_run_id=r.id AND step_key=step
 ORDER BY attempt DESC LIMIT 1;
 IF j.id IS NOT NULL AND (j.input_hash IS DISTINCT FROM inputhash OR j.stage IS DISTINCT FROM stage_name) THEN
 RAISE EXCEPTION 'Checkpoint mismatch' USING ERRCODE='23514'; END IF;
 IF j.status='SUCCEEDED' THEN
 RETURN j.id; END IF;
 IF j.id IS NOT NULL AND (j.status IN ('RUNNING','UNKNOWN_OUTCOME') OR NOT j.retryable OR j.retry_at>clock_timestamp()) THEN
 RAISE EXCEPTION 'Attempt requires reconciliation or cooldown' USING ERRCODE='23514'; END IF;
 SELECT count(*)+1 INTO attempts FROM public.social_publish_jobs WHERE tenant_id=r.tenant_id AND publish_run_id=r.id AND step_key=step;
 IF attempts>3 THEN RAISE EXCEPTION 'Attempts exhausted' USING ERRCODE='23514'; END IF;
 IF stage_name='CHILD' AND (step !~ '^child:[1-9][0-9]?$'
 OR substring(step from 7)::int>jsonb_array_length(r.plan->'slides') OR r.status<>'AUTHORIZED' AND r.status<>'PREPARING') THEN
 RAISE EXCEPTION 'Invalid child stage' USING ERRCODE='23514'; END IF;
 IF stage_name='CONTAINER' AND (step<>'container' OR jsonb_array_length(r.plan->'slides')<2 OR
 (SELECT count(*) FROM public.social_publish_jobs WHERE tenant_id=r.tenant_id AND publish_run_id=r.id
 AND stage='CHILD' AND status='SUCCEEDED')<>jsonb_array_length(r.plan->'slides')) THEN
 RAISE EXCEPTION 'Completed children required' USING ERRCODE='23514'; END IF;
 IF stage_name='POLL' THEN
 IF (jsonb_array_length(r.plan->'slides')=1 AND NOT EXISTS(SELECT 1 FROM public.social_publish_jobs WHERE
 tenant_id=r.tenant_id AND publish_run_id=r.id AND stage='CHILD' AND status='SUCCEEDED'))
 OR (jsonb_array_length(r.plan->'slides')>1 AND NOT EXISTS(SELECT 1 FROM public.social_publish_jobs WHERE
 tenant_id=r.tenant_id AND publish_run_id=r.id AND stage='CONTAINER' AND status='SUCCEEDED'))
 OR (SELECT count(*) FROM public.social_publish_jobs WHERE tenant_id=r.tenant_id AND publish_run_id=r.id AND stage='POLL')>=90
 OR EXISTS(SELECT 1 FROM public.social_publish_jobs WHERE tenant_id=r.tenant_id AND publish_run_id=r.id
 AND stage='POLL' AND started_at>clock_timestamp()-interval '2 seconds') THEN
 RAISE EXCEPTION 'Bounded container polling required' USING ERRCODE='23514'; END IF; END IF;
 IF stage_name='PUBLISH' AND (step<>'publish' OR r.status<>'READY') THEN
 RAISE EXCEPTION 'Ready container required' USING ERRCODE='23514'; END IF;
 INSERT INTO public.skill_runs(tenant_id,workflow_run_id,step_key,skill_identifier,skill_version,
 input_schema_version,output_schema_version,provider,model,adapter,attempt,input_hash,status,is_mock,cost)
 VALUES(r.tenant_id,r.workflow_run_id,'social:'||r.id::text||':'||step,'social.'||lower(stage_name),
 '1.0.0',1,1,'instagram','graph-api','instagram-login-v1',attempts,inputhash,'RUNNING',false,NULL) RETURNING id INTO sid;
 INSERT INTO public.cost_events(tenant_id,workflow_run_id,skill_run_id,provider,model,input_tokens,output_tokens,cost,currency,price_version)
 VALUES(r.tenant_id,r.workflow_run_id,sid,'instagram','graph-api',0,0,NULL,'USD','provider-unreported');
 INSERT INTO public.social_publish_jobs(tenant_id,publish_run_id,workflow_run_id,skill_run_id,stage,step_key,attempt,input_hash,read_connection_id)
 VALUES(r.tenant_id,r.id,r.workflow_run_id,sid,stage_name,step,attempts,inputhash,read_connection) RETURNING id INTO result;
 IF stage_name NOT IN ('INSIGHTS','RECONCILE') THEN
 UPDATE public.social_publish_runs SET status=CASE WHEN stage_name='PUBLISH' THEN 'PUBLISHING' ELSE 'PREPARING' END,
 updated_at=clock_timestamp() WHERE id=r.id RETURNING * INTO r; END IF;
 PERFORM private.social_audit(r,'SOCIAL_JOB_STARTED',jsonb_build_object('job_id',result,'stage',stage_name,'attempt',attempts,'read_connection_id',read_connection));
 RETURN result; END $$;
