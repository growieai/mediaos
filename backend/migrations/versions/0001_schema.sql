-- Frozen initial schema. Application migrations never connect to another product's database.
CREATE EXTENSION IF NOT EXISTS pgcrypto;
REVOKE CREATE ON SCHEMA public FROM PUBLIC;
CREATE SCHEMA private;
REVOKE ALL ON SCHEMA private FROM PUBLIC;
DO $$ BEGIN
 IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname='mediaos_runtime') THEN
   CREATE ROLE mediaos_runtime LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE NOINHERIT NOBYPASSRLS;
 END IF;
END $$;
ALTER ROLE mediaos_runtime NOSUPERUSER NOCREATEDB NOCREATEROLE NOINHERIT NOBYPASSRLS;
CREATE TABLE tenants (id uuid PRIMARY KEY DEFAULT gen_random_uuid(), slug text NOT NULL UNIQUE,
 name text NOT NULL, created_at timestamptz NOT NULL DEFAULT now());
CREATE TABLE principals (id uuid PRIMARY KEY DEFAULT gen_random_uuid(), name text NOT NULL,
 token_hash text NOT NULL UNIQUE, active boolean NOT NULL DEFAULT true, created_at timestamptz NOT NULL DEFAULT now());
CREATE TABLE tenant_memberships (tenant_id uuid NOT NULL REFERENCES tenants, principal_id uuid NOT NULL REFERENCES principals,
 roles text[] NOT NULL CHECK (roles <@ ARRAY['OPERATOR','APPROVER','ADMIN']::text[] AND cardinality(roles)>0),
 PRIMARY KEY(tenant_id,principal_id));
CREATE TABLE private.request_context (pid int PRIMARY KEY, tx bigint NOT NULL, tenant_id uuid NOT NULL, principal_id uuid NOT NULL);
CREATE FUNCTION public.context_tenant() RETURNS uuid LANGUAGE sql STABLE SECURITY DEFINER
 SET search_path=pg_catalog,public AS $$ SELECT tenant_id FROM private.request_context WHERE pid=pg_backend_pid() AND tx=txid_current() $$;
CREATE FUNCTION public.context_principal() RETURNS uuid LANGUAGE sql STABLE SECURITY DEFINER
 SET search_path=pg_catalog,public AS $$ SELECT principal_id FROM private.request_context WHERE pid=pg_backend_pid() AND tx=txid_current() $$;
CREATE FUNCTION public.has_role(required text) RETURNS boolean LANGUAGE sql STABLE SECURITY DEFINER
 SET search_path=pg_catalog,public AS $$
 SELECT coalesce((SELECT required=ANY(roles) OR 'ADMIN'=ANY(roles) FROM public.tenant_memberships
 WHERE tenant_id=public.context_tenant() AND principal_id=public.context_principal()),false) $$;
CREATE FUNCTION public.authenticate(token text, tenant uuid) RETURNS uuid LANGUAGE plpgsql SECURITY DEFINER
 SET search_path=pg_catalog,public AS $$
 DECLARE p uuid;
 BEGIN
 SELECT x.id INTO p FROM public.principals x JOIN public.tenant_memberships m ON m.principal_id=x.id
 WHERE x.active AND x.token_hash=encode(public.digest(token,'sha256'),'hex') AND m.tenant_id=tenant;
 IF p IS NULL THEN RAISE EXCEPTION 'Unauthenticated or tenant membership denied' USING ERRCODE='28000'; END IF;
 DELETE FROM private.request_context c WHERE NOT EXISTS(SELECT 1 FROM pg_stat_activity a WHERE a.pid=c.pid);
 INSERT INTO private.request_context VALUES(pg_backend_pid(),txid_current(),tenant,p)
 ON CONFLICT(pid) DO UPDATE SET tx=excluded.tx,tenant_id=excluded.tenant_id,principal_id=excluded.principal_id;
 RETURN p;
 END $$;

CREATE TABLE influencers (id uuid PRIMARY KEY DEFAULT gen_random_uuid(), tenant_id uuid NOT NULL REFERENCES tenants(id), created_at timestamptz NOT NULL DEFAULT now(), UNIQUE(tenant_id,id), slug text NOT NULL, name text NOT NULL, UNIQUE(tenant_id,slug));

