-- Provider Retry-After belongs to the account, not to a caller-chosen request key.
ALTER TABLE private.social_account_ownership ADD CONSTRAINT social_account_ownership_tenant_account_key
 UNIQUE(tenant_id,account_id);
CREATE TABLE private.social_account_cooldowns (
 tenant_id uuid NOT NULL,account_id text NOT NULL,retry_after timestamptz NOT NULL,
 updated_at timestamptz NOT NULL DEFAULT clock_timestamp(),PRIMARY KEY(tenant_id,account_id),
 FOREIGN KEY(tenant_id,account_id) REFERENCES private.social_account_ownership(tenant_id,account_id)
);
-- Preserve pending rate limits when upgrading an existing installation, including
-- exhausted attempts whose per-job retry_at is intentionally null.
INSERT INTO private.social_account_cooldowns(tenant_id,account_id,retry_after)
 SELECT tenant_id,account_id,max(retry_after) FROM (
 SELECT r.tenant_id,r.account_id,coalesce(j.retry_at,j.ended_at+interval '60 seconds') AS retry_after
 FROM social_publish_jobs j JOIN social_publish_runs r ON (r.tenant_id,r.id)=(j.tenant_id,j.publish_run_id)
 WHERE j.error_category='RATE_LIMITED' AND j.ended_at IS NOT NULL
 UNION ALL
 SELECT r.tenant_id,c.account_id,coalesce(j.retry_at,j.ended_at+interval '60 seconds')
 FROM social_reply_jobs j JOIN social_reply_runs r ON (r.tenant_id,r.id)=(j.tenant_id,j.reply_run_id)
 JOIN social_connections c ON (c.tenant_id,c.id)=(r.tenant_id,r.connection_id)
 WHERE j.error_category='RATE_LIMITED' AND j.ended_at IS NOT NULL
 ) prior GROUP BY tenant_id,account_id;
ALTER TABLE private.social_account_cooldowns ENABLE ROW LEVEL SECURITY;
ALTER TABLE private.social_account_cooldowns FORCE ROW LEVEL SECURITY;
CREATE POLICY tenant_boundary ON private.social_account_cooldowns
 USING(tenant_id=public.context_tenant()) WITH CHECK(tenant_id=public.context_tenant());
REVOKE ALL ON private.social_account_cooldowns FROM PUBLIC,mediaos_runtime;

-- Audit and skill foreign keys also lock the workflow. Acquire that lock first,
-- before account/parent/job locks, including receipt recovery after content changes.
CREATE FUNCTION private.social_workflow_lock(tid uuid,wid uuid) RETURNS void
 LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,public AS $$
 BEGIN
 IF tid IS DISTINCT FROM public.context_tenant() THEN
 RAISE EXCEPTION 'Owned social workflow required' USING ERRCODE='42501'; END IF;
 PERFORM 1 FROM public.workflow_runs WHERE tenant_id=tid AND id=wid FOR KEY SHARE;
 IF NOT FOUND THEN RAISE EXCEPTION 'Owned social workflow required' USING ERRCODE='42501'; END IF;
 END $$;

CREATE FUNCTION private.social_account_lock(tid uuid,account text) RETURNS void
 LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,public AS $$
 BEGIN
 IF tid IS DISTINCT FROM public.context_tenant() OR account IS NULL OR NOT EXISTS(
 SELECT 1 FROM private.social_account_ownership WHERE tenant_id=tid AND account_id=account) THEN
 RAISE EXCEPTION 'Owned social account required' USING ERRCODE='42501'; END IF;
 PERFORM pg_advisory_xact_lock(hashtextextended('social-account:'||account,0));
 END $$;

CREATE FUNCTION private.social_record_cooldown(tid uuid,account text,seconds int) RETURNS timestamptz
 LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,public AS $$
 DECLARE result timestamptz; BEGIN
 PERFORM private.social_account_lock(tid,account);
 IF seconds IS NULL OR seconds NOT BETWEEN 1 AND 86400 THEN
 RAISE EXCEPTION 'Bounded account cooldown required' USING ERRCODE='23514'; END IF;
 INSERT INTO private.social_account_cooldowns(tenant_id,account_id,retry_after)
 VALUES(tid,account,clock_timestamp()+make_interval(secs=>seconds))
 ON CONFLICT(tenant_id,account_id) DO UPDATE SET
 retry_after=greatest(social_account_cooldowns.retry_after,EXCLUDED.retry_after),updated_at=clock_timestamp()
 RETURNING retry_after INTO result;
 RETURN result; END $$;

