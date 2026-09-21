-- Historical observations only. Neither manual assertions nor fixtures prove that
-- a platform post exists, confer publishing authority, or change editorial policy.
CREATE TABLE metric_subjects (
 id uuid PRIMARY KEY DEFAULT gen_random_uuid(), tenant_id uuid NOT NULL REFERENCES tenants,
 workflow_run_id uuid NOT NULL, render_run_id uuid NOT NULL, asset_version_id uuid NOT NULL,
 research_version_id uuid NOT NULL, qa_report_id uuid NOT NULL,
 content_approval_record_id uuid NOT NULL, visual_approval_record_id uuid NOT NULL,
 manifest_hash text NOT NULL CHECK(manifest_hash ~ '^[0-9a-f]{64}$'),
 mode text NOT NULL CHECK(mode IN ('MANUAL','FIXTURE')),
 verification_status text NOT NULL CHECK(verification_status IN ('SELF_REPORTED','FIXTURE')),
 platform_label text NOT NULL CHECK(length(btrim(platform_label))>0 AND length(platform_label)<=100),
 external_reference text CHECK(length(btrim(external_reference))>0 AND length(external_reference)<=512),
 provenance_note text NOT NULL CHECK(length(btrim(provenance_note))>0 AND length(provenance_note)<=2000),
 schema_version int NOT NULL DEFAULT 1 CHECK(schema_version=1), payload jsonb NOT NULL,
 content_hash text NOT NULL CHECK(content_hash ~ '^[0-9a-f]{64}$'),
 input_hash text NOT NULL CHECK(input_hash ~ '^[0-9a-f]{64}$'),
 idempotency_key text NOT NULL CHECK(length(idempotency_key) BETWEEN 1 AND 128),
 created_by uuid NOT NULL, correlation_id uuid NOT NULL,
 created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
 UNIQUE(tenant_id,id), UNIQUE(tenant_id,idempotency_key),
 UNIQUE(tenant_id,id,workflow_run_id,mode,verification_status),
 FOREIGN KEY(tenant_id,workflow_run_id) REFERENCES workflow_runs(tenant_id,id),
 FOREIGN KEY(tenant_id,render_run_id,workflow_run_id,asset_version_id,research_version_id,qa_report_id,manifest_hash)
   REFERENCES render_runs(tenant_id,id,workflow_run_id,asset_version_id,research_version_id,qa_report_id,manifest_hash),
 FOREIGN KEY(tenant_id,content_approval_record_id,workflow_run_id,asset_version_id,research_version_id,qa_report_id)
   REFERENCES approval_records(tenant_id,id,workflow_run_id,asset_version_id,research_version_id,qa_report_id),
 FOREIGN KEY(tenant_id,visual_approval_record_id,render_run_id,workflow_run_id,asset_version_id,research_version_id,
   qa_report_id,content_approval_record_id,manifest_hash)
   REFERENCES visual_approval_records(tenant_id,id,render_run_id,workflow_run_id,asset_version_id,research_version_id,
   qa_report_id,content_approval_record_id,manifest_hash),
 FOREIGN KEY(tenant_id,created_by) REFERENCES tenant_memberships(tenant_id,principal_id),
 CHECK((mode='MANUAL' AND verification_status='SELF_REPORTED' AND external_reference IS NOT NULL)
   OR (mode='FIXTURE' AND verification_status='FIXTURE' AND external_reference IS NULL)),
 CHECK(jsonb_typeof(payload)='object' AND octet_length(payload::text)<=16384),
 CHECK(content_hash=private.visual_hash(payload))
);
CREATE UNIQUE INDEX metric_manual_external_reference ON metric_subjects(tenant_id,platform_label,external_reference)
 WHERE mode='MANUAL';
CREATE INDEX metric_subject_workflow ON metric_subjects(tenant_id,workflow_run_id,created_at);

