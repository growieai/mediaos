-- Internal manual handoff preparation. No destination is contacted, no audit performed.
CREATE TABLE conversion_destinations (
 id uuid PRIMARY KEY DEFAULT gen_random_uuid(), tenant_id uuid NOT NULL REFERENCES tenants,
 destination_key text NOT NULL CHECK(destination_key ~ '^[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}$'),
 revision int NOT NULL CHECK(revision>0), mode text NOT NULL CHECK(mode='MANUAL_EXPORT'),
 label text NOT NULL CHECK(length(btrim(label))>0 AND length(label)<=200), enabled boolean NOT NULL,
 payload jsonb NOT NULL, content_hash text NOT NULL CHECK(content_hash=private.visual_hash(payload)),
 idempotency_key text NOT NULL CHECK(length(idempotency_key) BETWEEN 1 AND 128),
 input_hash text NOT NULL CHECK(input_hash ~ '^[0-9a-f]{64}$'), created_by uuid NOT NULL,
 created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
 UNIQUE(tenant_id,id), UNIQUE(tenant_id,id,content_hash), UNIQUE(tenant_id,destination_key,revision),
 UNIQUE(tenant_id,idempotency_key),
 FOREIGN KEY(tenant_id,created_by) REFERENCES tenant_memberships(tenant_id,principal_id)
);
CREATE TABLE conversion_requests (
 id uuid PRIMARY KEY DEFAULT gen_random_uuid(), tenant_id uuid NOT NULL REFERENCES tenants,
 request_reference text NOT NULL CHECK(request_reference ~ '^[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}$'),
 revision int NOT NULL CHECK(revision>0), workflow_run_id uuid NOT NULL,
 asset_version_id uuid NOT NULL, research_version_id uuid NOT NULL, qa_report_id uuid NOT NULL,
 approval_record_id uuid NOT NULL, community_event_id uuid,
 destination_version_id uuid NOT NULL, destination_hash text NOT NULL,
 subject_reference text NOT NULL CHECK(subject_reference ~ '^[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}$'),
 business_reference text NOT NULL CHECK(business_reference ~ '^[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}$'),
 mode text NOT NULL CHECK(mode IN ('MANUAL','FIXTURE')),
 purpose text NOT NULL CHECK(purpose='BUSINESS_AUDIT_REQUEST'),
 consent_hash text NOT NULL CHECK(consent_hash=private.visual_hash(payload->'consent')),
 captured_at timestamptz NOT NULL, expires_at timestamptz NOT NULL,
 payload jsonb NOT NULL, content_hash text NOT NULL CHECK(content_hash=private.visual_hash(payload)),
 idempotency_key text NOT NULL CHECK(length(idempotency_key) BETWEEN 1 AND 128),
 input_hash text NOT NULL CHECK(input_hash ~ '^[0-9a-f]{64}$'), created_by uuid NOT NULL,
 correlation_id uuid NOT NULL, created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
 UNIQUE(tenant_id,id), UNIQUE(tenant_id,id,workflow_run_id), UNIQUE(tenant_id,id,request_reference), UNIQUE(tenant_id,idempotency_key),
 UNIQUE(tenant_id,request_reference,revision),
 UNIQUE(tenant_id,id,content_hash,destination_version_id,destination_hash,consent_hash),
 FOREIGN KEY(tenant_id,workflow_run_id) REFERENCES workflow_runs(tenant_id,id),
 FOREIGN KEY(tenant_id,approval_record_id,workflow_run_id,asset_version_id,research_version_id,qa_report_id)
 REFERENCES approval_records(tenant_id,id,workflow_run_id,asset_version_id,research_version_id,qa_report_id),
 FOREIGN KEY(tenant_id,community_event_id,workflow_run_id) REFERENCES community_events(tenant_id,id,workflow_run_id),
 FOREIGN KEY(tenant_id,destination_version_id,destination_hash) REFERENCES conversion_destinations(tenant_id,id,content_hash),
 FOREIGN KEY(tenant_id,created_by) REFERENCES tenant_memberships(tenant_id,principal_id),
 CHECK(captured_at<=created_at AND expires_at>captured_at)
);
CREATE INDEX conversion_workflow ON conversion_requests(tenant_id,workflow_run_id,created_at);
CREATE TABLE conversion_attestations (
 id uuid PRIMARY KEY DEFAULT gen_random_uuid(), tenant_id uuid NOT NULL REFERENCES tenants,
 request_id uuid NOT NULL, request_hash text NOT NULL, destination_version_id uuid NOT NULL,
 destination_hash text NOT NULL, consent_hash text NOT NULL,
 consent_attested boolean NOT NULL CHECK(consent_attested),
 status text NOT NULL DEFAULT 'REVIEWED_REQUEST' CHECK(status='REVIEWED_REQUEST'),
 payload jsonb NOT NULL, content_hash text NOT NULL CHECK(content_hash=private.visual_hash(payload)),
 created_by uuid NOT NULL, created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
 UNIQUE(tenant_id,id), UNIQUE(tenant_id,request_id), UNIQUE(tenant_id,id,request_id),
 FOREIGN KEY(tenant_id,request_id,request_hash,destination_version_id,destination_hash,consent_hash)
 REFERENCES conversion_requests(tenant_id,id,content_hash,destination_version_id,destination_hash,consent_hash),
 FOREIGN KEY(tenant_id,created_by) REFERENCES tenant_memberships(tenant_id,principal_id)
);
CREATE TABLE conversion_revocations (
 id uuid PRIMARY KEY DEFAULT gen_random_uuid(), tenant_id uuid NOT NULL REFERENCES tenants,
 request_id uuid NOT NULL, request_reference text NOT NULL,
 reason text NOT NULL CHECK(length(btrim(reason))>0 AND length(reason)<=2000),
 created_by uuid NOT NULL, created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
 UNIQUE(tenant_id,id), UNIQUE(tenant_id,request_reference),
 FOREIGN KEY(tenant_id,request_id,request_reference) REFERENCES conversion_requests(tenant_id,id,request_reference),
 FOREIGN KEY(tenant_id,created_by) REFERENCES tenant_memberships(tenant_id,principal_id)
);
CREATE TABLE conversion_exports (
 id uuid PRIMARY KEY DEFAULT gen_random_uuid(), tenant_id uuid NOT NULL REFERENCES tenants,
 request_id uuid NOT NULL, workflow_run_id uuid NOT NULL, attestation_id uuid NOT NULL,
 status text NOT NULL DEFAULT 'EXPORTED_FOR_MANUAL_HANDOFF' CHECK(status='EXPORTED_FOR_MANUAL_HANDOFF'),
 delivered boolean NOT NULL DEFAULT false CHECK(NOT delivered),
 network_performed boolean NOT NULL DEFAULT false CHECK(NOT network_performed),
 payload jsonb NOT NULL, content_hash text NOT NULL CHECK(content_hash=private.visual_hash(payload)),
 idempotency_key text NOT NULL CHECK(length(idempotency_key) BETWEEN 1 AND 128),
 input_hash text NOT NULL CHECK(input_hash ~ '^[0-9a-f]{64}$'), skill_run_id uuid NOT NULL,
 created_by uuid NOT NULL, created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
 UNIQUE(tenant_id,id), UNIQUE(tenant_id,idempotency_key),
 FOREIGN KEY(tenant_id,request_id,workflow_run_id) REFERENCES conversion_requests(tenant_id,id,workflow_run_id),
 FOREIGN KEY(tenant_id,attestation_id,request_id) REFERENCES conversion_attestations(tenant_id,id,request_id),
 FOREIGN KEY(tenant_id,skill_run_id,workflow_run_id) REFERENCES skill_runs(tenant_id,id,workflow_run_id),
 FOREIGN KEY(tenant_id,created_by) REFERENCES tenant_memberships(tenant_id,principal_id),
 CHECK(payload->'delivered'='false'::jsonb AND payload->'network_performed'='false'::jsonb
 AND payload->'audit_completed'='false'::jsonb)
);
DO $$ DECLARE t text; BEGIN
 FOREACH t IN ARRAY ARRAY['conversion_destinations','conversion_requests','conversion_attestations',
 'conversion_revocations','conversion_exports'] LOOP
 EXECUTE format('ALTER TABLE %I ENABLE ROW LEVEL SECURITY',t);
 EXECUTE format('ALTER TABLE %I FORCE ROW LEVEL SECURITY',t);
 EXECUTE format('CREATE POLICY tenant_boundary ON %I USING (tenant_id=public.context_tenant()) WITH CHECK (tenant_id=public.context_tenant())',t);
 EXECUTE format('CREATE TRIGGER immutable BEFORE UPDATE OR DELETE ON %I FOR EACH ROW EXECUTE FUNCTION immutable_record()',t);
 EXECUTE format('REVOKE ALL ON %I FROM PUBLIC,mediaos_runtime',t);
 EXECUTE format('GRANT SELECT ON %I TO mediaos_runtime',t);
 END LOOP;
 END $$;