CREATE TABLE character_config_versions (id uuid PRIMARY KEY DEFAULT gen_random_uuid(), tenant_id uuid NOT NULL REFERENCES tenants(id), created_at timestamptz NOT NULL DEFAULT now(), UNIQUE(tenant_id,id), influencer_id uuid NOT NULL, version int NOT NULL CHECK(version>0), schema_version int NOT NULL CHECK(schema_version=1), payload jsonb NOT NULL, content_hash text NOT NULL, UNIQUE(tenant_id,influencer_id,version), UNIQUE(tenant_id,id,influencer_id), FOREIGN KEY(tenant_id,influencer_id) REFERENCES influencers(tenant_id,id));

CREATE TABLE influencer_versions (id uuid PRIMARY KEY DEFAULT gen_random_uuid(), tenant_id uuid NOT NULL REFERENCES tenants(id), created_at timestamptz NOT NULL DEFAULT now(), UNIQUE(tenant_id,id), influencer_id uuid NOT NULL, character_config_version_id uuid NOT NULL, version int NOT NULL CHECK(version>0), UNIQUE(tenant_id,influencer_id,version), UNIQUE(tenant_id,id,influencer_id), FOREIGN KEY(tenant_id,influencer_id) REFERENCES influencers(tenant_id,id), FOREIGN KEY(tenant_id,character_config_version_id,influencer_id) REFERENCES character_config_versions(tenant_id,id,influencer_id));

CREATE TABLE missions (id uuid PRIMARY KEY DEFAULT gen_random_uuid(), tenant_id uuid NOT NULL REFERENCES tenants(id), created_at timestamptz NOT NULL DEFAULT now(), UNIQUE(tenant_id,id), influencer_id uuid NOT NULL, name text NOT NULL, objective text NOT NULL, UNIQUE(tenant_id,id,influencer_id), FOREIGN KEY(tenant_id,influencer_id) REFERENCES influencers(tenant_id,id));

CREATE TABLE workflow_runs (id uuid PRIMARY KEY DEFAULT gen_random_uuid(), tenant_id uuid NOT NULL REFERENCES tenants(id), created_at timestamptz NOT NULL DEFAULT now(), UNIQUE(tenant_id,id), influencer_id uuid NOT NULL, influencer_version_id uuid NOT NULL, mission_id uuid NOT NULL,
 idempotency_key text NOT NULL CHECK(length(idempotency_key) BETWEEN 1 AND 128), canonical_input_hash text NOT NULL,
 state text NOT NULL DEFAULT 'CREATED' CHECK(state IN ('CREATED','SOURCE_CAPTURED','RESEARCHING','RESEARCH_COMPLETE','BRIEFING','BRIEF_COMPLETE','CONTENT_GENERATING','CONTENT_COMPLETE','QA_RUNNING','REVISION_REQUIRED','BLOCKED','AWAITING_APPROVAL','APPROVED','FAILED')),
 source_snapshot_id uuid, research_version_id uuid, brief_id uuid, asset_version_id uuid, qa_report_id uuid,
 created_by uuid NOT NULL, updated_at timestamptz NOT NULL DEFAULT now(), correlation_id uuid NOT NULL,
 UNIQUE(tenant_id,idempotency_key),
 FOREIGN KEY(tenant_id,influencer_version_id,influencer_id) REFERENCES influencer_versions(tenant_id,id,influencer_id),
 FOREIGN KEY(tenant_id,mission_id,influencer_id) REFERENCES missions(tenant_id,id,influencer_id),
 FOREIGN KEY(tenant_id,created_by) REFERENCES tenant_memberships(tenant_id,principal_id));