CREATE FUNCTION private.social_job_account_cooldown() RETURNS trigger
 LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,public AS $$
 DECLARE account text; BEGIN
 IF TG_TABLE_NAME='social_publish_jobs' THEN
 SELECT account_id INTO account FROM public.social_publish_runs
 WHERE tenant_id=NEW.tenant_id AND id=NEW.publish_run_id;
 ELSE
 SELECT c.account_id INTO account FROM public.social_reply_runs r JOIN public.social_connections c
 ON (c.tenant_id,c.id)=(r.tenant_id,r.connection_id) WHERE r.tenant_id=NEW.tenant_id AND r.id=NEW.reply_run_id;
 END IF;
 -- Reservation functions already take this lock before the parent row lock.
 -- Reentrant acquisition also protects future guarded insertion paths.
 PERFORM private.social_account_lock(NEW.tenant_id,account);
 IF EXISTS(SELECT 1 FROM private.social_account_cooldowns WHERE tenant_id=NEW.tenant_id
 AND account_id=account AND retry_after>clock_timestamp()) THEN
 RAISE EXCEPTION 'Social account cooldown active' USING ERRCODE='23514'; END IF;
 RETURN NEW; END $$;
CREATE TRIGGER account_cooldown BEFORE INSERT ON social_publish_jobs
 FOR EACH ROW EXECUTE FUNCTION private.social_job_account_cooldown();
CREATE TRIGGER account_cooldown BEFORE INSERT ON social_reply_jobs
 FOR EACH ROW EXECUTE FUNCTION private.social_job_account_cooldown();

CREATE FUNCTION private.social_validate_failure(category text,retry boolean,unknown boolean,delay_seconds int,side_effect boolean)
 RETURNS void LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,public AS $$
 BEGIN
 IF category IS NULL OR category!~'^[A-Z][A-Z0-9_]{0,79}$' OR retry IS NULL OR unknown IS NULL
 OR side_effect IS NULL OR delay_seconds IS NULL OR delay_seconds NOT BETWEEN 0 AND 86400
 OR (category='UNKNOWN_OUTCOME' AND NOT unknown)
 OR (side_effect AND category IN ('PROCESS_INTERRUPTED','LOCAL_RECEIPT_FAILED') AND NOT unknown)
 OR (retry AND (unknown OR (side_effect AND category<>'RATE_LIMITED')
 OR (NOT side_effect AND category NOT IN ('RATE_LIMITED','NETWORK','PROVIDER_UNAVAILABLE','PROCESS_INTERRUPTED')))) THEN
 RAISE EXCEPTION 'Consistent bounded social failure required' USING ERRCODE='23514'; END IF;
 END $$;