CREATE TABLE metric_snapshots (
 id uuid PRIMARY KEY DEFAULT gen_random_uuid(), tenant_id uuid NOT NULL REFERENCES tenants,
 subject_id uuid NOT NULL, workflow_run_id uuid NOT NULL,
 mode text NOT NULL CHECK(mode IN ('MANUAL','FIXTURE')),
 verification_status text NOT NULL CHECK(verification_status IN ('SELF_REPORTED','FIXTURE')),
 schema_version int NOT NULL DEFAULT 1 CHECK(schema_version=1),
 observed_at timestamptz NOT NULL, scope text NOT NULL CHECK(scope='LIFETIME_CUMULATIVE'),
 reach bigint CHECK(reach BETWEEN 0 AND 9007199254740991),
 saves bigint CHECK(saves BETWEEN 0 AND 9007199254740991),
 shares bigint CHECK(shares BETWEEN 0 AND 9007199254740991),
 comments bigint CHECK(comments BETWEEN 0 AND 9007199254740991),
 follows bigint CHECK(follows BETWEEN 0 AND 9007199254740991),
 evidence_text text NOT NULL CHECK(length(btrim(evidence_text))>0 AND length(evidence_text)<=20000),
 definition_notes text NOT NULL CHECK(length(btrim(definition_notes))>0 AND length(definition_notes)<=2000),
 payload jsonb NOT NULL, content_hash text NOT NULL CHECK(content_hash ~ '^[0-9a-f]{64}$'),
 input_hash text NOT NULL CHECK(input_hash ~ '^[0-9a-f]{64}$'),
 idempotency_key text NOT NULL CHECK(length(idempotency_key) BETWEEN 1 AND 128),
 created_by uuid NOT NULL, correlation_id uuid NOT NULL,
 created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
 UNIQUE(tenant_id,id), UNIQUE(tenant_id,id,subject_id), UNIQUE(tenant_id,idempotency_key),
 FOREIGN KEY(tenant_id,subject_id,workflow_run_id,mode,verification_status)
   REFERENCES metric_subjects(tenant_id,id,workflow_run_id,mode,verification_status),
 FOREIGN KEY(tenant_id,created_by) REFERENCES tenant_memberships(tenant_id,principal_id),
 CHECK(observed_at<=created_at),
 CHECK(jsonb_typeof(payload)='object' AND octet_length(payload::text)<=131072),
 CHECK(content_hash=private.visual_hash(payload))
);
CREATE INDEX metric_snapshot_history ON metric_snapshots(tenant_id,subject_id,observed_at,created_at);

CREATE TABLE learning_reports (
 id uuid PRIMARY KEY DEFAULT gen_random_uuid(), tenant_id uuid NOT NULL REFERENCES tenants,
 subject_id uuid NOT NULL, workflow_run_id uuid NOT NULL,
 mode text NOT NULL CHECK(mode IN ('MANUAL','FIXTURE')),
 verification_status text NOT NULL CHECK(verification_status IN ('SELF_REPORTED','FIXTURE')),
 baseline_snapshot_id uuid NOT NULL, current_snapshot_id uuid NOT NULL,
 algorithm_version text NOT NULL DEFAULT 'descriptive-v1' CHECK(algorithm_version='descriptive-v1'),
 schema_version int NOT NULL DEFAULT 1 CHECK(schema_version=1),
 status text NOT NULL CHECK(status IN ('DESCRIPTIVE','INSUFFICIENT_DATA')),
 payload jsonb NOT NULL, content_hash text NOT NULL CHECK(content_hash ~ '^[0-9a-f]{64}$'),
 input_hash text NOT NULL CHECK(input_hash ~ '^[0-9a-f]{64}$'),
 idempotency_key text NOT NULL CHECK(length(idempotency_key) BETWEEN 1 AND 128),
 skill_run_id uuid NOT NULL, created_by uuid NOT NULL, correlation_id uuid NOT NULL,
 created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
 UNIQUE(tenant_id,id), UNIQUE(tenant_id,idempotency_key),
 FOREIGN KEY(tenant_id,subject_id,workflow_run_id,mode,verification_status)
   REFERENCES metric_subjects(tenant_id,id,workflow_run_id,mode,verification_status),
 FOREIGN KEY(tenant_id,baseline_snapshot_id,subject_id) REFERENCES metric_snapshots(tenant_id,id,subject_id),
 FOREIGN KEY(tenant_id,current_snapshot_id,subject_id) REFERENCES metric_snapshots(tenant_id,id,subject_id),
 FOREIGN KEY(tenant_id,skill_run_id,workflow_run_id) REFERENCES skill_runs(tenant_id,id,workflow_run_id),
 FOREIGN KEY(tenant_id,created_by) REFERENCES tenant_memberships(tenant_id,principal_id),
 CHECK(baseline_snapshot_id<>current_snapshot_id),
 CHECK(jsonb_typeof(payload)='object' AND octet_length(payload::text)<=65536),
 CHECK(content_hash=private.visual_hash(payload)),
 CHECK(payload->'policy_updated'='false'::jsonb AND payload->'causal_claim'='false'::jsonb)
);
CREATE INDEX metric_learning_history ON learning_reports(tenant_id,subject_id,created_at);