CREATE FUNCTION private.conversion_object(p jsonb, keys text[]) RETURNS void
 LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,public AS $$ BEGIN
 IF p IS NULL OR jsonb_typeof(p) IS DISTINCT FROM 'object' OR octet_length(p::text)>32768
 OR NOT(p ?& keys) OR p-keys<>'{}'::jsonb THEN
 RAISE EXCEPTION 'Exact bounded conversion schema required' USING ERRCODE='23514'; END IF;
 END $$;
CREATE FUNCTION private.conversion_text(p jsonb, name text, maximum int, pattern text DEFAULT NULL) RETURNS void
 LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,public AS $$ BEGIN
 IF jsonb_typeof(p->name) IS DISTINCT FROM 'string' OR length(btrim(p->>name))=0
 OR length(p->>name)>maximum OR (pattern IS NOT NULL AND (p->>name)!~pattern) THEN
 RAISE EXCEPTION 'Invalid conversion field' USING ERRCODE='23514'; END IF;
 END $$;
CREATE FUNCTION private.conversion_idempotency(key text,inputhash text,expected jsonb) RETURNS void
 LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,public AS $$ BEGIN
 IF key IS NULL OR length(key) NOT BETWEEN 1 AND 128
 OR inputhash IS DISTINCT FROM private.visual_hash(expected) THEN
 RAISE EXCEPTION 'Conversion idempotency hash mismatch' USING ERRCODE='23514'; END IF;
 END $$;
