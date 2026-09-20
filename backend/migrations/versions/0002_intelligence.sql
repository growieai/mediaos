-- The original M0/M1 migrations remain frozen.
-- Shared government-host cooldown is infrastructure state, private to trusted ingestion.
CREATE TABLE private.source_host_cooldowns(hostname text PRIMARY KEY, retry_at timestamptz NOT NULL, updated_at timestamptz NOT NULL DEFAULT now());
ALTER TABLE skill_runs DROP CONSTRAINT skill_runs_provider_check;
ALTER TABLE skill_runs DROP CONSTRAINT skill_runs_is_mock_check;
ALTER TABLE skill_runs ADD CHECK(provider IN ('mock','deterministic'));
ALTER TABLE skill_runs ADD CHECK(is_mock=(provider='mock'));
ALTER TABLE cost_events DROP CONSTRAINT cost_events_provider_check;
ALTER TABLE cost_events ADD CHECK(provider IN ('mock','deterministic'));
ALTER TABLE tenant_memberships DROP CONSTRAINT tenant_memberships_roles_check;
ALTER TABLE tenant_memberships ADD CONSTRAINT tenant_memberships_roles_check
 CHECK(roles <@ ARRAY['OPERATOR','APPROVER','ADMIN','INGESTOR']::text[] AND cardinality(roles)>0);

CREATE TABLE source_definitions (
 id uuid PRIMARY KEY DEFAULT gen_random_uuid(), tenant_id uuid NOT NULL REFERENCES tenants,
 created_at timestamptz NOT NULL DEFAULT now(), UNIQUE(tenant_id,id),
 source_key text NOT NULL, country text NOT NULL, source_type text NOT NULL,
 connector text NOT NULL CHECK(connector IN ('BDNS','BOE','CAMARA')),
 authority_level text NOT NULL CHECK(authority_level IN ('PRIMARY','SECONDARY')),
 trust_level text NOT NULL CHECK(trust_level IN ('OFFICIAL','DISCOVERY_ONLY')),
 enabled boolean NOT NULL DEFAULT false, polling_policy jsonb NOT NULL,
 connector_configuration jsonb NOT NULL, parser_version text NOT NULL,
 access_policy text NOT NULL, UNIQUE(tenant_id,source_key)
);
CREATE TABLE ingestion_runs (
 id uuid PRIMARY KEY DEFAULT gen_random_uuid(), tenant_id uuid NOT NULL REFERENCES tenants,
 created_at timestamptz NOT NULL DEFAULT now(), UNIQUE(tenant_id,id),
 source_definition_id uuid NOT NULL, created_by uuid NOT NULL, correlation_id uuid NOT NULL,
 idempotency_key text NOT NULL, input_hash text NOT NULL, request jsonb NOT NULL,
 mode text NOT NULL CHECK(mode IN ('MANUAL','SCHEDULE_READY')),
 status text NOT NULL DEFAULT 'CREATED' CHECK(status IN ('CREATED','RUNNING','SUCCEEDED','FAILED')),
 cursor text NOT NULL DEFAULT '', pending_page jsonb, document_offset int NOT NULL DEFAULT 0 CHECK(document_offset>=0),
 started_at timestamptz, ended_at timestamptz, error_category text, retry_at timestamptz,
 counters jsonb NOT NULL DEFAULT '{}', UNIQUE(tenant_id,idempotency_key),
 FOREIGN KEY(tenant_id,source_definition_id) REFERENCES source_definitions(tenant_id,id),
 FOREIGN KEY(tenant_id,created_by) REFERENCES tenant_memberships(tenant_id,principal_id)
);
CREATE TABLE raw_source_documents (
 id uuid PRIMARY KEY DEFAULT gen_random_uuid(), tenant_id uuid NOT NULL REFERENCES tenants,
 created_at timestamptz NOT NULL DEFAULT now(), UNIQUE(tenant_id,id),
 source_definition_id uuid NOT NULL, ingestion_run_id uuid NOT NULL, document_key text NOT NULL,
 url text NOT NULL, body text NOT NULL CHECK(octet_length(body)<=2000000), checksum text NOT NULL,
 media_type text NOT NULL, captured_at timestamptz NOT NULL, is_fixture boolean NOT NULL,
 etag text, environment text NOT NULL, parser_version text NOT NULL,
 UNIQUE(tenant_id,source_definition_id,document_key,checksum),
 UNIQUE(tenant_id,id,source_definition_id),
 FOREIGN KEY(tenant_id,source_definition_id) REFERENCES source_definitions(tenant_id,id),
 FOREIGN KEY(tenant_id,ingestion_run_id) REFERENCES ingestion_runs(tenant_id,id)
);
CREATE TABLE ingestion_attempts (
 id uuid PRIMARY KEY DEFAULT gen_random_uuid(), tenant_id uuid NOT NULL REFERENCES tenants,
 created_at timestamptz NOT NULL DEFAULT now(), UNIQUE(tenant_id,id),
 ingestion_run_id uuid NOT NULL, url text NOT NULL, attempt int NOT NULL CHECK(attempt>0),
 status_code int, latency_ms double precision NOT NULL CHECK(latency_ms>=0),
 error_category text, retry_at timestamptz, raw_document_id uuid,
 FOREIGN KEY(tenant_id,ingestion_run_id) REFERENCES ingestion_runs(tenant_id,id),
 FOREIGN KEY(tenant_id,raw_document_id) REFERENCES raw_source_documents(tenant_id,id)
);
CREATE TABLE source_observations (
 id uuid PRIMARY KEY DEFAULT gen_random_uuid(), tenant_id uuid NOT NULL REFERENCES tenants,
 created_at timestamptz NOT NULL DEFAULT now(), UNIQUE(tenant_id,id), raw_document_id uuid NOT NULL,
 ingestion_run_id uuid NOT NULL, fetched_at timestamptz NOT NULL, is_fixture boolean NOT NULL,
 UNIQUE(tenant_id,raw_document_id,fetched_at),
 FOREIGN KEY(tenant_id,raw_document_id) REFERENCES raw_source_documents(tenant_id,id),
 FOREIGN KEY(tenant_id,ingestion_run_id) REFERENCES ingestion_runs(tenant_id,id)
);