DO $$ DECLARE t text; BEGIN
 FOREACH t IN ARRAY ARRAY['metric_subjects','metric_snapshots','learning_reports'] LOOP
 EXECUTE format('ALTER TABLE %I ENABLE ROW LEVEL SECURITY',t);
 EXECUTE format('ALTER TABLE %I FORCE ROW LEVEL SECURITY',t);
 EXECUTE format('CREATE POLICY tenant_boundary ON %I USING (tenant_id=public.context_tenant()) WITH CHECK (tenant_id=public.context_tenant())',t);
 EXECUTE format('CREATE TRIGGER immutable BEFORE UPDATE OR DELETE ON %I FOR EACH ROW EXECUTE FUNCTION immutable_record()',t);
 EXECUTE format('REVOKE ALL ON %I FROM PUBLIC,mediaos_runtime',t);
 EXECUTE format('GRANT SELECT ON %I TO mediaos_runtime',t);
 END LOOP;
 END $$;

CREATE FUNCTION private.metrics_request(key text, inputhash text, expected jsonb) RETURNS void
 LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,public AS $$
 BEGIN
 IF NOT public.has_role('OPERATOR') THEN RAISE EXCEPTION 'Operator required' USING ERRCODE='42501'; END IF;
 IF key IS NULL OR length(key) NOT BETWEEN 1 AND 128 OR inputhash IS DISTINCT FROM private.visual_hash(expected) THEN
 RAISE EXCEPTION 'Valid metric idempotency key and canonical request hash required' USING ERRCODE='23514'; END IF;
 END $$;

CREATE FUNCTION private.audit_metrics(subject public.metric_subjects, event text, details jsonb) RETURNS void
 LANGUAGE sql SECURITY DEFINER SET search_path=pg_catalog,public AS $$
 INSERT INTO public.audit_events(tenant_id,workflow_run_id,actor_id,event_type,correlation_id,details)
 VALUES(subject.tenant_id,subject.workflow_run_id,public.context_principal(),event,subject.correlation_id,
 jsonb_build_object('metric_subject_id',subject.id,'render_run_id',subject.render_run_id,
 'mode',subject.mode,'verification_status',subject.verification_status,'network_performed',false)||details) $$;