CREATE TABLE source_snapshots (id uuid PRIMARY KEY DEFAULT gen_random_uuid(), tenant_id uuid NOT NULL REFERENCES tenants(id), created_at timestamptz NOT NULL DEFAULT now(), UNIQUE(tenant_id,id), workflow_run_id uuid NOT NULL, source_type text NOT NULL CHECK(source_type IN ('MANUAL','OFFICIAL','SECONDARY')),
 origin text NOT NULL, canonical_url text, title text NOT NULL, publisher text NOT NULL,
 raw_content text NOT NULL CHECK(length(raw_content) BETWEEN 1 AND 100000), captured_at timestamptz NOT NULL,
 submitted_at timestamptz NOT NULL DEFAULT now(), checksum text NOT NULL, classification text NOT NULL,
 verification_status text NOT NULL DEFAULT 'UNVERIFIED' CHECK(verification_status IN ('UNVERIFIED','VERIFIED','REJECTED')),
 metadata jsonb NOT NULL, evidence_input jsonb NOT NULL, created_by uuid NOT NULL, environment text NOT NULL, is_fixture boolean NOT NULL,
 verified_by uuid, verified_at timestamptz, verification_comment text,
 UNIQUE(tenant_id,id,workflow_run_id), UNIQUE(tenant_id,workflow_run_id),
 FOREIGN KEY(tenant_id,workflow_run_id) REFERENCES workflow_runs(tenant_id,id),
 FOREIGN KEY(tenant_id,created_by) REFERENCES tenant_memberships(tenant_id,principal_id),
 FOREIGN KEY(tenant_id,verified_by) REFERENCES tenant_memberships(tenant_id,principal_id));

CREATE TABLE research_packs (id uuid PRIMARY KEY DEFAULT gen_random_uuid(), tenant_id uuid NOT NULL REFERENCES tenants(id), created_at timestamptz NOT NULL DEFAULT now(), UNIQUE(tenant_id,id), workflow_run_id uuid NOT NULL, UNIQUE(tenant_id,id,workflow_run_id), UNIQUE(tenant_id,workflow_run_id),
 FOREIGN KEY(tenant_id,workflow_run_id) REFERENCES workflow_runs(tenant_id,id));

CREATE TABLE research_pack_versions (id uuid PRIMARY KEY DEFAULT gen_random_uuid(), tenant_id uuid NOT NULL REFERENCES tenants(id), created_at timestamptz NOT NULL DEFAULT now(), UNIQUE(tenant_id,id), research_pack_id uuid NOT NULL, workflow_run_id uuid NOT NULL, version int NOT NULL CHECK(version>0),
 schema_version int NOT NULL CHECK(schema_version=1), payload jsonb NOT NULL, content_hash text NOT NULL,
 verification_status text NOT NULL CHECK(verification_status IN ('UNVERIFIED','VERIFIED')),
 UNIQUE(tenant_id,id,workflow_run_id), UNIQUE(tenant_id,research_pack_id,version),
 FOREIGN KEY(tenant_id,research_pack_id,workflow_run_id) REFERENCES research_packs(tenant_id,id,workflow_run_id));

CREATE TABLE research_pack_sources (id uuid PRIMARY KEY DEFAULT gen_random_uuid(), tenant_id uuid NOT NULL REFERENCES tenants(id), created_at timestamptz NOT NULL DEFAULT now(), UNIQUE(tenant_id,id), research_version_id uuid NOT NULL, source_snapshot_id uuid NOT NULL, workflow_run_id uuid NOT NULL,
 UNIQUE(tenant_id,research_version_id,source_snapshot_id),
 FOREIGN KEY(tenant_id,research_version_id,workflow_run_id) REFERENCES research_pack_versions(tenant_id,id,workflow_run_id),
 FOREIGN KEY(tenant_id,source_snapshot_id,workflow_run_id) REFERENCES source_snapshots(tenant_id,id,workflow_run_id));

CREATE TABLE facts (id uuid PRIMARY KEY DEFAULT gen_random_uuid(), tenant_id uuid NOT NULL REFERENCES tenants(id), created_at timestamptz NOT NULL DEFAULT now(), UNIQUE(tenant_id,id), source_snapshot_id uuid NOT NULL, span_start int NOT NULL CHECK(span_start>=0), span_end int NOT NULL CHECK(span_end>span_start),
 statement text NOT NULL CHECK(length(statement)>0), verification_status text NOT NULL CHECK(verification_status IN ('VERIFIED','UNVERIFIED')),
 confidence numeric NOT NULL CHECK(confidence BETWEEN 0 AND 1), fact_type text NOT NULL CHECK(fact_type IN ('GENERAL','GRANT')),
 structured_data jsonb, version int NOT NULL DEFAULT 1 CHECK(version=1), UNIQUE(tenant_id,id,source_snapshot_id),
 FOREIGN KEY(tenant_id,source_snapshot_id) REFERENCES source_snapshots(tenant_id,id));