CREATE OR REPLACE FUNCTION public.social_fail_job(jobid uuid,category text,retry boolean,unknown boolean,delay_seconds int DEFAULT 0) RETURNS void
 LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,public AS $$
 DECLARE j public.social_publish_jobs;r public.social_publish_runs;can_retry boolean;
 retrytime timestamptz;account_retry timestamptz; BEGIN
 IF NOT public.has_role('SOCIAL') THEN RAISE EXCEPTION 'Connector required' USING ERRCODE='42501'; END IF;
 SELECT * INTO j FROM public.social_publish_jobs WHERE tenant_id=public.context_tenant() AND id=jobid;
 SELECT * INTO r FROM public.social_publish_runs WHERE tenant_id=public.context_tenant() AND id=j.publish_run_id;
 PERFORM private.social_workflow_lock(r.tenant_id,r.workflow_run_id);
 -- Workflow precedes account, then job/parent locks used by receipt completion.
 PERFORM private.social_account_lock(r.tenant_id,r.account_id);
 SELECT * INTO j FROM public.social_publish_jobs WHERE tenant_id=public.context_tenant() AND id=jobid FOR UPDATE;
 SELECT * INTO r FROM public.social_publish_runs WHERE tenant_id=public.context_tenant() AND id=j.publish_run_id FOR UPDATE;
 IF j.status IS DISTINCT FROM 'RUNNING' THEN RAISE EXCEPTION 'Active failure required' USING ERRCODE='23514'; END IF;
 PERFORM private.social_validate_failure(category,retry,unknown,delay_seconds,j.stage IN ('CHILD','CONTAINER','PUBLISH'));
 -- Preserve Retry-After even for an uncertain outcome or exhausted final attempt.
 IF category='RATE_LIMITED' OR delay_seconds>0 THEN
 account_retry:=private.social_record_cooldown(r.tenant_id,r.account_id,
 greatest(CASE WHEN delay_seconds>0 THEN delay_seconds ELSE 60 END,power(2,j.attempt)::int)); END IF;
 can_retry:=retry AND NOT unknown AND j.attempt<3;
 IF can_retry THEN retrytime:=greatest(account_retry,clock_timestamp()+make_interval(secs=>greatest(delay_seconds,power(2,j.attempt)::int))); END IF;
 UPDATE public.social_publish_jobs SET status=CASE WHEN unknown THEN 'UNKNOWN_OUTCOME' ELSE 'FAILED' END,
 error_category=category,retryable=can_retry,retry_at=retrytime,ended_at=clock_timestamp() WHERE id=j.id;
 UPDATE public.skill_runs SET status='FAILED',error_category=category,retryable=can_retry,retry_at=retrytime,
 ended_at=clock_timestamp(),latency_ms=extract(epoch FROM clock_timestamp()-j.started_at)*1000 WHERE id=j.skill_run_id;
 IF j.stage NOT IN ('INSIGHTS','RECONCILE') THEN
 UPDATE public.social_publish_runs SET status=CASE WHEN unknown THEN 'UNKNOWN_OUTCOME'
 WHEN can_retry AND j.stage='PUBLISH' THEN 'READY' WHEN can_retry THEN 'PREPARING'
 WHEN category='POLICY_BLOCKED' THEN 'BLOCKED' ELSE 'FAILED' END,updated_at=clock_timestamp()
 WHERE id=r.id RETURNING * INTO r; END IF;
 PERFORM private.social_audit(r,'SOCIAL_JOB_FAILED',jsonb_build_object('job_id',j.id,'stage',j.stage,
 'error_category',category,'retryable',can_retry,'account_retry_after',account_retry)); END $$;

