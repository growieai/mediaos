-- Social dispatch is independent from dry-run deliveries and never upgrades them.
GRANT SELECT ON alembic_version TO mediaos_runtime;
ALTER TABLE tenant_memberships DROP CONSTRAINT tenant_memberships_roles_check;
ALTER TABLE tenant_memberships ADD CONSTRAINT tenant_memberships_roles_check CHECK(
 roles <@ ARRAY['OPERATOR','APPROVER','ADMIN','INGESTOR','SOCIAL']::text[] AND cardinality(roles)>0);
ALTER TABLE skill_runs DROP CONSTRAINT skill_runs_provider_check;
ALTER TABLE skill_runs ADD CHECK(provider IN ('mock','deterministic','elevenlabs','heygen','openai','instagram'));
ALTER TABLE cost_events DROP CONSTRAINT cost_events_provider_check;
ALTER TABLE cost_events ADD CHECK(provider IN ('mock','deterministic','elevenlabs','heygen','openai','instagram'));
ALTER TABLE skill_runs DROP CONSTRAINT skill_runs_cost_check;
ALTER TABLE skill_runs ADD CHECK((provider IN ('mock','deterministic') AND cost IS NOT NULL AND cost=0)
 OR (provider IN ('elevenlabs','heygen','openai','instagram') AND (cost IS NULL OR cost>=0)));
ALTER TABLE cost_events DROP CONSTRAINT cost_events_cost_check;
ALTER TABLE cost_events ADD CHECK((provider IN ('mock','deterministic') AND cost IS NOT NULL AND cost=0)
 OR (provider IN ('elevenlabs','heygen','openai','instagram') AND (cost IS NULL OR cost>=0)));

-- The connector can record only its own attempts through owner-executed guarded
-- functions. It receives no OPERATOR role and cannot write skill history directly.
CREATE OR REPLACE FUNCTION public.guard_skill() RETURNS trigger LANGUAGE plpgsql AS $$
 BEGIN
 IF NOT public.has_role('OPERATOR') AND NOT (
 public.has_role('SOCIAL') AND NEW.provider='instagram' AND NEW.skill_identifier LIKE 'social.%'
 AND current_user=pg_get_userbyid((SELECT relowner FROM pg_class WHERE oid='public.skill_runs'::regclass))
 ) THEN RAISE EXCEPTION 'Operator or guarded connector required' USING ERRCODE='42501'; END IF;
 IF TG_OP='UPDATE' AND (OLD.status<>'RUNNING' OR NEW.status NOT IN ('SUCCEEDED','FAILED','INTERRUPTED')
 OR NEW.ended_at IS NULL OR NEW.latency_ms IS NULL OR NEW.latency_ms<0) THEN
 RAISE EXCEPTION 'Invalid skill attempt completion' USING ERRCODE='23514'; END IF;
 IF TG_OP='INSERT' AND NEW.status<>'RUNNING' THEN RAISE EXCEPTION 'Attempt must start RUNNING' USING ERRCODE='23514'; END IF;
 RETURN NEW;
 END $$;