CREATE FUNCTION public.register_metric_subject(workflowid uuid, payload jsonb, key text, inputhash text)
 RETURNS uuid LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,public AS $$
 DECLARE w public.workflow_runs; r public.render_runs; approval public.visual_approval_records;
 s public.metric_subjects; renderid uuid;
 required_keys text[]:=ARRAY['schema_version','render_run_id','mode','platform_label','external_reference','provenance_note'];
 BEGIN
 PERFORM private.metrics_request(key,inputhash,jsonb_build_object('workflow_run_id',workflowid,'payload',payload));
 SELECT * INTO w FROM public.workflow_runs WHERE tenant_id=public.context_tenant() AND id=workflowid;
 IF w.id IS NULL THEN RAISE EXCEPTION 'Workflow not found' USING ERRCODE='P0002'; END IF;
 PERFORM pg_advisory_xact_lock(hashtextextended('metric-subject:'||w.tenant_id::text||':'||key,0));
 SELECT * INTO s FROM public.metric_subjects WHERE tenant_id=w.tenant_id AND idempotency_key=key;
 IF s.id IS NOT NULL THEN
 IF s.input_hash<>inputhash OR s.workflow_run_id<>workflowid THEN
 RAISE EXCEPTION 'Metric subject idempotency payload conflict' USING ERRCODE='23505'; END IF;
 RETURN s.id; END IF;
 IF payload IS NULL OR jsonb_typeof(payload) IS DISTINCT FROM 'object' OR octet_length(payload::text)>16384
 OR NOT(payload ?& required_keys) OR payload-required_keys<>'{}'::jsonb
 OR payload->'schema_version' IS DISTINCT FROM '1'::jsonb
 OR coalesce(payload->>'mode','') NOT IN ('MANUAL','FIXTURE')
 OR jsonb_typeof(payload->'render_run_id') IS DISTINCT FROM 'string'
 OR coalesce(payload->>'render_run_id','') !~ '^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$'
 OR jsonb_typeof(payload->'platform_label') IS DISTINCT FROM 'string'
 OR length(btrim(payload->>'platform_label'))=0 OR length(payload->>'platform_label')>100
 OR jsonb_typeof(payload->'provenance_note') IS DISTINCT FROM 'string'
 OR length(btrim(payload->>'provenance_note'))=0 OR length(payload->>'provenance_note')>2000 THEN
 RAISE EXCEPTION 'Invalid typed metric subject' USING ERRCODE='23514'; END IF;
 IF (payload->>'mode'='MANUAL' AND (jsonb_typeof(payload->'external_reference') IS DISTINCT FROM 'string'
 OR length(btrim(payload->>'external_reference'))=0 OR length(payload->>'external_reference')>512))
 OR (payload->>'mode'='FIXTURE' AND payload->'external_reference' IS DISTINCT FROM 'null'::jsonb) THEN
 RAISE EXCEPTION 'Manual metrics require an asserted reference; fixtures have no external post' USING ERRCODE='23514'; END IF;
 renderid:=(payload->>'render_run_id')::uuid;
 SELECT * INTO r FROM public.render_runs WHERE tenant_id=w.tenant_id AND id=renderid AND workflow_run_id=w.id;
 IF r.id IS NULL THEN RAISE EXCEPTION 'Owned workflow render not found' USING ERRCODE='P0002'; END IF;
 -- Historical approval is intentional. Current expiry/new revisions do not erase
 -- the association, and recording a metric never authorizes a future dispatch.
 SELECT * INTO approval FROM public.visual_approval_records WHERE tenant_id=r.tenant_id
 AND render_run_id=r.id AND manifest_hash=r.manifest_hash AND decision='APPROVE';
 IF r.status<>'PASS' OR approval.id IS NULL OR NOT EXISTS(
 SELECT 1 FROM public.approval_records a WHERE a.tenant_id=r.tenant_id
 AND a.id=approval.content_approval_record_id AND a.decision='APPROVE'
 AND a.workflow_run_id=r.workflow_run_id AND a.asset_version_id=r.asset_version_id
 AND a.research_version_id=r.research_version_id AND a.qa_report_id=r.qa_report_id) THEN
 RAISE EXCEPTION 'Exact historical content and visual approvals required' USING ERRCODE='23514'; END IF;
 INSERT INTO public.metric_subjects(tenant_id,workflow_run_id,render_run_id,asset_version_id,research_version_id,
 qa_report_id,content_approval_record_id,visual_approval_record_id,manifest_hash,mode,verification_status,
 platform_label,external_reference,provenance_note,payload,content_hash,input_hash,idempotency_key,created_by,correlation_id)
 VALUES(w.tenant_id,w.id,r.id,r.asset_version_id,r.research_version_id,r.qa_report_id,
 approval.content_approval_record_id,approval.id,r.manifest_hash,payload->>'mode',
 CASE WHEN payload->>'mode'='MANUAL' THEN 'SELF_REPORTED' ELSE 'FIXTURE' END,
 payload->>'platform_label',payload->>'external_reference',payload->>'provenance_note',payload,
 private.visual_hash(payload),inputhash,key,public.context_principal(),w.correlation_id)
 ON CONFLICT(tenant_id,idempotency_key) DO NOTHING RETURNING * INTO s;
 IF s.id IS NULL THEN
 SELECT * INTO s FROM public.metric_subjects WHERE tenant_id=w.tenant_id AND idempotency_key=key;
 IF s.input_hash<>inputhash OR s.workflow_run_id<>workflowid THEN
 RAISE EXCEPTION 'Metric subject idempotency payload conflict' USING ERRCODE='23505'; END IF;
 ELSE PERFORM private.audit_metrics(s,'METRIC_SUBJECT_REGISTERED',jsonb_build_object('content_hash',s.content_hash));
 END IF;
 RETURN s.id;
 END $$;