CREATE OR REPLACE FUNCTION public.social_fail_reply(jobid uuid,category text,retry boolean,unknown boolean,delay_seconds int DEFAULT 0) RETURNS void
 LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,public AS $$
 DECLARE r public.social_reply_runs;j public.social_reply_jobs;can_retry boolean;
 retrytime timestamptz;account_retry timestamptz;account text; BEGIN
 IF NOT public.has_role('SOCIAL') THEN RAISE EXCEPTION 'Connector required' USING ERRCODE='42501'; END IF;
 SELECT * INTO j FROM public.social_reply_jobs WHERE tenant_id=public.context_tenant() AND id=jobid;
 SELECT * INTO r FROM public.social_reply_runs WHERE tenant_id=public.context_tenant() AND id=j.reply_run_id;
 PERFORM private.social_workflow_lock(r.tenant_id,r.workflow_run_id);
 SELECT account_id INTO account FROM public.social_connections WHERE tenant_id=r.tenant_id AND id=r.connection_id;
 PERFORM private.social_account_lock(r.tenant_id,account);
 SELECT * INTO r FROM public.social_reply_runs WHERE tenant_id=public.context_tenant() AND id=j.reply_run_id FOR UPDATE;
 SELECT * INTO j FROM public.social_reply_jobs WHERE tenant_id=public.context_tenant() AND id=jobid FOR UPDATE;
 IF j.status IS DISTINCT FROM 'RUNNING' THEN RAISE EXCEPTION 'Active failure required' USING ERRCODE='23514'; END IF;
 PERFORM private.social_validate_failure(category,retry,unknown,delay_seconds,true);
 IF category='RATE_LIMITED' OR delay_seconds>0 THEN
 account_retry:=private.social_record_cooldown(r.tenant_id,account,
 greatest(CASE WHEN delay_seconds>0 THEN delay_seconds ELSE 60 END,power(2,j.attempt)::int)); END IF;
 can_retry:=retry AND NOT unknown AND j.attempt<3;
 IF can_retry THEN retrytime:=greatest(account_retry,clock_timestamp()+make_interval(secs=>greatest(delay_seconds,power(2,j.attempt)::int))); END IF;
 UPDATE public.social_reply_jobs SET status=CASE WHEN unknown THEN 'UNKNOWN_OUTCOME' ELSE 'FAILED' END,
 retryable=can_retry,retry_at=retrytime,error_category=category,ended_at=clock_timestamp() WHERE id=j.id;
 UPDATE public.skill_runs SET status='FAILED',retryable=can_retry,retry_at=retrytime,error_category=category,
 ended_at=clock_timestamp(),latency_ms=extract(epoch FROM clock_timestamp()-j.started_at)*1000 WHERE id=j.skill_run_id;
 UPDATE public.social_reply_runs SET status=CASE WHEN unknown THEN 'UNKNOWN_OUTCOME' WHEN can_retry THEN 'AUTHORIZED'
 WHEN category='POLICY_BLOCKED' THEN 'BLOCKED' ELSE 'FAILED' END,updated_at=clock_timestamp() WHERE id=r.id RETURNING * INTO r;
 PERFORM private.social_reply_audit(r,'SOCIAL_REPLY_FAILED',jsonb_build_object('job_id',j.id,'attempt',j.attempt,
 'error_category',category,'retryable',can_retry,'account_retry_after',account_retry)); END $$;

REVOKE ALL ON FUNCTION private.social_workflow_lock(uuid,uuid),private.social_account_lock(uuid,text),private.social_record_cooldown(uuid,text,int),
 private.social_job_account_cooldown(),private.social_validate_failure(text,boolean,boolean,int,boolean) FROM PUBLIC,mediaos_runtime;
REVOKE ALL ON FUNCTION public.social_fail_job(uuid,text,boolean,boolean,int),public.social_fail_reply(uuid,text,boolean,boolean,int) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION public.social_fail_job(uuid,text,boolean,boolean,int),public.social_fail_reply(uuid,text,boolean,boolean,int) TO mediaos_runtime;

-- Existing APIs retain their ACLs; only acquisition order changes below.
CREATE OR REPLACE FUNCTION public.social_reserve_job(pid uuid,stage_name text,step text,inputhash text) RETURNS uuid
 LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,public AS $$
 DECLARE r public.social_publish_runs; j public.social_publish_jobs; sid uuid; result uuid; attempts int; BEGIN
 IF NOT public.has_role('SOCIAL') THEN RAISE EXCEPTION 'Connector required' USING ERRCODE='42501'; END IF;
 SELECT * INTO r FROM public.social_publish_runs WHERE tenant_id=public.context_tenant() AND id=pid;
 IF r.id IS NULL THEN RAISE EXCEPTION 'Publish run not found' USING ERRCODE='P0002'; END IF;
 IF stage_name NOT IN ('INSIGHTS','RECONCILE') THEN PERFORM private.social_publish_current(r);
 ELSE PERFORM private.social_workflow_lock(r.tenant_id,r.workflow_run_id);
 PERFORM private.social_connection_current(r.connection_id); END IF;
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
 INSERT INTO public.social_publish_jobs(tenant_id,publish_run_id,workflow_run_id,skill_run_id,stage,step_key,attempt,input_hash)
 VALUES(r.tenant_id,r.id,r.workflow_run_id,sid,stage_name,step,attempts,inputhash) RETURNING id INTO result;
 IF stage_name NOT IN ('INSIGHTS','RECONCILE') THEN
 UPDATE public.social_publish_runs SET status=CASE WHEN stage_name='PUBLISH' THEN 'PUBLISHING' ELSE 'PREPARING' END,
 updated_at=clock_timestamp() WHERE id=r.id RETURNING * INTO r; END IF;
 PERFORM private.social_audit(r,'SOCIAL_JOB_STARTED',jsonb_build_object('job_id',result,'stage',stage_name,'attempt',attempts));
 RETURN result; END $$;