CREATE TABLE social_oauth_states (
 id uuid PRIMARY KEY DEFAULT gen_random_uuid(),tenant_id uuid NOT NULL REFERENCES tenants,
 influencer_id uuid NOT NULL, created_by uuid NOT NULL,
 state_hash text NOT NULL UNIQUE CHECK(state_hash ~ '^[0-9a-f]{64}$'),
 expires_at timestamptz NOT NULL, consumed_at timestamptz,
 created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
 UNIQUE(tenant_id,id), FOREIGN KEY(tenant_id,influencer_id) REFERENCES influencers(tenant_id,id),
 FOREIGN KEY(tenant_id,created_by) REFERENCES tenant_memberships(tenant_id,principal_id),
 CHECK(expires_at>created_at AND expires_at<=created_at+interval '15 minutes')
);
CREATE TABLE private.social_account_ownership (
 account_id text PRIMARY KEY CHECK(account_id ~ '^[0-9]{1,64}$'),
 tenant_id uuid NOT NULL REFERENCES tenants
);
CREATE TABLE social_connections (
 id uuid PRIMARY KEY DEFAULT gen_random_uuid(),tenant_id uuid NOT NULL REFERENCES tenants,
 influencer_id uuid NOT NULL, account_id text NOT NULL CHECK(account_id ~ '^[0-9]{1,64}$'),
 username text NOT NULL CHECK(length(username) BETWEEN 1 AND 200),
 version int NOT NULL CHECK(version>0), api_version text NOT NULL CHECK(api_version ~ '^v[0-9]{1,2}\.0$'),
 scopes jsonb NOT NULL CHECK(jsonb_typeof(scopes)='array'),
 oauth_state_id uuid NOT NULL, created_by uuid NOT NULL,
 created_at timestamptz NOT NULL DEFAULT clock_timestamp(), UNIQUE(tenant_id,id),
 UNIQUE(tenant_id,account_id,version), UNIQUE(tenant_id,id,influencer_id),UNIQUE(tenant_id,id,account_id),
 FOREIGN KEY(tenant_id,influencer_id) REFERENCES influencers(tenant_id,id),
 FOREIGN KEY(tenant_id,oauth_state_id) REFERENCES social_oauth_states(tenant_id,id),
 FOREIGN KEY(tenant_id,created_by) REFERENCES tenant_memberships(tenant_id,principal_id)
);
CREATE TABLE private.social_credentials (
 id uuid PRIMARY KEY DEFAULT gen_random_uuid(),tenant_id uuid NOT NULL REFERENCES tenants,
 connection_id uuid NOT NULL, encrypted_token bytea NOT NULL,
 expires_at timestamptz NOT NULL, created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
 FOREIGN KEY(tenant_id,connection_id) REFERENCES social_connections(tenant_id,id),
 CHECK(expires_at>created_at)
);
CREATE TABLE social_revocations (
 id uuid PRIMARY KEY DEFAULT gen_random_uuid(),tenant_id uuid NOT NULL REFERENCES tenants,
 connection_id uuid NOT NULL,created_by uuid NOT NULL,
 created_at timestamptz NOT NULL DEFAULT clock_timestamp(),UNIQUE(tenant_id,id),
 UNIQUE(tenant_id,connection_id),
 FOREIGN KEY(tenant_id,connection_id) REFERENCES social_connections(tenant_id,id),
 FOREIGN KEY(tenant_id,created_by) REFERENCES tenant_memberships(tenant_id,principal_id)
);
CREATE TABLE social_publish_runs (
 id uuid PRIMARY KEY DEFAULT gen_random_uuid(),tenant_id uuid NOT NULL REFERENCES tenants,
 workflow_run_id uuid NOT NULL,render_run_id uuid NOT NULL,connection_id uuid NOT NULL,account_id text NOT NULL,
 asset_version_id uuid NOT NULL,research_version_id uuid NOT NULL,qa_report_id uuid NOT NULL,
 manifest_hash text NOT NULL,content_approval_record_id uuid NOT NULL,visual_approval_record_id uuid NOT NULL,
 plan jsonb NOT NULL,plan_hash text NOT NULL CHECK(plan_hash=private.visual_hash(plan)),
 idempotency_key text NOT NULL CHECK(length(idempotency_key) BETWEEN 1 AND 128),
 input_hash text NOT NULL CHECK(input_hash ~ '^[0-9a-f]{64}$'),
 status text NOT NULL DEFAULT 'AWAITING_PUBLISH_APPROVAL' CHECK(status IN
 ('AWAITING_PUBLISH_APPROVAL','AUTHORIZED','PREPARING','READY','PUBLISHING','PUBLISHED','REJECTED','BLOCKED','FAILED','UNKNOWN_OUTCOME')),
 post_id text CHECK(post_id ~ '^[0-9]{1,64}$'),published_at timestamptz,
 created_by uuid NOT NULL,created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
 updated_at timestamptz NOT NULL DEFAULT clock_timestamp(),correlation_id uuid NOT NULL,
 UNIQUE(tenant_id,id),UNIQUE(tenant_id,id,workflow_run_id),UNIQUE(tenant_id,idempotency_key),
 FOREIGN KEY(tenant_id,workflow_run_id) REFERENCES workflow_runs(tenant_id,id),
 FOREIGN KEY(tenant_id,connection_id,account_id) REFERENCES social_connections(tenant_id,id,account_id),
 FOREIGN KEY(tenant_id,render_run_id,workflow_run_id,asset_version_id,research_version_id,qa_report_id,manifest_hash)
 REFERENCES render_runs(tenant_id,id,workflow_run_id,asset_version_id,research_version_id,qa_report_id,manifest_hash),
 FOREIGN KEY(tenant_id,content_approval_record_id,workflow_run_id,asset_version_id,research_version_id,qa_report_id)
 REFERENCES approval_records(tenant_id,id,workflow_run_id,asset_version_id,research_version_id,qa_report_id),
 FOREIGN KEY(tenant_id,visual_approval_record_id,render_run_id,workflow_run_id,asset_version_id,research_version_id,
 qa_report_id,content_approval_record_id,manifest_hash)
 REFERENCES visual_approval_records(tenant_id,id,render_run_id,workflow_run_id,asset_version_id,research_version_id,
 qa_report_id,content_approval_record_id,manifest_hash),
 FOREIGN KEY(tenant_id,created_by) REFERENCES tenant_memberships(tenant_id,principal_id),
 CHECK((status='PUBLISHED')=(post_id IS NOT NULL AND published_at IS NOT NULL))
);
CREATE UNIQUE INDEX social_no_duplicate_publish ON social_publish_runs(tenant_id,render_run_id,account_id)
 WHERE status NOT IN ('REJECTED','BLOCKED','FAILED');