CREATE FUNCTION public.import_metric_snapshot(subjectid uuid, payload jsonb, key text, inputhash text)
 RETURNS uuid LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,public AS $$
 DECLARE s public.metric_subjects; snapshot public.metric_snapshots; observed timestamptz;
 name text; value jsonb;
 required_keys text[]:=ARRAY['schema_version','observed_at','scope','reach','saves','shares','comments','follows','evidence_text','definition_notes'];
 BEGIN
 PERFORM private.metrics_request(key,inputhash,jsonb_build_object('subject_id',subjectid,'payload',payload));
 SELECT * INTO s FROM public.metric_subjects WHERE tenant_id=public.context_tenant() AND id=subjectid;
 IF s.id IS NULL THEN RAISE EXCEPTION 'Metric subject not found' USING ERRCODE='P0002'; END IF;
 SELECT * INTO snapshot FROM public.metric_snapshots WHERE tenant_id=s.tenant_id AND idempotency_key=key;
 IF snapshot.id IS NOT NULL THEN
 IF snapshot.input_hash<>inputhash OR snapshot.subject_id<>subjectid THEN
 RAISE EXCEPTION 'Metric snapshot idempotency payload conflict' USING ERRCODE='23505'; END IF;
 RETURN snapshot.id; END IF;
 IF payload IS NULL OR jsonb_typeof(payload) IS DISTINCT FROM 'object' OR octet_length(payload::text)>131072
 OR NOT(payload ?& required_keys) OR payload-required_keys<>'{}'::jsonb
 OR payload->'schema_version' IS DISTINCT FROM '1'::jsonb
 OR payload->>'scope' IS DISTINCT FROM 'LIFETIME_CUMULATIVE'
 OR jsonb_typeof(payload->'observed_at') IS DISTINCT FROM 'string'
 OR coalesce(payload->>'observed_at','') !~ '^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(\.\d{1,6})?(Z|\+00:00)$'
 OR jsonb_typeof(payload->'evidence_text') IS DISTINCT FROM 'string'
 OR length(btrim(payload->>'evidence_text'))=0 OR length(payload->>'evidence_text')>20000
 OR jsonb_typeof(payload->'definition_notes') IS DISTINCT FROM 'string'
 OR length(btrim(payload->>'definition_notes'))=0 OR length(payload->>'definition_notes')>2000 THEN
 RAISE EXCEPTION 'Invalid typed metric snapshot' USING ERRCODE='23514'; END IF;
 BEGIN observed:=(payload->>'observed_at')::timestamptz;
 EXCEPTION WHEN invalid_datetime_format OR datetime_field_overflow THEN
 RAISE EXCEPTION 'Invalid metric observation timestamp' USING ERRCODE='23514'; END;
 IF observed>clock_timestamp() THEN RAISE EXCEPTION 'Metric observation cannot be in the future' USING ERRCODE='23514'; END IF;
 FOREACH name IN ARRAY ARRAY['reach','saves','shares','comments','follows'] LOOP
 value:=payload->name;
 IF value<>'null'::jsonb THEN
 IF jsonb_typeof(value) IS DISTINCT FROM 'number' OR value::text !~ '^(0|[1-9][0-9]*)$' THEN
 RAISE EXCEPTION 'Metric counts must be nonnegative integers or explicit null' USING ERRCODE='23514'; END IF;
 IF value::numeric>9007199254740991 THEN
 RAISE EXCEPTION 'Metric count exceeds supported safe integer range' USING ERRCODE='23514'; END IF;
 END IF;
 END LOOP;
 INSERT INTO public.metric_snapshots(tenant_id,subject_id,workflow_run_id,mode,verification_status,
 observed_at,scope,reach,saves,shares,comments,follows,evidence_text,definition_notes,payload,content_hash,
 input_hash,idempotency_key,created_by,correlation_id)
 VALUES(s.tenant_id,s.id,s.workflow_run_id,s.mode,s.verification_status,observed,payload->>'scope',
 (payload->>'reach')::bigint,(payload->>'saves')::bigint,(payload->>'shares')::bigint,
 (payload->>'comments')::bigint,(payload->>'follows')::bigint,payload->>'evidence_text',payload->>'definition_notes',
 payload,private.visual_hash(payload),inputhash,key,public.context_principal(),s.correlation_id)
 ON CONFLICT(tenant_id,idempotency_key) DO NOTHING RETURNING * INTO snapshot;
 IF snapshot.id IS NULL THEN
 SELECT * INTO snapshot FROM public.metric_snapshots WHERE tenant_id=s.tenant_id AND idempotency_key=key;
 IF snapshot.input_hash<>inputhash OR snapshot.subject_id<>subjectid THEN
 RAISE EXCEPTION 'Metric snapshot idempotency payload conflict' USING ERRCODE='23505'; END IF;
 ELSE PERFORM private.audit_metrics(s,'METRIC_SNAPSHOT_IMPORTED',
 jsonb_build_object('metric_snapshot_id',snapshot.id,'content_hash',snapshot.content_hash,'observed_at',snapshot.observed_at));
 END IF;
 RETURN snapshot.id;
 END $$;