ALTER TABLE source_snapshots ALTER COLUMN workflow_run_id DROP NOT NULL;
ALTER TABLE source_snapshots ADD COLUMN raw_document_id uuid;
ALTER TABLE source_snapshots ADD COLUMN source_definition_id uuid;
ALTER TABLE source_snapshots ADD COLUMN representation text NOT NULL DEFAULT 'MANUAL' CHECK(representation IN ('MANUAL','RAW','NORMALIZED'));
ALTER TABLE source_snapshots ADD COLUMN parent_snapshot_id uuid;
ALTER TABLE source_snapshots ADD FOREIGN KEY(tenant_id,parent_snapshot_id) REFERENCES source_snapshots(tenant_id,id);
ALTER TABLE source_snapshots DROP CONSTRAINT source_snapshots_raw_content_check;
ALTER TABLE source_snapshots ADD CHECK(length(raw_content) BETWEEN 1 AND 2000000);
ALTER TABLE source_snapshots ADD CONSTRAINT ingested_document_owner FOREIGN KEY(tenant_id,raw_document_id,source_definition_id)
 REFERENCES raw_source_documents(tenant_id,id,source_definition_id);
ALTER TABLE source_snapshots ADD CONSTRAINT source_origin_required CHECK(
 workflow_run_id IS NOT NULL OR (raw_document_id IS NOT NULL AND source_definition_id IS NOT NULL));
CREATE UNIQUE INDEX ingested_snapshot_once ON source_snapshots(tenant_id,raw_document_id,representation) WHERE workflow_run_id IS NULL;