CREATE FUNCTION private.audit_conversion(r public.conversion_requests,event text,details jsonb) RETURNS void
 LANGUAGE sql SECURITY DEFINER SET search_path=pg_catalog,public AS $$
 INSERT INTO public.audit_events(tenant_id,workflow_run_id,actor_id,event_type,correlation_id,details)
 VALUES(r.tenant_id,r.workflow_run_id,public.context_principal(),event,r.correlation_id,
 jsonb_build_object('conversion_request_id',r.id,'request_revision',r.revision,'mode',r.mode,
 'network_performed',false,'delivered',false)||details) $$;

CREATE FUNCTION public.register_conversion_destination(p jsonb,key text,inputhash text) RETURNS uuid
 LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,public AS $$
 DECLARE d public.conversion_destinations; rev int; BEGIN
 IF NOT public.has_role('ADMIN') THEN RAISE EXCEPTION 'Admin required' USING ERRCODE='42501'; END IF;
 PERFORM private.conversion_idempotency(key,inputhash,jsonb_build_object('payload',p));
 PERFORM pg_advisory_xact_lock(hashtextextended('conversion-destination-key:'||public.context_tenant()::text||':'||key,0));
 SELECT * INTO d FROM public.conversion_destinations WHERE tenant_id=public.context_tenant() AND idempotency_key=key;
 IF d.id IS NOT NULL THEN
 IF d.input_hash<>inputhash THEN RAISE EXCEPTION 'Destination key conflict' USING ERRCODE='23505'; END IF;
 RETURN d.id; END IF;
 PERFORM private.conversion_object(p,ARRAY['schema_version','destination_key','mode','label','enabled']);
 PERFORM private.conversion_text(p,'destination_key',128,'^[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}$');
 PERFORM private.conversion_text(p,'label',200);
 IF p->'schema_version' IS DISTINCT FROM '1'::jsonb OR p->>'mode' IS DISTINCT FROM 'MANUAL_EXPORT'
 OR jsonb_typeof(p->'enabled') IS DISTINCT FROM 'boolean' THEN
 RAISE EXCEPTION 'Only internal manual export destinations supported' USING ERRCODE='23514'; END IF;
 PERFORM pg_advisory_xact_lock(hashtextextended('conversion-destination:'||public.context_tenant()::text||':'||(p->>'destination_key'),0));
 SELECT coalesce(max(revision),0)+1 INTO rev FROM public.conversion_destinations
 WHERE tenant_id=public.context_tenant() AND destination_key=p->>'destination_key';
 INSERT INTO public.conversion_destinations(tenant_id,destination_key,revision,mode,label,enabled,payload,content_hash,
 idempotency_key,input_hash,created_by) VALUES(public.context_tenant(),p->>'destination_key',rev,'MANUAL_EXPORT',
 p->>'label',(p->>'enabled')::boolean,p,private.visual_hash(p),key,inputhash,public.context_principal()) RETURNING * INTO d;
 RETURN d.id;
 END $$;