CREATE TABLE social_publish_decisions (
 id uuid PRIMARY KEY DEFAULT gen_random_uuid(),tenant_id uuid NOT NULL REFERENCES tenants,
 publish_run_id uuid NOT NULL,plan_hash text NOT NULL,decision text NOT NULL CHECK(decision IN ('AUTHORIZE_PUBLISH','REJECT')),
 created_by uuid NOT NULL,created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
 comment text CHECK(length(comment)<=2000),UNIQUE(tenant_id,id),UNIQUE(tenant_id,publish_run_id),
 FOREIGN KEY(tenant_id,publish_run_id) REFERENCES social_publish_runs(tenant_id,id),
 FOREIGN KEY(tenant_id,created_by) REFERENCES tenant_memberships(tenant_id,principal_id)
);
CREATE TABLE social_publish_jobs (
 id uuid PRIMARY KEY DEFAULT gen_random_uuid(),tenant_id uuid NOT NULL REFERENCES tenants,
 publish_run_id uuid NOT NULL,workflow_run_id uuid NOT NULL,skill_run_id uuid NOT NULL,
 stage text NOT NULL CHECK(stage IN ('CHILD','CONTAINER','POLL','PUBLISH','RECONCILE','INSIGHTS')),
 step_key text NOT NULL CHECK(length(step_key) BETWEEN 1 AND 160),attempt int NOT NULL CHECK(attempt BETWEEN 1 AND 90),
 input_hash text NOT NULL CHECK(input_hash ~ '^[0-9a-f]{64}$'),
 status text NOT NULL DEFAULT 'RUNNING' CHECK(status IN ('RUNNING','SUCCEEDED','FAILED','UNKNOWN_OUTCOME')),
 retryable boolean NOT NULL DEFAULT false,retry_at timestamptz,
 error_category text CHECK(error_category ~ '^[A-Z][A-Z0-9_]{0,79}$'),
 result jsonb,started_at timestamptz NOT NULL DEFAULT clock_timestamp(),ended_at timestamptz,
 created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
 UNIQUE(tenant_id,id),UNIQUE(tenant_id,publish_run_id,step_key,attempt),
 FOREIGN KEY(tenant_id,publish_run_id,workflow_run_id) REFERENCES social_publish_runs(tenant_id,id,workflow_run_id),
 FOREIGN KEY(tenant_id,skill_run_id,workflow_run_id) REFERENCES skill_runs(tenant_id,id,workflow_run_id),
 CHECK((status='RUNNING')=(ended_at IS NULL))
);
CREATE TABLE social_reconcile_requests (
 id uuid PRIMARY KEY DEFAULT gen_random_uuid(),tenant_id uuid NOT NULL REFERENCES tenants,
 publish_run_id uuid NOT NULL,candidate_media_id text NOT NULL CHECK(candidate_media_id ~ '^[0-9]{1,64}$'),
 input_hash text NOT NULL CHECK(input_hash ~ '^[0-9a-f]{64}$'),created_by uuid NOT NULL,
 comment text CHECK(length(comment)<=2000),created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
 UNIQUE(tenant_id,id),UNIQUE(tenant_id,publish_run_id,input_hash),
 FOREIGN KEY(tenant_id,publish_run_id) REFERENCES social_publish_runs(tenant_id,id),
 FOREIGN KEY(tenant_id,created_by) REFERENCES tenant_memberships(tenant_id,principal_id)
);
CREATE UNIQUE INDEX social_one_completed_step ON social_publish_jobs(tenant_id,publish_run_id,step_key) WHERE status='SUCCEEDED';
CREATE TABLE social_insight_snapshots (
 id uuid PRIMARY KEY DEFAULT gen_random_uuid(),tenant_id uuid NOT NULL REFERENCES tenants,
 publish_run_id uuid NOT NULL,workflow_run_id uuid NOT NULL,job_id uuid NOT NULL,
 payload jsonb NOT NULL,content_hash text NOT NULL CHECK(content_hash=private.visual_hash(payload)),
 captured_at timestamptz NOT NULL,created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
 UNIQUE(tenant_id,id),UNIQUE(tenant_id,job_id),
 FOREIGN KEY(tenant_id,publish_run_id,workflow_run_id) REFERENCES social_publish_runs(tenant_id,id,workflow_run_id),
 FOREIGN KEY(tenant_id,job_id) REFERENCES social_publish_jobs(tenant_id,id)
);
CREATE TABLE social_webhook_events (
 id uuid PRIMARY KEY DEFAULT gen_random_uuid(),tenant_id uuid NOT NULL REFERENCES tenants,
 connection_id uuid NOT NULL,event_key text NOT NULL,payload jsonb NOT NULL,
 content_hash text NOT NULL CHECK(content_hash=private.visual_hash(payload)),
 captured_at timestamptz NOT NULL DEFAULT clock_timestamp(),
 UNIQUE(tenant_id,id),UNIQUE(tenant_id,event_key),
 FOREIGN KEY(tenant_id,connection_id) REFERENCES social_connections(tenant_id,id)
);
DO $$ DECLARE t text; BEGIN
 FOREACH t IN ARRAY ARRAY['social_oauth_states','social_connections','social_revocations','social_publish_runs',
 'social_publish_decisions','social_publish_jobs','social_insight_snapshots','social_webhook_events','social_reconcile_requests'] LOOP
 EXECUTE format('ALTER TABLE %I ENABLE ROW LEVEL SECURITY',t);
 EXECUTE format('ALTER TABLE %I FORCE ROW LEVEL SECURITY',t);
 EXECUTE format('CREATE POLICY tenant_boundary ON %I USING (tenant_id=public.context_tenant()) WITH CHECK (tenant_id=public.context_tenant())',t);
 EXECUTE format('REVOKE ALL ON %I FROM PUBLIC,mediaos_runtime',t);
 EXECUTE format('GRANT SELECT ON %I TO mediaos_runtime',t);
 IF t NOT IN ('social_oauth_states','social_publish_runs','social_publish_jobs') THEN
 EXECUTE format('CREATE TRIGGER immutable BEFORE UPDATE OR DELETE ON %I FOR EACH ROW EXECUTE FUNCTION immutable_record()',t);
 END IF;
 END LOOP;
 FOREACH t IN ARRAY ARRAY['social_account_ownership','social_credentials'] LOOP
 EXECUTE format('ALTER TABLE private.%I ENABLE ROW LEVEL SECURITY',t);
 EXECUTE format('ALTER TABLE private.%I FORCE ROW LEVEL SECURITY',t);
 EXECUTE format('CREATE POLICY tenant_boundary ON private.%I USING (tenant_id=public.context_tenant()) WITH CHECK (tenant_id=public.context_tenant())',t);
 EXECUTE format('REVOKE ALL ON private.%I FROM PUBLIC,mediaos_runtime',t);
 EXECUTE format('CREATE TRIGGER immutable BEFORE UPDATE OR DELETE ON private.%I FOR EACH ROW EXECUTE FUNCTION immutable_record()',t);
 END LOOP; END $$;