CREATE TABLE opportunities (
 id uuid PRIMARY KEY DEFAULT gen_random_uuid(), tenant_id uuid NOT NULL REFERENCES tenants,
 created_at timestamptz NOT NULL DEFAULT now(), UNIQUE(tenant_id,id),
 canonical_external_id text NOT NULL, country text NOT NULL, opportunity_type text NOT NULL,
 first_seen_at timestamptz NOT NULL DEFAULT now(), last_seen_at timestamptz NOT NULL DEFAULT now(),
 last_changed_at timestamptz NOT NULL DEFAULT now(), current_version_id uuid,
 UNIQUE(tenant_id,canonical_external_id)
);
CREATE TABLE opportunity_versions (
 id uuid PRIMARY KEY DEFAULT gen_random_uuid(), tenant_id uuid NOT NULL REFERENCES tenants,
 created_at timestamptz NOT NULL DEFAULT now(), UNIQUE(tenant_id,id),
 opportunity_id uuid NOT NULL, version int NOT NULL CHECK(version>0), schema_version int NOT NULL CHECK(schema_version=1),
 title text NOT NULL, issuing_body text NOT NULL, geography jsonb NOT NULL, business_types jsonb NOT NULL, industries jsonb NOT NULL,
 max_amount numeric CHECK(max_amount>=0), currency text, funding_percentage numeric CHECK(funding_percentage BETWEEN 0 AND 100),
 opening_date date, closing_date date, application_url text,
 status text NOT NULL CHECK(status IN ('UNKNOWN','UPCOMING','OPEN','CLOSED','CONFLICT')),
 source_confidence numeric NOT NULL CHECK(source_confidence BETWEEN 0 AND 1),
 payload jsonb NOT NULL, content_hash text NOT NULL, source_snapshot_id uuid NOT NULL,
 UNIQUE(tenant_id,opportunity_id,version), UNIQUE(tenant_id,id,opportunity_id),
 FOREIGN KEY(tenant_id,opportunity_id) REFERENCES opportunities(tenant_id,id),
 FOREIGN KEY(tenant_id,source_snapshot_id) REFERENCES source_snapshots(tenant_id,id)
);
ALTER TABLE opportunities ADD FOREIGN KEY(tenant_id,current_version_id,id) REFERENCES opportunity_versions(tenant_id,id,opportunity_id);
CREATE TABLE source_links (
 id uuid PRIMARY KEY DEFAULT gen_random_uuid(), tenant_id uuid NOT NULL REFERENCES tenants,
 created_at timestamptz NOT NULL DEFAULT now(), UNIQUE(tenant_id,id),
 opportunity_id uuid NOT NULL, source_snapshot_id uuid NOT NULL, source_definition_id uuid NOT NULL,
 document_key text NOT NULL, external_ids jsonb NOT NULL, normalized_payload jsonb NOT NULL,
 linkage_method text NOT NULL CHECK(linkage_method IN ('CANONICAL_ID','AUTHORITATIVE_REFERENCE','SOURCE_IDENTIFIER')),
 UNIQUE(tenant_id,opportunity_id,source_snapshot_id),
 FOREIGN KEY(tenant_id,opportunity_id) REFERENCES opportunities(tenant_id,id),
 FOREIGN KEY(tenant_id,source_snapshot_id) REFERENCES source_snapshots(tenant_id,id),
 FOREIGN KEY(tenant_id,source_definition_id) REFERENCES source_definitions(tenant_id,id)
);
CREATE TABLE opportunity_facts (
 id uuid PRIMARY KEY, tenant_id uuid NOT NULL REFERENCES tenants,
 created_at timestamptz NOT NULL DEFAULT now(), UNIQUE(tenant_id,id),
 opportunity_id uuid NOT NULL, source_snapshot_id uuid NOT NULL, fact_type text NOT NULL,
 normalized_value jsonb NOT NULL, unit text, statement text NOT NULL,
 span_start int NOT NULL CHECK(span_start>=0), span_end int NOT NULL CHECK(span_end>span_start),
 raw_locator text NOT NULL, extractor_version text NOT NULL, version int NOT NULL CHECK(version=1),
 verification_status text NOT NULL CHECK(verification_status IN ('VERIFIED','UNVERIFIED')),
 confidence numeric NOT NULL CHECK(confidence BETWEEN 0 AND 1),
 FOREIGN KEY(tenant_id,opportunity_id,source_snapshot_id) REFERENCES source_links(tenant_id,opportunity_id,source_snapshot_id)
);
CREATE TABLE verification_conflicts (
 id uuid PRIMARY KEY DEFAULT gen_random_uuid(), tenant_id uuid NOT NULL REFERENCES tenants,
 created_at timestamptz NOT NULL DEFAULT now(), UNIQUE(tenant_id,id),
 opportunity_id uuid NOT NULL, field text NOT NULL, left_snapshot_id uuid NOT NULL, right_snapshot_id uuid NOT NULL,
 left_value jsonb NOT NULL, right_value jsonb NOT NULL, status text NOT NULL DEFAULT 'UNRESOLVED' CHECK(status='UNRESOLVED'),
 UNIQUE(tenant_id,opportunity_id,field,left_snapshot_id,right_snapshot_id),
 FOREIGN KEY(tenant_id,opportunity_id,left_snapshot_id) REFERENCES source_links(tenant_id,opportunity_id,source_snapshot_id),
 FOREIGN KEY(tenant_id,opportunity_id,right_snapshot_id) REFERENCES source_links(tenant_id,opportunity_id,source_snapshot_id)
);
CREATE TABLE change_events (
 id uuid PRIMARY KEY DEFAULT gen_random_uuid(), tenant_id uuid NOT NULL REFERENCES tenants,
 created_at timestamptz NOT NULL DEFAULT now(), UNIQUE(tenant_id,id),
 opportunity_id uuid NOT NULL, old_version_id uuid, new_version_id uuid NOT NULL, source_snapshot_id uuid NOT NULL,
 event_type text NOT NULL CHECK(event_type IN ('NEW_OPPORTUNITY','STATUS_CHANGED','OPENING_DATE_CHANGED','DEADLINE_CHANGED','AMOUNT_CHANGED','FUNDING_PERCENTAGE_CHANGED','ELIGIBILITY_CHANGED','DOCUMENT_CHANGED','APPLICATION_OPENED','APPLICATION_CLOSED')),
 materiality text NOT NULL CHECK(materiality IN ('HIGH','LOW')), payload jsonb NOT NULL,
 FOREIGN KEY(tenant_id,old_version_id,opportunity_id) REFERENCES opportunity_versions(tenant_id,id,opportunity_id),
 FOREIGN KEY(tenant_id,new_version_id,opportunity_id) REFERENCES opportunity_versions(tenant_id,id,opportunity_id),
 FOREIGN KEY(tenant_id,source_snapshot_id) REFERENCES source_snapshots(tenant_id,id)
);
CREATE TABLE duplicate_candidates (
 id uuid PRIMARY KEY DEFAULT gen_random_uuid(), tenant_id uuid NOT NULL REFERENCES tenants,
 created_at timestamptz NOT NULL DEFAULT now(), UNIQUE(tenant_id,id),
 opportunity_id uuid NOT NULL, candidate_id uuid NOT NULL, score numeric NOT NULL CHECK(score BETWEEN 0 AND 1),
 status text NOT NULL CHECK(status='POSSIBLE_DUPLICATE'), algorithm text NOT NULL,
 CHECK(opportunity_id<>candidate_id), UNIQUE(tenant_id,opportunity_id,candidate_id),
 FOREIGN KEY(tenant_id,opportunity_id) REFERENCES opportunities(tenant_id,id),
 FOREIGN KEY(tenant_id,candidate_id) REFERENCES opportunities(tenant_id,id)
);
CREATE TABLE audience_segments (
 id uuid PRIMARY KEY DEFAULT gen_random_uuid(), tenant_id uuid NOT NULL REFERENCES tenants,
 created_at timestamptz NOT NULL DEFAULT now(), UNIQUE(tenant_id,id), code text NOT NULL,
 schema_version int NOT NULL CHECK(schema_version=1), payload jsonb NOT NULL, UNIQUE(tenant_id,code)
);
CREATE TABLE mission_editorial_policies (
 id uuid PRIMARY KEY DEFAULT gen_random_uuid(), tenant_id uuid NOT NULL REFERENCES tenants,
 created_at timestamptz NOT NULL DEFAULT now(), UNIQUE(tenant_id,id), mission_id uuid NOT NULL,
 version int NOT NULL CHECK(version>0), schema_version int NOT NULL CHECK(schema_version=1), payload jsonb NOT NULL,
 UNIQUE(tenant_id,mission_id,version), FOREIGN KEY(tenant_id,mission_id) REFERENCES missions(tenant_id,id)
);
CREATE TABLE relevance_scores (
 id uuid PRIMARY KEY DEFAULT gen_random_uuid(), tenant_id uuid NOT NULL REFERENCES tenants,
 created_at timestamptz NOT NULL DEFAULT now(), UNIQUE(tenant_id,id), opportunity_version_id uuid NOT NULL,
 audience_segment_id uuid NOT NULL, mission_id uuid NOT NULL, policy_id uuid NOT NULL,
 schema_version int NOT NULL CHECK(schema_version=1), payload jsonb NOT NULL, expires_at timestamptz NOT NULL,
 FOREIGN KEY(tenant_id,opportunity_version_id) REFERENCES opportunity_versions(tenant_id,id),
 FOREIGN KEY(tenant_id,audience_segment_id) REFERENCES audience_segments(tenant_id,id),
 FOREIGN KEY(tenant_id,mission_id) REFERENCES missions(tenant_id,id),
 FOREIGN KEY(tenant_id,policy_id) REFERENCES mission_editorial_policies(tenant_id,id)
);
CREATE TABLE editorial_decisions (
 id uuid PRIMARY KEY DEFAULT gen_random_uuid(), tenant_id uuid NOT NULL REFERENCES tenants,
 created_at timestamptz NOT NULL DEFAULT now(), UNIQUE(tenant_id,id), relevance_score_id uuid NOT NULL,
 decision text NOT NULL CHECK(decision IN ('CREATE_CONTENT','WATCH','IGNORE','HUMAN_REVIEW')),
 schema_version int NOT NULL CHECK(schema_version=1), payload jsonb NOT NULL, research_context jsonb NOT NULL,
 FOREIGN KEY(tenant_id,relevance_score_id) REFERENCES relevance_scores(tenant_id,id)
);
CREATE TABLE workflow_opportunities (
 id uuid PRIMARY KEY DEFAULT gen_random_uuid(), tenant_id uuid NOT NULL REFERENCES tenants,
 created_at timestamptz NOT NULL DEFAULT now(), UNIQUE(tenant_id,id), workflow_run_id uuid NOT NULL,
 opportunity_id uuid NOT NULL, opportunity_version_id uuid NOT NULL, source_snapshot_id uuid NOT NULL,
 editorial_decision_id uuid NOT NULL, audience_segment_id uuid NOT NULL, expires_at timestamptz NOT NULL,
 research_context jsonb NOT NULL, UNIQUE(tenant_id,workflow_run_id),
 FOREIGN KEY(tenant_id,workflow_run_id) REFERENCES workflow_runs(tenant_id,id),
 FOREIGN KEY(tenant_id,opportunity_version_id,opportunity_id) REFERENCES opportunity_versions(tenant_id,id,opportunity_id),
 FOREIGN KEY(tenant_id,opportunity_id,source_snapshot_id) REFERENCES source_links(tenant_id,opportunity_id,source_snapshot_id),
 FOREIGN KEY(tenant_id,editorial_decision_id) REFERENCES editorial_decisions(tenant_id,id),
 FOREIGN KEY(tenant_id,audience_segment_id) REFERENCES audience_segments(tenant_id,id)
);