CREATE TABLE research_pack_facts (id uuid PRIMARY KEY DEFAULT gen_random_uuid(), tenant_id uuid NOT NULL REFERENCES tenants(id), created_at timestamptz NOT NULL DEFAULT now(), UNIQUE(tenant_id,id), research_version_id uuid NOT NULL, fact_id uuid NOT NULL, source_snapshot_id uuid NOT NULL,
 UNIQUE(tenant_id,research_version_id,fact_id),
 FOREIGN KEY(tenant_id,research_version_id,source_snapshot_id) REFERENCES research_pack_sources(tenant_id,research_version_id,source_snapshot_id),
 FOREIGN KEY(tenant_id,fact_id,source_snapshot_id) REFERENCES facts(tenant_id,id,source_snapshot_id));

CREATE TABLE content_briefs (id uuid PRIMARY KEY DEFAULT gen_random_uuid(), tenant_id uuid NOT NULL REFERENCES tenants(id), created_at timestamptz NOT NULL DEFAULT now(), UNIQUE(tenant_id,id), workflow_run_id uuid NOT NULL, research_version_id uuid NOT NULL, influencer_id uuid NOT NULL,
 influencer_version_id uuid NOT NULL, mission_id uuid NOT NULL, schema_version int NOT NULL CHECK(schema_version=1),
 payload jsonb NOT NULL, content_hash text NOT NULL,
 UNIQUE(tenant_id,id,workflow_run_id), UNIQUE(tenant_id,id,research_version_id), UNIQUE(tenant_id,workflow_run_id),
 FOREIGN KEY(tenant_id,research_version_id,workflow_run_id) REFERENCES research_pack_versions(tenant_id,id,workflow_run_id),
 FOREIGN KEY(tenant_id,influencer_version_id,influencer_id) REFERENCES influencer_versions(tenant_id,id,influencer_id),
 FOREIGN KEY(tenant_id,mission_id,influencer_id) REFERENCES missions(tenant_id,id,influencer_id));

CREATE TABLE brief_facts (id uuid PRIMARY KEY DEFAULT gen_random_uuid(), tenant_id uuid NOT NULL REFERENCES tenants(id), created_at timestamptz NOT NULL DEFAULT now(), UNIQUE(tenant_id,id), brief_id uuid NOT NULL, research_version_id uuid NOT NULL, fact_id uuid NOT NULL,
 UNIQUE(tenant_id,brief_id,research_version_id,fact_id),
 FOREIGN KEY(tenant_id,brief_id,research_version_id) REFERENCES content_briefs(tenant_id,id,research_version_id),
 FOREIGN KEY(tenant_id,research_version_id,fact_id) REFERENCES research_pack_facts(tenant_id,research_version_id,fact_id));

CREATE TABLE content_assets (id uuid PRIMARY KEY DEFAULT gen_random_uuid(), tenant_id uuid NOT NULL REFERENCES tenants(id), created_at timestamptz NOT NULL DEFAULT now(), UNIQUE(tenant_id,id), workflow_run_id uuid NOT NULL, type text NOT NULL CHECK(type='CAROUSEL'),
 UNIQUE(tenant_id,id,workflow_run_id), UNIQUE(tenant_id,workflow_run_id),
 FOREIGN KEY(tenant_id,workflow_run_id) REFERENCES workflow_runs(tenant_id,id));

CREATE TABLE content_asset_versions (id uuid PRIMARY KEY DEFAULT gen_random_uuid(), tenant_id uuid NOT NULL REFERENCES tenants(id), created_at timestamptz NOT NULL DEFAULT now(), UNIQUE(tenant_id,id), content_asset_id uuid NOT NULL, workflow_run_id uuid NOT NULL, brief_id uuid NOT NULL, research_version_id uuid NOT NULL,
 version int NOT NULL CHECK(version>0), schema_version int NOT NULL CHECK(schema_version=1), payload jsonb NOT NULL, content_hash text NOT NULL,
 UNIQUE(tenant_id,content_asset_id,version), UNIQUE(tenant_id,id,workflow_run_id),
 UNIQUE(tenant_id,id,brief_id,research_version_id), UNIQUE(tenant_id,id,workflow_run_id,research_version_id),
 FOREIGN KEY(tenant_id,content_asset_id,workflow_run_id) REFERENCES content_assets(tenant_id,id,workflow_run_id),
 FOREIGN KEY(tenant_id,brief_id,workflow_run_id) REFERENCES content_briefs(tenant_id,id,workflow_run_id),
 FOREIGN KEY(tenant_id,brief_id,research_version_id) REFERENCES content_briefs(tenant_id,id,research_version_id));