CREATE OR REPLACE FUNCTION public.social_finish_job(jobid uuid,p jsonb) RETURNS void
 LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,public AS $$
 DECLARE j public.social_publish_jobs; r public.social_publish_runs; nextstate text; BEGIN
 IF NOT public.has_role('SOCIAL') THEN RAISE EXCEPTION 'Connector required' USING ERRCODE='42501'; END IF;
 SELECT * INTO j FROM public.social_publish_jobs WHERE tenant_id=public.context_tenant() AND id=jobid;
 PERFORM private.social_workflow_lock(j.tenant_id,j.workflow_run_id);
 SELECT * INTO j FROM public.social_publish_jobs WHERE tenant_id=public.context_tenant() AND id=jobid FOR UPDATE;
 SELECT * INTO r FROM public.social_publish_runs WHERE tenant_id=public.context_tenant() AND id=j.publish_run_id FOR UPDATE;
 IF j.id IS NULL OR j.status<>'RUNNING' OR p IS NULL OR jsonb_typeof(p)<>'object' OR octet_length(p::text)>131072 THEN
 RAISE EXCEPTION 'Reserved job and bounded result required' USING ERRCODE='23514'; END IF;
 IF j.stage IN ('CHILD','CONTAINER','PUBLISH','RECONCILE') THEN
 PERFORM private.conversion_object(p,ARRAY['id','response_hash','captured_at']);
 IF p->>'id' IS NULL OR (p->>'id')!~'^[0-9]{1,64}$' OR p->>'response_hash' IS NULL
 OR (p->>'response_hash')!~'^[0-9a-f]{64}$' OR p->>'captured_at' IS NULL
 OR (p->>'captured_at')::timestamptz<j.started_at-interval '5 seconds'
 OR (p->>'captured_at')::timestamptz>clock_timestamp()+interval '30 seconds' THEN
 RAISE EXCEPTION 'Invalid provider acknowledgement' USING ERRCODE='23514'; END IF;
 IF j.stage='RECONCILE' AND NOT EXISTS(SELECT 1 FROM public.social_reconcile_requests
 WHERE tenant_id=r.tenant_id AND publish_run_id=r.id AND input_hash=j.input_hash AND candidate_media_id=p->>'id') THEN
 RAISE EXCEPTION 'Exact reconciled candidate required' USING ERRCODE='23514'; END IF;
 nextstate:=CASE WHEN j.stage IN ('PUBLISH','RECONCILE') THEN 'PUBLISHED' ELSE 'PREPARING' END;
 ELSIF j.stage='POLL' THEN
 PERFORM private.conversion_object(p,ARRAY['status_code','response_hash','captured_at']);
 IF p->>'status_code' IS NULL OR p->>'status_code' NOT IN ('IN_PROGRESS','FINISHED','ERROR','EXPIRED','PUBLISHED')
 OR p->>'response_hash' IS NULL OR (p->>'response_hash')!~'^[0-9a-f]{64}$' OR p->>'captured_at' IS NULL
 OR (p->>'captured_at')::timestamptz<j.started_at-interval '5 seconds'
 OR (p->>'captured_at')::timestamptz>clock_timestamp()+interval '30 seconds' THEN
 RAISE EXCEPTION 'Invalid container status' USING ERRCODE='23514'; END IF;
 nextstate:=CASE p->>'status_code' WHEN 'FINISHED' THEN 'READY' WHEN 'IN_PROGRESS' THEN 'PREPARING'
 WHEN 'PUBLISHED' THEN 'UNKNOWN_OUTCOME' ELSE 'FAILED' END;
 ELSE
 PERFORM private.conversion_object(p,ARRAY['schema_version','media_id','captured_at','api_version','raw','raw_hash','metrics','definitions']);
 IF p->'schema_version' IS DISTINCT FROM '1'::jsonb OR p->>'media_id' IS DISTINCT FROM r.post_id
 OR p->>'raw_hash' IS NULL OR p->>'raw_hash' IS DISTINCT FROM private.visual_hash(p->'raw')
 OR jsonb_typeof(p->'metrics') IS DISTINCT FROM 'object' OR jsonb_typeof(p->'definitions') IS DISTINCT FROM 'object'
 OR p->>'api_version' IS DISTINCT FROM (SELECT api_version FROM public.social_connections WHERE tenant_id=r.tenant_id AND id=r.connection_id)
 OR p->>'captured_at' IS NULL OR (p->>'captured_at')::timestamptz<j.started_at-interval '5 seconds'
 OR (p->>'captured_at')::timestamptz>clock_timestamp()+interval '30 seconds' THEN
 RAISE EXCEPTION 'Owned observed insights required' USING ERRCODE='23514'; END IF;
 INSERT INTO public.social_insight_snapshots(tenant_id,publish_run_id,workflow_run_id,job_id,payload,content_hash,captured_at)
 VALUES(r.tenant_id,r.id,r.workflow_run_id,j.id,p,private.visual_hash(p),(p->>'captured_at')::timestamptz);
 nextstate:=r.status; END IF;
 UPDATE public.social_publish_jobs SET status='SUCCEEDED',result=p,ended_at=clock_timestamp() WHERE id=j.id;
 UPDATE public.skill_runs SET status='SUCCEEDED',output=p,ended_at=clock_timestamp(),
 latency_ms=extract(epoch FROM clock_timestamp()-j.started_at)*1000 WHERE id=j.skill_run_id;
 UPDATE public.social_publish_runs SET status=nextstate,updated_at=clock_timestamp(),
 post_id=CASE WHEN j.stage IN ('PUBLISH','RECONCILE') THEN p->>'id' ELSE post_id END,
 published_at=CASE WHEN j.stage IN ('PUBLISH','RECONCILE') THEN clock_timestamp() ELSE published_at END
 WHERE id=r.id RETURNING * INTO r;
 PERFORM private.social_audit(r,'SOCIAL_JOB_COMPLETED',jsonb_build_object('job_id',j.id,'stage',j.stage));
 END $$;