CREATE INDEX source_link_current ON source_links(tenant_id,opportunity_id,source_definition_id,document_key,created_at DESC);
CREATE INDEX opportunity_facts_source ON opportunity_facts(tenant_id,opportunity_id,source_snapshot_id);
CREATE INDEX ingestion_status ON ingestion_runs(tenant_id,status,created_at);
CREATE INDEX opportunity_change_time ON change_events(tenant_id,opportunity_id,created_at);
CREATE INDEX unresolved_opportunity ON verification_conflicts(tenant_id,opportunity_id);

DO $$ DECLARE t text; BEGIN
 FOREACH t IN ARRAY ARRAY['source_definitions','ingestion_runs','raw_source_documents','source_observations','ingestion_attempts','opportunities','opportunity_versions','source_links','opportunity_facts','verification_conflicts','change_events','duplicate_candidates','audience_segments','mission_editorial_policies','relevance_scores','editorial_decisions','workflow_opportunities'] LOOP
 EXECUTE format('ALTER TABLE %I ENABLE ROW LEVEL SECURITY',t);
 EXECUTE format('ALTER TABLE %I FORCE ROW LEVEL SECURITY',t);
 EXECUTE format('CREATE POLICY tenant_boundary ON %I USING(tenant_id=public.context_tenant()) WITH CHECK(tenant_id=public.context_tenant())',t);
 EXECUTE format('GRANT SELECT ON %I TO mediaos_runtime',t);
 IF t NOT IN ('ingestion_runs','opportunities') THEN
 EXECUTE format('CREATE TRIGGER immutable BEFORE UPDATE OR DELETE ON %I FOR EACH ROW EXECUTE FUNCTION immutable_record()',t);
 END IF;
 END LOOP;
END $$;