CREATE FUNCTION public.social_begin_oauth(influencer uuid,statehash text) RETURNS uuid
 LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,public AS $$
 DECLARE result uuid; BEGIN
 IF NOT public.has_role('ADMIN') THEN RAISE EXCEPTION 'Admin required' USING ERRCODE='42501'; END IF;
 INSERT INTO public.social_oauth_states(tenant_id,influencer_id,created_by,state_hash,expires_at)
 VALUES(public.context_tenant(),influencer,public.context_principal(),statehash,clock_timestamp()+interval '10 minutes')
 RETURNING id INTO result; RETURN result; END $$;
CREATE FUNCTION public.social_consume_oauth(statehash text) RETURNS uuid
 LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,public AS $$
 DECLARE s public.social_oauth_states; BEGIN
 IF NOT public.has_role('SOCIAL') THEN RAISE EXCEPTION 'Connector required' USING ERRCODE='42501'; END IF;
 SELECT * INTO s FROM public.social_oauth_states WHERE tenant_id=public.context_tenant() AND state_hash=statehash FOR UPDATE;
 IF s.id IS NULL OR s.consumed_at IS NOT NULL OR s.expires_at<=clock_timestamp()
 OR NOT EXISTS(SELECT 1 FROM public.tenant_memberships m JOIN public.principals p ON p.id=m.principal_id
 WHERE m.tenant_id=s.tenant_id AND p.id=s.created_by AND p.active AND 'ADMIN'=ANY(m.roles)) THEN
 RAISE EXCEPTION 'Expired or invalid OAuth state' USING ERRCODE='23514'; END IF;
 UPDATE public.social_oauth_states SET consumed_at=clock_timestamp() WHERE id=s.id;
 RETURN s.id; END $$;
CREATE FUNCTION public.social_finish_oauth(stateid uuid,account text,uname text,apiversion text,
 permissions jsonb,access_token text,expiry timestamptz,vault_key text) RETURNS uuid
 LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,public AS $$
 DECLARE s public.social_oauth_states; result uuid; rev int; BEGIN
 IF NOT public.has_role('SOCIAL') THEN RAISE EXCEPTION 'Connector required' USING ERRCODE='42501'; END IF;
 IF vault_key IS NULL OR length(vault_key)<32 OR access_token IS NULL OR length(access_token) NOT BETWEEN 16 AND 8192
 OR expiry IS NULL OR expiry<=clock_timestamp()
 OR (permissions @> '["instagram_business_basic","instagram_business_content_publish"]'::jsonb) IS DISTINCT FROM true THEN
 RAISE EXCEPTION 'Valid encrypted token and permissions required' USING ERRCODE='23514'; END IF;
 SELECT * INTO s FROM public.social_oauth_states WHERE tenant_id=public.context_tenant() AND id=stateid FOR UPDATE;
 IF s.id IS NULL OR s.consumed_at IS NULL OR s.expires_at<=clock_timestamp()
 OR EXISTS(SELECT 1 FROM public.social_connections WHERE tenant_id=s.tenant_id AND oauth_state_id=s.id)
 OR NOT EXISTS(SELECT 1 FROM public.tenant_memberships m JOIN public.principals p ON p.id=m.principal_id
 WHERE m.tenant_id=s.tenant_id AND p.id=s.created_by AND p.active AND 'ADMIN'=ANY(m.roles)) THEN
 RAISE EXCEPTION 'Consumed OAuth attempt required' USING ERRCODE='23514'; END IF;
 PERFORM pg_advisory_xact_lock(hashtextextended('social-account:'||account,0));
 IF EXISTS(SELECT 1 FROM private.social_account_ownership WHERE account_id=account AND tenant_id<>s.tenant_id) THEN
 RAISE EXCEPTION 'Account already owned' USING ERRCODE='42501'; END IF;
 INSERT INTO private.social_account_ownership VALUES(account,s.tenant_id) ON CONFLICT DO NOTHING;
 IF NOT EXISTS(SELECT 1 FROM private.social_account_ownership WHERE account_id=account AND tenant_id=s.tenant_id) THEN
 RAISE EXCEPTION 'Account belongs to another tenant' USING ERRCODE='42501'; END IF;
 SELECT coalesce(max(version),0)+1 INTO rev FROM public.social_connections WHERE tenant_id=s.tenant_id AND account_id=account;
 INSERT INTO public.social_connections(tenant_id,influencer_id,account_id,username,version,api_version,scopes,oauth_state_id,created_by)
 VALUES(s.tenant_id,s.influencer_id,account,uname,rev,apiversion,permissions,s.id,s.created_by) RETURNING id INTO result;
 INSERT INTO private.social_credentials(tenant_id,connection_id,encrypted_token,expires_at)
 VALUES(s.tenant_id,result,public.pgp_sym_encrypt(access_token,vault_key,'cipher-algo=aes256'),expiry);
 RETURN result; END $$;