CREATE FUNCTION public.register_conversion_request(workflowid uuid,p jsonb,key text,inputhash text) RETURNS uuid
 LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,public AS $$
 DECLARE r public.conversion_requests; prior public.conversion_requests; w public.workflow_runs;
 d public.conversion_destinations; a public.approval_records; c jsonb; name text;
 captured timestamptz; expires timestamptz; eventid uuid;
 uuid_pattern text:='^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$';
 BEGIN
 IF NOT public.has_role('OPERATOR') THEN RAISE EXCEPTION 'Operator required' USING ERRCODE='42501'; END IF;
 PERFORM private.conversion_idempotency(key,inputhash,jsonb_build_object('workflow_run_id',workflowid,'payload',p));
 SELECT * INTO w FROM public.workflow_runs WHERE tenant_id=public.context_tenant() AND id=workflowid;
 IF w.id IS NULL THEN RAISE EXCEPTION 'Workflow not found' USING ERRCODE='P0002'; END IF;
 PERFORM pg_advisory_xact_lock(hashtextextended('conversion-request-key:'||w.tenant_id::text||':'||key,0));
 SELECT * INTO r FROM public.conversion_requests WHERE tenant_id=w.tenant_id AND idempotency_key=key;
 IF r.id IS NOT NULL THEN
 IF r.input_hash<>inputhash THEN RAISE EXCEPTION 'Request key conflict' USING ERRCODE='23505'; END IF;
 RETURN r.id; END IF;
 PERFORM private.conversion_object(p,ARRAY['schema_version','request_reference','subject_reference','business_reference',
 'mode','purpose','approval_record_id','community_event_id','destination_version_id','consent']);
 FOREACH name IN ARRAY ARRAY['request_reference','subject_reference','business_reference'] LOOP
 PERFORM private.conversion_text(p,name,128,'^[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}$'); END LOOP;
 PERFORM private.conversion_text(p,'approval_record_id',36,uuid_pattern);
 PERFORM private.conversion_text(p,'destination_version_id',36,uuid_pattern);
 IF p->'community_event_id'<>'null'::jsonb THEN
 PERFORM private.conversion_text(p,'community_event_id',36,uuid_pattern); eventid:=(p->>'community_event_id')::uuid; END IF;
 IF p->'schema_version' IS DISTINCT FROM '1'::jsonb OR coalesce(p->>'mode','') NOT IN ('MANUAL','FIXTURE')
 OR p->>'purpose' IS DISTINCT FROM 'BUSINESS_AUDIT_REQUEST' THEN
 RAISE EXCEPTION 'Invalid conversion request policy' USING ERRCODE='23514'; END IF;
 c:=p->'consent'; PERFORM private.conversion_object(c,ARRAY['schema_version','purpose','statement','origin','captured_at','expires_at']);
 PERFORM private.conversion_text(c,'statement',2000); PERFORM private.conversion_text(c,'origin',512);
 FOREACH name IN ARRAY ARRAY['captured_at','expires_at'] LOOP
 PERFORM private.conversion_text(c,name,40,'^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(\.\d{1,6})?(Z|\+00:00)$'); END LOOP;
 IF c->'schema_version' IS DISTINCT FROM '1'::jsonb OR c->>'purpose' IS DISTINCT FROM 'BUSINESS_AUDIT_REQUEST' THEN
 RAISE EXCEPTION 'Explicit purpose-specific consent evidence required' USING ERRCODE='23514'; END IF;
 BEGIN captured:=(c->>'captured_at')::timestamptz; expires:=(c->>'expires_at')::timestamptz;
 EXCEPTION WHEN invalid_datetime_format OR datetime_field_overflow THEN
 RAISE EXCEPTION 'Invalid consent timestamp' USING ERRCODE='23514'; END;
 IF captured>clock_timestamp() OR expires<=captured THEN
 RAISE EXCEPTION 'Invalid consent time window' USING ERRCODE='23514'; END IF;
 -- All request revision/review/revocation/export operations take this same series lock.
 PERFORM pg_advisory_xact_lock(hashtextextended('conversion-request:'||w.tenant_id::text||':'||(p->>'request_reference'),0));
 SELECT * INTO prior FROM public.conversion_requests WHERE tenant_id=w.tenant_id
 AND request_reference=p->>'request_reference' ORDER BY revision DESC LIMIT 1;
 IF prior.id IS NOT NULL AND (prior.mode<>p->>'mode' OR prior.subject_reference<>p->>'subject_reference'
 OR prior.business_reference<>p->>'business_reference' OR prior.workflow_run_id<>workflowid) THEN
 RAISE EXCEPTION 'Request identity and fixture provenance are permanent' USING ERRCODE='23514'; END IF;
 IF EXISTS(SELECT 1 FROM public.conversion_revocations WHERE tenant_id=w.tenant_id AND request_reference=p->>'request_reference') THEN
 RAISE EXCEPTION 'Revoked request cannot acquire new revisions' USING ERRCODE='23514'; END IF;
 SELECT * INTO a FROM public.approval_records WHERE tenant_id=w.tenant_id
 AND id=(p->>'approval_record_id')::uuid AND workflow_run_id=w.id AND decision='APPROVE';
 IF a.id IS NULL THEN RAISE EXCEPTION 'Exact historical approval required' USING ERRCODE='P0002'; END IF;
 IF eventid IS NOT NULL AND NOT EXISTS(SELECT 1 FROM public.community_events WHERE tenant_id=w.tenant_id
 AND id=eventid AND workflow_run_id=w.id AND (mode='MANUAL' OR p->>'mode'='FIXTURE')) THEN
 RAISE EXCEPTION 'Owned attribution required; fixture events remain fixtures' USING ERRCODE='P0002'; END IF;
 SELECT * INTO d FROM public.conversion_destinations WHERE tenant_id=w.tenant_id AND id=(p->>'destination_version_id')::uuid;
 IF d.id IS NULL THEN RAISE EXCEPTION 'Destination not found' USING ERRCODE='P0002'; END IF;
 PERFORM pg_advisory_xact_lock(hashtextextended('conversion-destination:'||w.tenant_id::text||':'||d.destination_key,0));
 IF NOT d.enabled OR EXISTS(SELECT 1 FROM public.conversion_destinations WHERE tenant_id=d.tenant_id
 AND destination_key=d.destination_key AND revision>d.revision) THEN
 RAISE EXCEPTION 'Current enabled destination required' USING ERRCODE='23514'; END IF;
 INSERT INTO public.conversion_requests(tenant_id,request_reference,revision,workflow_run_id,asset_version_id,research_version_id,
 qa_report_id,approval_record_id,community_event_id,destination_version_id,destination_hash,subject_reference,business_reference,
 mode,purpose,consent_hash,captured_at,expires_at,payload,content_hash,idempotency_key,input_hash,created_by,correlation_id)
 VALUES(w.tenant_id,p->>'request_reference',coalesce(prior.revision,0)+1,w.id,a.asset_version_id,a.research_version_id,a.qa_report_id,
 a.id,eventid,d.id,d.content_hash,p->>'subject_reference',p->>'business_reference',p->>'mode',p->>'purpose',
 private.visual_hash(c),captured,expires,p,private.visual_hash(p),key,inputhash,public.context_principal(),w.correlation_id)
 RETURNING * INTO r;
 PERFORM private.audit_conversion(r,'CONVERSION_REQUEST_RECORDED',jsonb_build_object('request_hash',r.content_hash,'consent_hash',r.consent_hash));
 RETURN r.id;
 END $$;