CREATE TABLE content_claims (id uuid PRIMARY KEY DEFAULT gen_random_uuid(), tenant_id uuid NOT NULL REFERENCES tenants(id), created_at timestamptz NOT NULL DEFAULT now(), UNIQUE(tenant_id,id), asset_version_id uuid NOT NULL, brief_id uuid NOT NULL, research_version_id uuid NOT NULL, fact_id uuid NOT NULL, field_path text NOT NULL,
 UNIQUE(tenant_id,asset_version_id,field_path,fact_id),
 FOREIGN KEY(tenant_id,asset_version_id,brief_id,research_version_id) REFERENCES content_asset_versions(tenant_id,id,brief_id,research_version_id),
 FOREIGN KEY(tenant_id,brief_id,research_version_id,fact_id) REFERENCES brief_facts(tenant_id,brief_id,research_version_id,fact_id));

CREATE TABLE qa_reports (id uuid PRIMARY KEY DEFAULT gen_random_uuid(), tenant_id uuid NOT NULL REFERENCES tenants(id), created_at timestamptz NOT NULL DEFAULT now(), UNIQUE(tenant_id,id), workflow_run_id uuid NOT NULL, asset_version_id uuid NOT NULL, research_version_id uuid NOT NULL, version int NOT NULL CHECK(version>0),
 schema_version int NOT NULL CHECK(schema_version=1), policy_version text NOT NULL, status text NOT NULL CHECK(status IN ('PASS','REVISION_REQUIRED','BLOCKED')),
 payload jsonb NOT NULL, content_hash text NOT NULL,
 UNIQUE(tenant_id,asset_version_id,version), UNIQUE(tenant_id,id,workflow_run_id),
 UNIQUE(tenant_id,id,workflow_run_id,asset_version_id,research_version_id),
 FOREIGN KEY(tenant_id,asset_version_id,workflow_run_id,research_version_id) REFERENCES content_asset_versions(tenant_id,id,workflow_run_id,research_version_id));

CREATE TABLE approval_records (id uuid PRIMARY KEY DEFAULT gen_random_uuid(), tenant_id uuid NOT NULL REFERENCES tenants(id), created_at timestamptz NOT NULL DEFAULT now(), UNIQUE(tenant_id,id), workflow_run_id uuid NOT NULL, asset_version_id uuid NOT NULL, research_version_id uuid NOT NULL, qa_report_id uuid NOT NULL,
 approver_id uuid NOT NULL, decision text NOT NULL CHECK(decision IN ('APPROVE','REJECT')), comment text,
 UNIQUE(tenant_id,qa_report_id),
 FOREIGN KEY(tenant_id,qa_report_id,workflow_run_id,asset_version_id,research_version_id) REFERENCES qa_reports(tenant_id,id,workflow_run_id,asset_version_id,research_version_id),
 FOREIGN KEY(tenant_id,approver_id) REFERENCES tenant_memberships(tenant_id,principal_id));

CREATE TABLE skill_runs (id uuid PRIMARY KEY DEFAULT gen_random_uuid(), tenant_id uuid NOT NULL REFERENCES tenants(id), created_at timestamptz NOT NULL DEFAULT now(), UNIQUE(tenant_id,id), workflow_run_id uuid NOT NULL, skill_identifier text NOT NULL, skill_version text NOT NULL, input_schema_version int NOT NULL,
 output_schema_version int NOT NULL, prompt_version text, provider text NOT NULL CHECK(provider='mock'), model text NOT NULL, adapter text NOT NULL,
 attempt int NOT NULL CHECK(attempt>0), step_key text NOT NULL, input_hash text NOT NULL,
 started_at timestamptz NOT NULL DEFAULT now(), ended_at timestamptz, latency_ms double precision,
 status text NOT NULL CHECK(status IN ('RUNNING','SUCCEEDED','FAILED','INTERRUPTED')),
 error_category text, retryable boolean NOT NULL DEFAULT false, retry_at timestamptz,
 input_tokens int NOT NULL DEFAULT 0 CHECK(input_tokens>=0), output_tokens int NOT NULL DEFAULT 0 CHECK(output_tokens>=0),
 cost numeric NOT NULL DEFAULT 0 CHECK(cost=0), is_mock boolean NOT NULL CHECK(is_mock), output jsonb,
 research_version_id uuid, brief_id uuid, asset_version_id uuid, qa_report_id uuid,
 UNIQUE(tenant_id,workflow_run_id,step_key,attempt),
 FOREIGN KEY(tenant_id,workflow_run_id) REFERENCES workflow_runs(tenant_id,id),
 FOREIGN KEY(tenant_id,research_version_id,workflow_run_id) REFERENCES research_pack_versions(tenant_id,id,workflow_run_id),
 FOREIGN KEY(tenant_id,brief_id,workflow_run_id) REFERENCES content_briefs(tenant_id,id,workflow_run_id),
 FOREIGN KEY(tenant_id,asset_version_id,workflow_run_id) REFERENCES content_asset_versions(tenant_id,id,workflow_run_id),
 FOREIGN KEY(tenant_id,qa_report_id,workflow_run_id) REFERENCES qa_reports(tenant_id,id,workflow_run_id));