CREATE FUNCTION private.social_connection_current(cid uuid) RETURNS void
 LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,public AS $$
 DECLARE c public.social_connections; BEGIN
 SELECT * INTO c FROM public.social_connections WHERE tenant_id=public.context_tenant() AND id=cid;
 IF c.id IS NULL THEN RAISE EXCEPTION 'Connection not found' USING ERRCODE='P0002'; END IF;
 PERFORM pg_advisory_xact_lock(hashtextextended('social-account:'||c.account_id,0));
 IF EXISTS(SELECT 1 FROM public.social_revocations WHERE tenant_id=c.tenant_id AND connection_id=c.id)
 OR EXISTS(SELECT 1 FROM public.social_connections WHERE tenant_id=c.tenant_id AND account_id=c.account_id AND version>c.version)
 OR NOT EXISTS(SELECT 1 FROM private.social_credentials WHERE tenant_id=c.tenant_id AND connection_id=c.id AND expires_at>clock_timestamp()) THEN
 RAISE EXCEPTION 'Connection revoked, superseded or expired' USING ERRCODE='23514'; END IF;
 END $$;
CREATE FUNCTION public.social_token(cid uuid,vault_key text) RETURNS text
 LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,public AS $$
 DECLARE result text; BEGIN
 IF NOT public.has_role('SOCIAL') THEN RAISE EXCEPTION 'Connector required' USING ERRCODE='42501'; END IF;
 PERFORM private.social_connection_current(cid);
 SELECT public.pgp_sym_decrypt(encrypted_token,vault_key) INTO result FROM private.social_credentials
 WHERE tenant_id=public.context_tenant() AND connection_id=cid ORDER BY created_at DESC,id DESC LIMIT 1;
 IF result IS NULL THEN RAISE EXCEPTION 'Credential missing' USING ERRCODE='23514'; END IF;
 RETURN result; END $$;
CREATE FUNCTION public.social_rotate_token(cid uuid,access_token text,expiry timestamptz,vault_key text) RETURNS void
 LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,public AS $$ BEGIN
 IF NOT public.has_role('SOCIAL') THEN RAISE EXCEPTION 'Connector required' USING ERRCODE='42501'; END IF;
 PERFORM private.social_connection_current(cid);
 IF vault_key IS NULL OR length(vault_key)<32 OR access_token IS NULL OR length(access_token) NOT BETWEEN 16 AND 8192
 OR expiry IS NULL OR expiry<=clock_timestamp() THEN
 RAISE EXCEPTION 'Valid token required' USING ERRCODE='23514'; END IF;
 INSERT INTO private.social_credentials(tenant_id,connection_id,encrypted_token,expires_at)
 VALUES(public.context_tenant(),cid,public.pgp_sym_encrypt(access_token,vault_key,'cipher-algo=aes256'),expiry); END $$;
CREATE FUNCTION public.social_revoke(cid uuid) RETURNS void
 LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,public AS $$
 DECLARE c public.social_connections; BEGIN
 IF NOT public.has_role('ADMIN') THEN RAISE EXCEPTION 'Admin required' USING ERRCODE='42501'; END IF;
 SELECT * INTO c FROM public.social_connections WHERE tenant_id=public.context_tenant() AND id=cid;
 IF c.id IS NULL THEN RAISE EXCEPTION 'Connection not found' USING ERRCODE='P0002'; END IF;
 PERFORM pg_advisory_xact_lock(hashtextextended('social-account:'||c.account_id,0));
 INSERT INTO public.social_revocations(tenant_id,connection_id,created_by)
 VALUES(c.tenant_id,c.id,public.context_principal()) ON CONFLICT DO NOTHING; END $$;

CREATE FUNCTION private.social_audit(r public.social_publish_runs,event text,extra jsonb DEFAULT '{}'::jsonb) RETURNS void
 LANGUAGE sql SECURITY DEFINER SET search_path=pg_catalog,public AS $$
 INSERT INTO public.audit_events(tenant_id,workflow_run_id,actor_id,event_type,correlation_id,details)
 VALUES(r.tenant_id,r.workflow_run_id,public.context_principal(),event,r.correlation_id,
 jsonb_build_object('publish_run_id',r.id,'connection_id',r.connection_id,'status',r.status)||extra) $$;