CREATE FUNCTION private.guard_conversion(requestid uuid) RETURNS public.conversion_requests
 LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,public AS $$
 DECLARE r public.conversion_requests; d public.conversion_destinations; BEGIN
 SELECT * INTO r FROM public.conversion_requests WHERE tenant_id=public.context_tenant() AND id=requestid;
 IF r.id IS NULL THEN RAISE EXCEPTION 'Request not found' USING ERRCODE='P0002'; END IF;
 PERFORM pg_advisory_xact_lock(hashtextextended('conversion-request:'||r.tenant_id::text||':'||r.request_reference,0));
 SELECT * INTO d FROM public.conversion_destinations WHERE tenant_id=r.tenant_id AND id=r.destination_version_id;
 PERFORM pg_advisory_xact_lock(hashtextextended('conversion-destination:'||r.tenant_id::text||':'||d.destination_key,0));
 IF r.mode<>'MANUAL' OR r.expires_at<=clock_timestamp() OR NOT d.enabled
 OR EXISTS(SELECT 1 FROM public.conversion_requests WHERE tenant_id=r.tenant_id AND request_reference=r.request_reference AND revision>r.revision)
 OR EXISTS(SELECT 1 FROM public.conversion_destinations WHERE tenant_id=d.tenant_id AND destination_key=d.destination_key AND revision>d.revision)
 OR EXISTS(SELECT 1 FROM public.conversion_revocations WHERE tenant_id=r.tenant_id AND request_reference=r.request_reference) THEN
 RAISE EXCEPTION 'Current unexpired unrevoked manual request and destination required' USING ERRCODE='23514'; END IF;
 RETURN r;
 END $$;