CREATE TABLE audit_events (id uuid PRIMARY KEY DEFAULT gen_random_uuid(), tenant_id uuid NOT NULL REFERENCES tenants(id), created_at timestamptz NOT NULL DEFAULT now(), UNIQUE(tenant_id,id), workflow_run_id uuid NOT NULL, actor_id uuid NOT NULL, event_type text NOT NULL, from_state text, to_state text,
 correlation_id uuid NOT NULL, details jsonb NOT NULL DEFAULT '{}',
 FOREIGN KEY(tenant_id,workflow_run_id) REFERENCES workflow_runs(tenant_id,id),
 FOREIGN KEY(tenant_id,actor_id) REFERENCES tenant_memberships(tenant_id,principal_id));

CREATE TABLE cost_events (id uuid PRIMARY KEY DEFAULT gen_random_uuid(), tenant_id uuid NOT NULL REFERENCES tenants(id), created_at timestamptz NOT NULL DEFAULT now(), UNIQUE(tenant_id,id), workflow_run_id uuid NOT NULL, skill_run_id uuid NOT NULL, provider text NOT NULL CHECK(provider='mock'),
 model text NOT NULL, input_tokens int NOT NULL CHECK(input_tokens=0), output_tokens int NOT NULL CHECK(output_tokens=0),
 cost numeric NOT NULL CHECK(cost=0), currency text NOT NULL CHECK(currency='USD'), price_version text NOT NULL,
 UNIQUE(tenant_id,skill_run_id),
 FOREIGN KEY(tenant_id,workflow_run_id) REFERENCES workflow_runs(tenant_id,id),
 FOREIGN KEY(tenant_id,skill_run_id) REFERENCES skill_runs(tenant_id,id));
ALTER TABLE workflow_runs ADD FOREIGN KEY(tenant_id,source_snapshot_id,id) REFERENCES source_snapshots(tenant_id,id,workflow_run_id);
ALTER TABLE workflow_runs ADD FOREIGN KEY(tenant_id,research_version_id,id) REFERENCES research_pack_versions(tenant_id,id,workflow_run_id);
ALTER TABLE workflow_runs ADD FOREIGN KEY(tenant_id,brief_id,id) REFERENCES content_briefs(tenant_id,id,workflow_run_id);
ALTER TABLE workflow_runs ADD FOREIGN KEY(tenant_id,asset_version_id,id) REFERENCES content_asset_versions(tenant_id,id,workflow_run_id);
ALTER TABLE workflow_runs ADD FOREIGN KEY(tenant_id,qa_report_id,id) REFERENCES qa_reports(tenant_id,id,workflow_run_id);

CREATE UNIQUE INDEX one_success_per_step ON skill_runs(tenant_id,workflow_run_id,step_key) WHERE status='SUCCEEDED';
CREATE INDEX workflow_tenant_state ON workflow_runs(tenant_id,state,created_at);
CREATE INDEX audit_run_time ON audit_events(tenant_id,workflow_run_id,created_at);
CREATE INDEX skills_run_time ON skill_runs(tenant_id,workflow_run_id,started_at);

ALTER TABLE influencers ENABLE ROW LEVEL SECURITY;
ALTER TABLE influencers FORCE ROW LEVEL SECURITY;
CREATE POLICY tenant_boundary ON influencers USING (tenant_id=public.context_tenant()) WITH CHECK(tenant_id=public.context_tenant());
GRANT SELECT ON influencers TO mediaos_runtime;