CREATE FUNCTION private.social_publish_current(r public.social_publish_runs) RETURNS void
 LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,public AS $$ BEGIN
 IF r.tenant_id IS DISTINCT FROM public.context_tenant() THEN RAISE EXCEPTION 'Owned publish run required' USING ERRCODE='42501'; END IF;
 PERFORM public.check_render_export(r.render_run_id);
 PERFORM private.social_connection_current(r.connection_id);
 IF NOT EXISTS(SELECT 1 FROM public.social_connections c JOIN public.workflow_runs w
 ON w.tenant_id=c.tenant_id AND w.influencer_id=c.influencer_id
 WHERE c.tenant_id=r.tenant_id AND c.id=r.connection_id AND w.id=r.workflow_run_id) THEN
 RAISE EXCEPTION 'Account influencer mismatch' USING ERRCODE='23514'; END IF;
 END $$;
CREATE FUNCTION public.social_start_publish(rid uuid,cid uuid,p jsonb,key text,inputhash text) RETURNS uuid
 LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,public AS $$
 DECLARE r public.render_runs; w public.workflow_runs; a public.visual_approval_records;
 result public.social_publish_runs; slide jsonb; expected jsonb; n int:=0; BEGIN
 IF NOT public.has_role('OPERATOR') THEN RAISE EXCEPTION 'Operator required' USING ERRCODE='42501'; END IF;
 expected:=jsonb_build_object('render_run_id',rid,'connection_id',cid);
 IF inputhash IS DISTINCT FROM private.visual_hash(expected) OR length(key) NOT BETWEEN 1 AND 128 THEN
 RAISE EXCEPTION 'Invalid idempotency input' USING ERRCODE='23514'; END IF;
 PERFORM pg_advisory_xact_lock(hashtextextended('social-key:'||public.context_tenant()::text||':'||key,0));
 SELECT * INTO result FROM public.social_publish_runs WHERE tenant_id=public.context_tenant() AND idempotency_key=key;
 IF result.id IS NOT NULL THEN
 IF result.input_hash<>inputhash THEN RAISE EXCEPTION 'Publish key conflict' USING ERRCODE='23505'; END IF;
 RETURN result.id; END IF;
 SELECT * INTO r FROM public.render_runs WHERE tenant_id=public.context_tenant() AND id=rid;
 SELECT * INTO w FROM public.workflow_runs WHERE tenant_id=public.context_tenant() AND id=r.workflow_run_id;
 PERFORM public.check_render_export(rid);
 PERFORM private.social_connection_current(cid);
 IF NOT EXISTS(SELECT 1 FROM public.social_connections WHERE tenant_id=w.tenant_id AND id=cid AND influencer_id=w.influencer_id) THEN
 RAISE EXCEPTION 'Account influencer mismatch' USING ERRCODE='23514'; END IF;
 PERFORM private.conversion_object(p,ARRAY['schema_version','connection_id','render_run_id','manifest_hash','caption','slides']);
 IF p->'schema_version' IS DISTINCT FROM '1'::jsonb OR p->>'connection_id' IS DISTINCT FROM cid::text OR p->>'render_run_id' IS DISTINCT FROM rid::text
 OR p->>'manifest_hash' IS DISTINCT FROM r.manifest_hash OR p->>'caption' IS DISTINCT FROM r.manifest->'caption'->>'text'
 OR length(p->>'caption') NOT BETWEEN 1 AND 2200 OR jsonb_typeof(p->'slides')<>'array'
 OR jsonb_array_length(p->'slides') NOT BETWEEN 1 AND 10
 OR jsonb_array_length(p->'slides')<>jsonb_array_length(r.manifest->'slides') THEN
 RAISE EXCEPTION 'Exact Instagram plan required' USING ERRCODE='23514'; END IF;
 FOR slide IN SELECT value FROM jsonb_array_elements(p->'slides') LOOP
 n:=n+1;
 PERFORM private.conversion_object(slide,ARRAY['index','sha256','source_sha256','width','height','mime_type']);
 IF slide->'index' IS DISTINCT FROM to_jsonb(n) OR slide->>'sha256' IS NULL OR (slide->>'sha256')!~'^[0-9a-f]{64}$'
 OR slide->>'source_sha256' IS DISTINCT FROM r.manifest->'slides'->(n-1)->>'sha256'
 OR slide->'width' IS DISTINCT FROM '1080'::jsonb OR slide->'height' IS DISTINCT FROM '1350'::jsonb OR slide->>'mime_type' IS DISTINCT FROM 'image/jpeg' THEN
 RAISE EXCEPTION 'Exact JPEG slide required' USING ERRCODE='23514'; END IF;
 END LOOP;
 SELECT * INTO a FROM public.visual_approval_records WHERE tenant_id=w.tenant_id AND render_run_id=rid AND decision='APPROVE'
 ORDER BY created_at DESC,id DESC LIMIT 1;
 INSERT INTO public.social_publish_runs(tenant_id,workflow_run_id,render_run_id,connection_id,account_id,asset_version_id,research_version_id,
 qa_report_id,manifest_hash,content_approval_record_id,visual_approval_record_id,plan,plan_hash,idempotency_key,input_hash,created_by,correlation_id)
 VALUES(w.tenant_id,w.id,rid,cid,(SELECT account_id FROM public.social_connections WHERE id=cid AND tenant_id=w.tenant_id),r.asset_version_id,r.research_version_id,r.qa_report_id,r.manifest_hash,
 a.content_approval_record_id,a.id,p,private.visual_hash(p),key,inputhash,public.context_principal(),w.correlation_id) RETURNING * INTO result;
 PERFORM private.social_audit(result,'SOCIAL_PUBLISH_CREATED'); RETURN result.id; END $$;