CREATE FUNCTION public.attest_conversion_request(requestid uuid,p jsonb) RETURNS uuid
 LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,public AS $$
 DECLARE r public.conversion_requests; a public.conversion_attestations; BEGIN
 IF NOT public.has_role('APPROVER') THEN RAISE EXCEPTION 'Approver required' USING ERRCODE='42501'; END IF;
 r:=private.guard_conversion(requestid);
 PERFORM private.conversion_object(p,ARRAY['request_hash','destination_hash','consent_hash','consent_attested','comment']);
 PERFORM private.conversion_text(p,'comment',2000);
 IF p->'consent_attested' IS DISTINCT FROM 'true'::jsonb OR p->>'request_hash' IS DISTINCT FROM r.content_hash
 OR p->>'destination_hash' IS DISTINCT FROM r.destination_hash OR p->>'consent_hash' IS DISTINCT FROM r.consent_hash THEN
 RAISE EXCEPTION 'Exact request, destination and consent attestation required' USING ERRCODE='23514'; END IF;
 SELECT * INTO a FROM public.conversion_attestations WHERE tenant_id=r.tenant_id AND request_id=r.id;
 IF a.id IS NOT NULL THEN
 IF a.payload<>p THEN RAISE EXCEPTION 'Review is immutable' USING ERRCODE='23505'; END IF; RETURN a.id; END IF;
 INSERT INTO public.conversion_attestations(tenant_id,request_id,request_hash,destination_version_id,destination_hash,
 consent_hash,consent_attested,payload,content_hash,created_by) VALUES(r.tenant_id,r.id,r.content_hash,r.destination_version_id,
 r.destination_hash,r.consent_hash,true,p,private.visual_hash(p),public.context_principal()) RETURNING * INTO a;
 PERFORM private.audit_conversion(r,'CONVERSION_CONSENT_ATTESTED',jsonb_build_object('attestation_id',a.id,'status','REVIEWED_REQUEST'));
 RETURN a.id;
 END $$;