CREATE FUNCTION public.build_metric_learning(subjectid uuid, baselineid uuid, currentid uuid, key text, inputhash text)
 RETURNS uuid LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,public AS $$
 DECLARE s public.metric_subjects; baseline public.metric_snapshots; current_snapshot public.metric_snapshots;
 report public.learning_reports; reportid uuid:=gen_random_uuid(); attemptid uuid;
 name text; before_value bigint; after_value bigint; delta bigint; direction text;
 metrics jsonb:='{}'::jsonb; comparable_count int:=0; result jsonb; result_status text;
 start_time timestamptz; finish_time timestamptz;
 BEGIN
 PERFORM private.metrics_request(key,inputhash,jsonb_build_object('subject_id',subjectid,
 'baseline_snapshot_id',baselineid,'current_snapshot_id',currentid,'algorithm_version','descriptive-v1'));
 SELECT * INTO s FROM public.metric_subjects WHERE tenant_id=public.context_tenant() AND id=subjectid;
 IF s.id IS NULL THEN RAISE EXCEPTION 'Metric subject not found' USING ERRCODE='P0002'; END IF;
 -- Serialize this tenant/key before generating an attempt; a concurrent replay
 -- cannot leave a duplicate success or cost event even across different subjects.
 PERFORM pg_advisory_xact_lock(hashtextextended('metric-learning:'||s.tenant_id::text||':'||key,0));
 SELECT * INTO report FROM public.learning_reports WHERE tenant_id=s.tenant_id AND idempotency_key=key;
 IF report.id IS NOT NULL THEN
 IF report.input_hash<>inputhash OR report.subject_id<>subjectid THEN
 RAISE EXCEPTION 'Metric learning idempotency payload conflict' USING ERRCODE='23505'; END IF;
 RETURN report.id; END IF;
 SELECT * INTO baseline FROM public.metric_snapshots WHERE tenant_id=s.tenant_id AND id=baselineid AND subject_id=s.id;
 SELECT * INTO current_snapshot FROM public.metric_snapshots WHERE tenant_id=s.tenant_id AND id=currentid AND subject_id=s.id;
 IF baseline.id IS NULL OR current_snapshot.id IS NULL THEN
 RAISE EXCEPTION 'Owned snapshots of the same metric subject required' USING ERRCODE='P0002'; END IF;
 IF current_snapshot.observed_at<=baseline.observed_at OR baseline.scope<>current_snapshot.scope
 OR baseline.definition_notes<>current_snapshot.definition_notes THEN
 RAISE EXCEPTION 'Comparable scope, definitions and strictly increasing observation times required' USING ERRCODE='23514'; END IF;
 start_time:=clock_timestamp();
 INSERT INTO public.skill_runs(tenant_id,workflow_run_id,skill_identifier,skill_version,input_schema_version,
 output_schema_version,prompt_version,provider,model,adapter,attempt,step_key,input_hash,started_at,status,
 input_tokens,output_tokens,cost,is_mock)
 VALUES(s.tenant_id,s.workflow_run_id,'learning.describe','1.0.0',1,1,NULL,'deterministic','none',
 'descriptive-v1',1,'learning.describe:'||reportid,inputhash,start_time,'RUNNING',0,0,0,false) RETURNING id INTO attemptid;
 FOREACH name IN ARRAY ARRAY['reach','saves','shares','comments','follows'] LOOP
 before_value:=(baseline.payload->>name)::bigint; after_value:=(current_snapshot.payload->>name)::bigint;
 delta:=NULL; direction:='UNKNOWN';
 IF before_value IS NOT NULL AND after_value IS NOT NULL THEN
 comparable_count:=comparable_count+1; delta:=after_value-before_value;
 direction:=CASE WHEN delta>0 THEN 'INCREASED' WHEN delta<0 THEN 'DECREASED' ELSE 'UNCHANGED' END;
 END IF;
 metrics:=metrics||jsonb_build_object(name,jsonb_build_object('baseline',before_value,'current',after_value,
 'delta',delta,'direction',direction));
 END LOOP;
 result_status:=CASE WHEN comparable_count=0 THEN 'INSUFFICIENT_DATA' ELSE 'DESCRIPTIVE' END;
 result:=jsonb_build_object('schema_version',1,'algorithm_version','descriptive-v1','subject_id',s.id,
 'baseline_snapshot_id',baseline.id,'current_snapshot_id',current_snapshot.id,'mode',s.mode,
 'scope',baseline.scope,'baseline_observed_at',baseline.payload->>'observed_at',
 'current_observed_at',current_snapshot.payload->>'observed_at','definition_notes',baseline.definition_notes,
 'metrics',metrics,'limitations',jsonb_build_array(s.verification_status,'DESCRIPTIVE_ONLY','NO_CAUSAL_INFERENCE'),
 'status',result_status,'baseline_snapshot_hash',baseline.content_hash,'current_snapshot_hash',current_snapshot.content_hash,
 'policy_updated',false,'causal_claim',false);
 INSERT INTO public.learning_reports(id,tenant_id,subject_id,workflow_run_id,mode,verification_status,
 baseline_snapshot_id,current_snapshot_id,status,payload,content_hash,input_hash,idempotency_key,skill_run_id,created_by,correlation_id)
 VALUES(reportid,s.tenant_id,s.id,s.workflow_run_id,s.mode,s.verification_status,baseline.id,current_snapshot.id,
 result_status,result,private.visual_hash(result),inputhash,key,attemptid,public.context_principal(),s.correlation_id)
 RETURNING * INTO report;
 finish_time:=clock_timestamp();
 UPDATE public.skill_runs SET status='SUCCEEDED',ended_at=finish_time,
 latency_ms=greatest(0,extract(epoch FROM finish_time-start_time)*1000),output=result
 WHERE tenant_id=s.tenant_id AND id=attemptid;
 INSERT INTO public.cost_events(tenant_id,workflow_run_id,skill_run_id,provider,model,input_tokens,output_tokens,cost,currency,price_version)
 VALUES(s.tenant_id,s.workflow_run_id,attemptid,'deterministic','none',0,0,0,'USD','deterministic-zero-v1');
 PERFORM private.audit_metrics(s,'METRIC_LEARNING_CREATED',jsonb_build_object('learning_report_id',report.id,
 'skill_run_id',attemptid,'baseline_snapshot_id',baseline.id,'current_snapshot_id',current_snapshot.id,
 'content_hash',report.content_hash,'status',result_status,'policy_updated',false,'causal_claim',false));
 RETURN report.id;
 END $$;

REVOKE ALL ON FUNCTION private.metrics_request(text,text,jsonb),private.audit_metrics(public.metric_subjects,text,jsonb)
 FROM PUBLIC,mediaos_runtime;
REVOKE ALL ON FUNCTION public.register_metric_subject(uuid,jsonb,text,text),public.import_metric_snapshot(uuid,jsonb,text,text),
 public.build_metric_learning(uuid,uuid,uuid,text,text) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION public.register_metric_subject(uuid,jsonb,text,text),public.import_metric_snapshot(uuid,jsonb,text,text),
 public.build_metric_learning(uuid,uuid,uuid,text,text) TO mediaos_runtime;