CREATE FUNCTION public.social_decide_publish(pid uuid,p jsonb) RETURNS uuid
 LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,public AS $$
 DECLARE r public.social_publish_runs; result uuid; BEGIN
 IF NOT public.has_role('APPROVER') THEN RAISE EXCEPTION 'Approver required' USING ERRCODE='42501'; END IF;
 SELECT * INTO r FROM public.social_publish_runs WHERE tenant_id=public.context_tenant() AND id=pid;
 IF r.id IS NULL THEN RAISE EXCEPTION 'Publish run not found' USING ERRCODE='P0002'; END IF;
 PERFORM private.social_publish_current(r);
 SELECT * INTO r FROM public.social_publish_runs WHERE tenant_id=r.tenant_id AND id=r.id FOR UPDATE;
 PERFORM private.conversion_object(p,ARRAY['plan_hash','decision','reviewed_images','reviewed_caption','confirmed_account','comment']);
 IF r.status<>'AWAITING_PUBLISH_APPROVAL' OR p->>'plan_hash' IS DISTINCT FROM r.plan_hash
 OR p->>'decision' IS NULL OR p->>'decision' NOT IN ('AUTHORIZE_PUBLISH','REJECT')
 OR (p->>'decision'='AUTHORIZE_PUBLISH' AND (p->'reviewed_images' IS DISTINCT FROM 'true'::jsonb
 OR p->'reviewed_caption' IS DISTINCT FROM 'true'::jsonb OR p->'confirmed_account' IS DISTINCT FROM 'true'::jsonb)) THEN
 RAISE EXCEPTION 'Exact reviewed publish plan required' USING ERRCODE='23514'; END IF;
 INSERT INTO public.social_publish_decisions(tenant_id,publish_run_id,plan_hash,decision,created_by,comment)
 VALUES(r.tenant_id,r.id,r.plan_hash,p->>'decision',public.context_principal(),p->>'comment') RETURNING id INTO result;
 UPDATE public.social_publish_runs SET status=CASE WHEN p->>'decision'='AUTHORIZE_PUBLISH' THEN 'AUTHORIZED' ELSE 'REJECTED' END,
 updated_at=clock_timestamp() WHERE id=r.id RETURNING * INTO r;
 PERFORM private.social_audit(r,'SOCIAL_PUBLISH_DECISION'); RETURN result; END $$;

CREATE FUNCTION public.social_request_reconciliation(pid uuid,p jsonb) RETURNS uuid
 LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,public AS $$
 DECLARE r public.social_publish_runs; result uuid; BEGIN
 IF NOT public.has_role('APPROVER') THEN RAISE EXCEPTION 'Approver required' USING ERRCODE='42501'; END IF;
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

CREATE FUNCTION public.social_reserve_job(pid uuid,stage_name text,step text,inputhash text) RETURNS uuid
 LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,public AS $$
 DECLARE r public.social_publish_runs; j public.social_publish_jobs; sid uuid; result uuid; attempts int; BEGIN
 IF NOT public.has_role('SOCIAL') THEN RAISE EXCEPTION 'Connector required' USING ERRCODE='42501'; END IF;
 SELECT * INTO r FROM public.social_publish_runs WHERE tenant_id=public.context_tenant() AND id=pid;
 IF r.id IS NULL THEN RAISE EXCEPTION 'Publish run not found' USING ERRCODE='P0002'; END IF;
 IF stage_name NOT IN ('INSIGHTS','RECONCILE') THEN PERFORM private.social_publish_current(r);
 ELSE PERFORM private.social_connection_current(r.connection_id); END IF;
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

-- Hold the existing content/source/configuration locks over the bounded final
-- dispatch call. This closes the guard-to-side-effect race with source revisions.
CREATE FUNCTION public.social_guard_dispatch(jobid uuid) RETURNS void
 LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,public AS $$
 DECLARE j public.social_publish_jobs; r public.social_publish_runs; BEGIN
 IF NOT public.has_role('SOCIAL') THEN RAISE EXCEPTION 'Connector required' USING ERRCODE='42501'; END IF;
 SELECT * INTO j FROM public.social_publish_jobs WHERE tenant_id=public.context_tenant() AND id=jobid;
 SELECT * INTO r FROM public.social_publish_runs WHERE tenant_id=public.context_tenant() AND id=j.publish_run_id;
 IF j.id IS NULL OR j.status<>'RUNNING' OR j.stage<>'PUBLISH' OR r.status<>'PUBLISHING' THEN
 RAISE EXCEPTION 'Reserved publish job required' USING ERRCODE='23514'; END IF;
 PERFORM private.social_publish_current(r); END $$;