CREATE FUNCTION public.revoke_conversion_request(requestid uuid,p jsonb) RETURNS uuid
 LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,public AS $$
 DECLARE r public.conversion_requests; v public.conversion_revocations; BEGIN
 IF NOT(public.has_role('OPERATOR') OR public.has_role('APPROVER')) THEN RAISE EXCEPTION 'Internal permission required' USING ERRCODE='42501'; END IF;
 SELECT * INTO r FROM public.conversion_requests WHERE tenant_id=public.context_tenant() AND id=requestid;
 IF r.id IS NULL THEN RAISE EXCEPTION 'Request not found' USING ERRCODE='P0002'; END IF;
 PERFORM private.conversion_object(p,ARRAY['reason']); PERFORM private.conversion_text(p,'reason',2000);
 PERFORM pg_advisory_xact_lock(hashtextextended('conversion-request:'||r.tenant_id::text||':'||r.request_reference,0));
 SELECT * INTO v FROM public.conversion_revocations WHERE tenant_id=r.tenant_id AND request_reference=r.request_reference;
 IF v.id IS NOT NULL THEN RETURN v.id; END IF;
 INSERT INTO public.conversion_revocations(tenant_id,request_id,request_reference,reason,created_by)
 VALUES(r.tenant_id,r.id,r.request_reference,p->>'reason',public.context_principal()) RETURNING * INTO v;
 PERFORM private.audit_conversion(r,'CONVERSION_CONSENT_REVOKED',jsonb_build_object('revocation_id',v.id,'status','REVOKED'));
 RETURN v.id;
 END $$;