CREATE OR REPLACE FUNCTION public.social_request_reconciliation(pid uuid,p jsonb) RETURNS uuid
 LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,public AS $$
 DECLARE r public.social_publish_runs; result uuid; BEGIN
 IF NOT public.has_role('APPROVER') THEN RAISE EXCEPTION 'Approver required' USING ERRCODE='42501'; END IF;
 SELECT * INTO r FROM public.social_publish_runs WHERE tenant_id=public.context_tenant() AND id=pid;
 PERFORM private.social_workflow_lock(r.tenant_id,r.workflow_run_id);
 SELECT * INTO r FROM public.social_publish_runs WHERE tenant_id=public.context_tenant() AND id=pid FOR UPDATE;
 IF r.id IS NULL THEN RAISE EXCEPTION 'Publish run not found' USING ERRCODE='P0002'; END IF;
 PERFORM private.conversion_object(p,ARRAY['candidate_media_id','confirm_external_match','comment']);
 IF r.status<>'UNKNOWN_OUTCOME' OR p->'confirm_external_match' IS DISTINCT FROM 'true'::jsonb
 OR p->>'candidate_media_id' IS NULL OR (p->>'candidate_media_id')!~'^[0-9]{1,64}$'
 OR NOT EXISTS(SELECT 1 FROM public.social_publish_jobs WHERE tenant_id=r.tenant_id AND publish_run_id=r.id
 AND stage='PUBLISH' AND status='UNKNOWN_OUTCOME') THEN
 RAISE EXCEPTION 'Explicit reconciliation of an uncertain post required' USING ERRCODE='23514'; END IF;
 INSERT INTO public.social_reconcile_requests(tenant_id,publish_run_id,candidate_media_id,input_hash,created_by,comment)
 VALUES(r.tenant_id,r.id,p->>'candidate_media_id',private.visual_hash(p),public.context_principal(),p->>'comment')
 ON CONFLICT DO NOTHING RETURNING id INTO result;
 IF result IS NULL THEN SELECT id INTO result FROM public.social_reconcile_requests WHERE tenant_id=r.tenant_id
 AND publish_run_id=r.id AND input_hash=private.visual_hash(p); ELSE
 PERFORM private.social_audit(r,'SOCIAL_RECONCILIATION_REQUESTED',jsonb_build_object('request_id',result)); END IF;
 RETURN result; END $$;