ALTER TABLE character_config_versions ENABLE ROW LEVEL SECURITY;
ALTER TABLE character_config_versions FORCE ROW LEVEL SECURITY;
CREATE POLICY tenant_boundary ON character_config_versions USING (tenant_id=public.context_tenant()) WITH CHECK(tenant_id=public.context_tenant());
GRANT SELECT ON character_config_versions TO mediaos_runtime;

ALTER TABLE influencer_versions ENABLE ROW LEVEL SECURITY;
ALTER TABLE influencer_versions FORCE ROW LEVEL SECURITY;
CREATE POLICY tenant_boundary ON influencer_versions USING (tenant_id=public.context_tenant()) WITH CHECK(tenant_id=public.context_tenant());
GRANT SELECT ON influencer_versions TO mediaos_runtime;

ALTER TABLE missions ENABLE ROW LEVEL SECURITY;
ALTER TABLE missions FORCE ROW LEVEL SECURITY;
CREATE POLICY tenant_boundary ON missions USING (tenant_id=public.context_tenant()) WITH CHECK(tenant_id=public.context_tenant());
GRANT SELECT ON missions TO mediaos_runtime;

ALTER TABLE workflow_runs ENABLE ROW LEVEL SECURITY;
ALTER TABLE workflow_runs FORCE ROW LEVEL SECURITY;
CREATE POLICY tenant_boundary ON workflow_runs USING (tenant_id=public.context_tenant()) WITH CHECK(tenant_id=public.context_tenant());
GRANT SELECT ON workflow_runs TO mediaos_runtime;

ALTER TABLE source_snapshots ENABLE ROW LEVEL SECURITY;
ALTER TABLE source_snapshots FORCE ROW LEVEL SECURITY;
CREATE POLICY tenant_boundary ON source_snapshots USING (tenant_id=public.context_tenant()) WITH CHECK(tenant_id=public.context_tenant());
GRANT SELECT ON source_snapshots TO mediaos_runtime;

ALTER TABLE research_packs ENABLE ROW LEVEL SECURITY;
ALTER TABLE research_packs FORCE ROW LEVEL SECURITY;
CREATE POLICY tenant_boundary ON research_packs USING (tenant_id=public.context_tenant()) WITH CHECK(tenant_id=public.context_tenant());
GRANT SELECT ON research_packs TO mediaos_runtime;

ALTER TABLE research_pack_versions ENABLE ROW LEVEL SECURITY;
ALTER TABLE research_pack_versions FORCE ROW LEVEL SECURITY;
CREATE POLICY tenant_boundary ON research_pack_versions USING (tenant_id=public.context_tenant()) WITH CHECK(tenant_id=public.context_tenant());
GRANT SELECT ON research_pack_versions TO mediaos_runtime;

ALTER TABLE research_pack_sources ENABLE ROW LEVEL SECURITY;
ALTER TABLE research_pack_sources FORCE ROW LEVEL SECURITY;
CREATE POLICY tenant_boundary ON research_pack_sources USING (tenant_id=public.context_tenant()) WITH CHECK(tenant_id=public.context_tenant());
GRANT SELECT ON research_pack_sources TO mediaos_runtime;

ALTER TABLE facts ENABLE ROW LEVEL SECURITY;
ALTER TABLE facts FORCE ROW LEVEL SECURITY;
CREATE POLICY tenant_boundary ON facts USING (tenant_id=public.context_tenant()) WITH CHECK(tenant_id=public.context_tenant());
GRANT SELECT ON facts TO mediaos_runtime;

ALTER TABLE research_pack_facts ENABLE ROW LEVEL SECURITY;
ALTER TABLE research_pack_facts FORCE ROW LEVEL SECURITY;
CREATE POLICY tenant_boundary ON research_pack_facts USING (tenant_id=public.context_tenant()) WITH CHECK(tenant_id=public.context_tenant());
GRANT SELECT ON research_pack_facts TO mediaos_runtime;

ALTER TABLE content_briefs ENABLE ROW LEVEL SECURITY;
ALTER TABLE content_briefs FORCE ROW LEVEL SECURITY;
CREATE POLICY tenant_boundary ON content_briefs USING (tenant_id=public.context_tenant()) WITH CHECK(tenant_id=public.context_tenant());
GRANT SELECT ON content_briefs TO mediaos_runtime;