CREATE FUNCTION public.export_conversion_request(requestid uuid,p jsonb,key text,inputhash text) RETURNS uuid
 LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,public AS $$
 DECLARE r public.conversion_requests; a public.conversion_attestations; e public.conversion_exports;
 result jsonb; exportid uuid:=gen_random_uuid(); attemptid uuid; started timestamptz; finished timestamptz; BEGIN
 IF NOT public.has_role('OPERATOR') THEN RAISE EXCEPTION 'Operator required' USING ERRCODE='42501'; END IF;
 PERFORM private.conversion_idempotency(key,inputhash,jsonb_build_object('request_id',requestid,'payload',p));
 -- Tenant-key lock before series lock matches all export replays. Replays must recheck authority.
 PERFORM pg_advisory_xact_lock(hashtextextended('conversion-export-key:'||public.context_tenant()::text||':'||key,0));
 r:=private.guard_conversion(requestid);
 PERFORM private.conversion_object(p,ARRAY['request_hash','destination_hash','attestation_id']);
 IF p->>'request_hash' IS DISTINCT FROM r.content_hash OR p->>'destination_hash' IS DISTINCT FROM r.destination_hash THEN
 RAISE EXCEPTION 'Exact export hashes required' USING ERRCODE='23514'; END IF;
 PERFORM private.conversion_text(p,'attestation_id',36,'^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$');
 SELECT * INTO a FROM public.conversion_attestations WHERE tenant_id=r.tenant_id AND request_id=r.id AND id=(p->>'attestation_id')::uuid;
 IF a.id IS NULL THEN RAISE EXCEPTION 'Exact reviewed consent required' USING ERRCODE='23514'; END IF;
 SELECT * INTO e FROM public.conversion_exports WHERE tenant_id=r.tenant_id AND idempotency_key=key;
 IF e.id IS NOT NULL THEN
 IF e.input_hash<>inputhash THEN RAISE EXCEPTION 'Export key conflict' USING ERRCODE='23505'; END IF; RETURN e.id; END IF;
 started:=clock_timestamp();
 INSERT INTO public.skill_runs(tenant_id,workflow_run_id,skill_identifier,skill_version,input_schema_version,output_schema_version,
 provider,model,adapter,attempt,step_key,input_hash,started_at,status,input_tokens,output_tokens,cost,is_mock)
 VALUES(r.tenant_id,r.workflow_run_id,'conversion.export','1.0.0',1,1,'deterministic','none','manual-handoff-v1',1,
 'conversion.export:'||exportid,inputhash,started,'RUNNING',0,0,0,false) RETURNING id INTO attemptid;
 result:=jsonb_build_object('schema_version',1,'request_id',r.id,'request_revision',r.revision,'request_hash',r.content_hash,
 'subject_reference',r.subject_reference,'business_reference',r.business_reference,'purpose',r.purpose,
 'destination_version_id',r.destination_version_id,'destination_hash',r.destination_hash,'consent_attestation_id',a.id,
 'consent_expires_at',r.payload->'consent'->>'expires_at','workflow_run_id',r.workflow_run_id,'approval_record_id',r.approval_record_id,
 'community_event_id',r.community_event_id,'mode','MANUAL_EXPORT','status','EXPORTED_FOR_MANUAL_HANDOFF',
 'delivered',false,'network_performed',false,'audit_completed',false,'consent_provenance','OPERATOR_ASSERTION_REVIEWED');
 INSERT INTO public.conversion_exports(id,tenant_id,request_id,workflow_run_id,attestation_id,payload,content_hash,
 idempotency_key,input_hash,skill_run_id,created_by) VALUES(exportid,r.tenant_id,r.id,r.workflow_run_id,a.id,result,
 private.visual_hash(result),key,inputhash,attemptid,public.context_principal()) RETURNING * INTO e;
 finished:=clock_timestamp();
 -- Generic attempt history is readable without the guarded export transition.
 -- Persist only receipt metadata there; the handoff lives solely in conversion_exports.
 UPDATE public.skill_runs SET status='SUCCEEDED',ended_at=finished,latency_ms=greatest(0,extract(epoch FROM finished-started)*1000),
 output=jsonb_build_object('schema_version',1,'export_id',e.id,'request_id',r.id,'content_hash',e.content_hash,
 'status','EXPORTED_FOR_MANUAL_HANDOFF','mode','MANUAL_EXPORT','delivered',false,'network_performed',false,'audit_completed',false)
 WHERE tenant_id=r.tenant_id AND id=attemptid;
 INSERT INTO public.cost_events(tenant_id,workflow_run_id,skill_run_id,provider,model,input_tokens,output_tokens,cost,currency,price_version)
 VALUES(r.tenant_id,r.workflow_run_id,attemptid,'deterministic','none',0,0,0,'USD','deterministic-zero-v1');
 PERFORM private.audit_conversion(r,'CONVERSION_MANUAL_EXPORT_CREATED',jsonb_build_object('export_id',e.id,
 'skill_run_id',attemptid,'status',e.status,'content_hash',e.content_hash));
 RETURN e.id;
 END $$;

REVOKE ALL ON FUNCTION private.conversion_object(jsonb,text[]),private.conversion_text(jsonb,text,int,text),
 private.conversion_idempotency(text,text,jsonb),private.audit_conversion(public.conversion_requests,text,jsonb),
 private.guard_conversion(uuid) FROM PUBLIC,mediaos_runtime;
REVOKE ALL ON FUNCTION public.register_conversion_destination(jsonb,text,text),public.register_conversion_request(uuid,jsonb,text,text),
 public.attest_conversion_request(uuid,jsonb),public.revoke_conversion_request(uuid,jsonb),public.export_conversion_request(uuid,jsonb,text,text) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION public.register_conversion_destination(jsonb,text,text),public.register_conversion_request(uuid,jsonb,text,text),
 public.attest_conversion_request(uuid,jsonb),public.revoke_conversion_request(uuid,jsonb),public.export_conversion_request(uuid,jsonb,text,text) TO mediaos_runtime;