CREATE OR REPLACE FUNCTION public.social_finish_reply(jobid uuid,p jsonb) RETURNS void
 LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,public AS $$
 DECLARE j public.social_reply_jobs;r public.social_reply_runs;captured timestamptz; BEGIN
 IF NOT public.has_role('SOCIAL') THEN RAISE EXCEPTION 'Connector required' USING ERRCODE='42501'; END IF;
 SELECT * INTO j FROM public.social_reply_jobs WHERE tenant_id=public.context_tenant() AND id=jobid;
 SELECT * INTO r FROM public.social_reply_runs WHERE tenant_id=public.context_tenant() AND id=j.reply_run_id;
 IF r.id IS NULL THEN RAISE EXCEPTION 'Reply not found' USING ERRCODE='P0002'; END IF;
 PERFORM private.social_workflow_lock(r.tenant_id,r.workflow_run_id);
 -- A receipt records a side effect that already happened. Freshness is checked
 -- while dispatch locks are held, not retroactively used to erase sent history.
 SELECT * INTO r FROM public.social_reply_runs WHERE tenant_id=r.tenant_id AND id=r.id FOR UPDATE;
 SELECT * INTO j FROM public.social_reply_jobs WHERE tenant_id=r.tenant_id AND id=jobid FOR UPDATE;
 PERFORM private.conversion_object(p,ARRAY['schema_version','id','response_hash','captured_at','raw','raw_hash','text_hash']);
 IF j.status IS DISTINCT FROM 'RUNNING' OR r.status<>'SENDING' OR p->'schema_version' IS DISTINCT FROM '1'::jsonb
 OR jsonb_typeof(p->'id') IS DISTINCT FROM 'string' OR p->>'id' !~ '^[0-9]{1,64}$'
 OR jsonb_typeof(p->'response_hash') IS DISTINCT FROM 'string' OR p->>'response_hash' !~ '^[0-9a-f]{64}$'
 OR jsonb_typeof(p->'raw') IS DISTINCT FROM 'object' OR octet_length(p::text)>131072
 OR p->>'raw_hash' IS DISTINCT FROM private.visual_hash(p->'raw') OR p->'raw'->>'id' IS DISTINCT FROM p->>'id'
 OR p->>'text_hash' IS DISTINCT FROM r.text_hash OR jsonb_typeof(p->'captured_at') IS DISTINCT FROM 'string'
 OR p->>'captured_at' !~ '^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(\.\d{1,6})?(Z|\+00:00)$' THEN
 RAISE EXCEPTION 'Exact bounded reply receipt required' USING ERRCODE='23514'; END IF;
 captured:=(p->>'captured_at')::timestamptz;
 IF captured<j.started_at-interval '5 seconds' OR captured>clock_timestamp()+interval '30 seconds' THEN
 RAISE EXCEPTION 'Invalid reply receipt timestamp' USING ERRCODE='23514'; END IF;
 UPDATE public.social_reply_jobs SET status='SUCCEEDED',result=p,result_hash=private.visual_hash(p),ended_at=clock_timestamp() WHERE id=j.id;
 UPDATE public.skill_runs SET status='SUCCEEDED',output=p,ended_at=clock_timestamp(),
 latency_ms=extract(epoch FROM clock_timestamp()-j.started_at)*1000 WHERE id=j.skill_run_id;
 UPDATE public.social_reply_runs SET status='SENT',reply_id=p->>'id',sent_at=captured,updated_at=clock_timestamp() WHERE id=r.id RETURNING * INTO r;
 PERFORM private.social_reply_audit(r,'SOCIAL_REPLY_SENT',jsonb_build_object('job_id',j.id)); END $$;