ALTER TABLE brief_facts ENABLE ROW LEVEL SECURITY;
ALTER TABLE brief_facts FORCE ROW LEVEL SECURITY;
CREATE POLICY tenant_boundary ON brief_facts USING (tenant_id=public.context_tenant()) WITH CHECK(tenant_id=public.context_tenant());
GRANT SELECT ON brief_facts TO mediaos_runtime;

ALTER TABLE content_assets ENABLE ROW LEVEL SECURITY;
ALTER TABLE content_assets FORCE ROW LEVEL SECURITY;
CREATE POLICY tenant_boundary ON content_assets USING (tenant_id=public.context_tenant()) WITH CHECK(tenant_id=public.context_tenant());
GRANT SELECT ON content_assets TO mediaos_runtime;

ALTER TABLE content_asset_versions ENABLE ROW LEVEL SECURITY;
ALTER TABLE content_asset_versions FORCE ROW LEVEL SECURITY;
CREATE POLICY tenant_boundary ON content_asset_versions USING (tenant_id=public.context_tenant()) WITH CHECK(tenant_id=public.context_tenant());
GRANT SELECT ON content_asset_versions TO mediaos_runtime;

ALTER TABLE content_claims ENABLE ROW LEVEL SECURITY;
ALTER TABLE content_claims FORCE ROW LEVEL SECURITY;
CREATE POLICY tenant_boundary ON content_claims USING (tenant_id=public.context_tenant()) WITH CHECK(tenant_id=public.context_tenant());
GRANT SELECT ON content_claims TO mediaos_runtime;

ALTER TABLE qa_reports ENABLE ROW LEVEL SECURITY;
ALTER TABLE qa_reports FORCE ROW LEVEL SECURITY;
CREATE POLICY tenant_boundary ON qa_reports USING (tenant_id=public.context_tenant()) WITH CHECK(tenant_id=public.context_tenant());
GRANT SELECT ON qa_reports TO mediaos_runtime;

ALTER TABLE approval_records ENABLE ROW LEVEL SECURITY;
ALTER TABLE approval_records FORCE ROW LEVEL SECURITY;
CREATE POLICY tenant_boundary ON approval_records USING (tenant_id=public.context_tenant()) WITH CHECK(tenant_id=public.context_tenant());
GRANT SELECT ON approval_records TO mediaos_runtime;

ALTER TABLE skill_runs ENABLE ROW LEVEL SECURITY;
ALTER TABLE skill_runs FORCE ROW LEVEL SECURITY;
CREATE POLICY tenant_boundary ON skill_runs USING (tenant_id=public.context_tenant()) WITH CHECK(tenant_id=public.context_tenant());
GRANT SELECT ON skill_runs TO mediaos_runtime;

ALTER TABLE audit_events ENABLE ROW LEVEL SECURITY;
ALTER TABLE audit_events FORCE ROW LEVEL SECURITY;
CREATE POLICY tenant_boundary ON audit_events USING (tenant_id=public.context_tenant()) WITH CHECK(tenant_id=public.context_tenant());
GRANT SELECT ON audit_events TO mediaos_runtime;

ALTER TABLE cost_events ENABLE ROW LEVEL SECURITY;
ALTER TABLE cost_events FORCE ROW LEVEL SECURITY;
CREATE POLICY tenant_boundary ON cost_events USING (tenant_id=public.context_tenant()) WITH CHECK(tenant_id=public.context_tenant());
GRANT SELECT ON cost_events TO mediaos_runtime;

ALTER TABLE tenant_memberships ENABLE ROW LEVEL SECURITY;
ALTER TABLE tenant_memberships FORCE ROW LEVEL SECURITY;
CREATE POLICY tenant_boundary ON tenant_memberships USING (tenant_id=public.context_tenant()) WITH CHECK(tenant_id=public.context_tenant());
GRANT SELECT ON tenant_memberships TO mediaos_runtime;

ALTER TABLE tenants ENABLE ROW LEVEL SECURITY;
ALTER TABLE tenants FORCE ROW LEVEL SECURITY;
CREATE POLICY tenant_boundary ON tenants USING(id=public.context_tenant());
GRANT SELECT ON tenants TO mediaos_runtime;
GRANT USAGE ON SCHEMA public TO mediaos_runtime;
-- Principals, role membership and the protected authentication context cannot be written by runtime.