CREATE FUNCTION public.social_finish_job(jobid uuid,p jsonb) RETURNS void
 LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,public AS $$
 DECLARE j public.social_publish_jobs; r public.social_publish_runs; nextstate text; BEGIN
 IF NOT public.has_role('SOCIAL') THEN RAISE EXCEPTION 'Connector required' USING ERRCODE='42501'; END IF;
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
CREATE FUNCTION public.social_fail_job(jobid uuid,category text,retry boolean,unknown boolean,delay_seconds int DEFAULT 0) RETURNS void
 LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,public AS $$
 DECLARE j public.social_publish_jobs; r public.social_publish_runs; can_retry boolean; BEGIN
 IF NOT public.has_role('SOCIAL') THEN RAISE EXCEPTION 'Connector required' USING ERRCODE='42501'; END IF;
 SELECT * INTO j FROM public.social_publish_jobs WHERE tenant_id=public.context_tenant() AND id=jobid FOR UPDATE;
 SELECT * INTO r FROM public.social_publish_runs WHERE tenant_id=public.context_tenant() AND id=j.publish_run_id FOR UPDATE;
 IF j.id IS NULL OR j.status<>'RUNNING' OR category IS NULL OR category!~'^[A-Z][A-Z0-9_]{0,79}$'
 OR delay_seconds IS NULL OR delay_seconds NOT BETWEEN 0 AND 86400 OR retry IS NULL OR unknown IS NULL THEN
 RAISE EXCEPTION 'Valid active failure required' USING ERRCODE='23514'; END IF;
 -- Retryable POST errors must be a definite refusal (e.g. rate limiting), never an uncertain side effect.
 can_retry:=retry AND NOT unknown AND j.attempt<3;
 UPDATE public.social_publish_jobs SET status=CASE WHEN unknown THEN 'UNKNOWN_OUTCOME' ELSE 'FAILED' END,
 error_category=category,retryable=can_retry,retry_at=CASE WHEN can_retry THEN clock_timestamp()+
 make_interval(secs=>greatest(delay_seconds,power(2,j.attempt)::int)) ELSE NULL END,ended_at=clock_timestamp() WHERE id=j.id;
 UPDATE public.skill_runs SET status='FAILED',error_category=category,retryable=can_retry,
 retry_at=CASE WHEN can_retry THEN clock_timestamp()+make_interval(secs=>greatest(delay_seconds,power(2,j.attempt)::int)) ELSE NULL END,
 ended_at=clock_timestamp(),latency_ms=extract(epoch FROM clock_timestamp()-j.started_at)*1000 WHERE id=j.skill_run_id;
 IF j.stage NOT IN ('INSIGHTS','RECONCILE') THEN
 UPDATE public.social_publish_runs SET status=CASE WHEN unknown THEN 'UNKNOWN_OUTCOME'
 WHEN can_retry AND j.stage='PUBLISH' THEN 'READY' WHEN can_retry THEN 'PREPARING'
 WHEN category='POLICY_BLOCKED' THEN 'BLOCKED' ELSE 'FAILED' END,updated_at=clock_timestamp()
 WHERE id=r.id RETURNING * INTO r; END IF;
 PERFORM private.social_audit(r,'SOCIAL_JOB_FAILED',jsonb_build_object('job_id',j.id,'stage',j.stage,'error_category',category));
 END $$;
CREATE FUNCTION public.social_record_webhook(cid uuid,eventkey text,p jsonb) RETURNS uuid
 LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,public AS $$
 DECLARE result uuid; BEGIN
 IF NOT public.has_role('SOCIAL') THEN RAISE EXCEPTION 'Connector required' USING ERRCODE='42501'; END IF;
 PERFORM private.social_connection_current(cid);
 IF p IS NULL OR jsonb_typeof(p)<>'object' OR octet_length(p::text)>131072 OR length(eventkey) NOT BETWEEN 1 AND 200 THEN
 RAISE EXCEPTION 'Bounded verified webhook required' USING ERRCODE='23514'; END IF;
 INSERT INTO public.social_webhook_events(tenant_id,connection_id,event_key,payload,content_hash)
 VALUES(public.context_tenant(),cid,eventkey,p,private.visual_hash(p)) ON CONFLICT DO NOTHING RETURNING id INTO result;
 IF result IS NULL THEN SELECT id INTO result FROM public.social_webhook_events
 WHERE tenant_id=public.context_tenant() AND event_key=eventkey AND content_hash=private.visual_hash(p); END IF;
 IF result IS NULL THEN RAISE EXCEPTION 'Webhook identity conflict' USING ERRCODE='23505'; END IF;
 RETURN result; END $$;
DO $$ DECLARE f record; BEGIN
 FOR f IN SELECT p.oid::regprocedure AS signature,n.nspname FROM pg_proc p JOIN pg_namespace n ON n.oid=p.pronamespace
 WHERE n.nspname IN ('public','private') AND p.proname LIKE 'social_%' LOOP
 EXECUTE format('REVOKE ALL ON FUNCTION %s FROM PUBLIC,mediaos_runtime',f.signature);
 IF f.nspname='public' THEN EXECUTE format('GRANT EXECUTE ON FUNCTION %s TO mediaos_runtime',f.signature); END IF;
 END LOOP; END $$;
