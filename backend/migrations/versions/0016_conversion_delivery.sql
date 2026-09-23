-- Manual consent export remains unchanged. Outbound handoff is an additional exact review.
CREATE TABLE business_identities (
 id uuid PRIMARY KEY DEFAULT gen_random_uuid(), tenant_id uuid NOT NULL REFERENCES tenants,
 business_reference text NOT NULL, revision int NOT NULL CHECK(revision>0), mode text NOT NULL CHECK(mode IN('MANUAL','FIXTURE')),
 retain_until timestamptz NOT NULL, payload jsonb NOT NULL, content_hash text NOT NULL CHECK(content_hash=private.visual_hash(payload)),
 idempotency_key text NOT NULL, input_hash text NOT NULL, created_by uuid NOT NULL,
 created_at timestamptz NOT NULL DEFAULT clock_timestamp(), UNIQUE(tenant_id,id), UNIQUE(tenant_id,idempotency_key),
 UNIQUE(tenant_id,business_reference,revision), FOREIGN KEY(tenant_id,created_by) REFERENCES tenant_memberships(tenant_id,principal_id)
);
CREATE TABLE business_identity_reviews (
 id uuid PRIMARY KEY DEFAULT gen_random_uuid(), tenant_id uuid NOT NULL REFERENCES tenants,
 identity_id uuid NOT NULL, content_hash text NOT NULL, comment text NOT NULL,
 created_by uuid NOT NULL, created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
 UNIQUE(tenant_id,id), UNIQUE(tenant_id,identity_id),
 FOREIGN KEY(tenant_id,identity_id) REFERENCES business_identities(tenant_id,id),
 FOREIGN KEY(tenant_id,created_by) REFERENCES tenant_memberships(tenant_id,principal_id)
);
CREATE TABLE conversion_transports (
 id uuid PRIMARY KEY DEFAULT gen_random_uuid(), tenant_id uuid NOT NULL REFERENCES tenants,
 destination_version_id uuid NOT NULL, endpoint text NOT NULL, protocol text NOT NULL DEFAULT 'signed-webhook-v1' CHECK(protocol='signed-webhook-v1'),
 max_retention_days int NOT NULL CHECK(max_retention_days BETWEEN 1 AND 90),
 created_at timestamptz NOT NULL DEFAULT clock_timestamp(), UNIQUE(tenant_id,id), UNIQUE(tenant_id,destination_version_id),
 FOREIGN KEY(tenant_id,destination_version_id) REFERENCES conversion_destinations(tenant_id,id),
 CHECK(endpoint ~ '^https://[a-z0-9][a-z0-9.-]+(:443)?/[A-Za-z0-9/_-]+$')
);
CREATE TABLE private.conversion_transport_keys (
 transport_id uuid PRIMARY KEY REFERENCES conversion_transports(id), signing_key bytea NOT NULL CHECK(octet_length(signing_key)=32)
);
REVOKE ALL ON private.conversion_transport_keys FROM PUBLIC,mediaos_runtime;
CREATE TABLE conversion_deliveries (
 id uuid PRIMARY KEY, tenant_id uuid NOT NULL REFERENCES tenants, request_id uuid NOT NULL,
 workflow_run_id uuid NOT NULL, request_reference text NOT NULL, business_identity_id uuid NOT NULL,
 transport_id uuid NOT NULL, attestation_id uuid NOT NULL, operation text NOT NULL CHECK(operation IN('SEND','REVOKE')),
 original_delivery_id uuid, payload jsonb NOT NULL, payload_hash text NOT NULL CHECK(payload_hash=private.visual_hash(payload)),
 expires_at timestamptz NOT NULL, idempotency_key text NOT NULL, input_hash text NOT NULL,
 created_by uuid NOT NULL, created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
 UNIQUE(tenant_id,id), UNIQUE(tenant_id,idempotency_key),
 FOREIGN KEY(tenant_id,request_id,workflow_run_id) REFERENCES conversion_requests(tenant_id,id,workflow_run_id),
 FOREIGN KEY(tenant_id,request_id,request_reference) REFERENCES conversion_requests(tenant_id,id,request_reference),
 FOREIGN KEY(tenant_id,business_identity_id) REFERENCES business_identities(tenant_id,id),
 FOREIGN KEY(tenant_id,transport_id) REFERENCES conversion_transports(tenant_id,id),
 FOREIGN KEY(tenant_id,attestation_id,request_id) REFERENCES conversion_attestations(tenant_id,id,request_id),
 FOREIGN KEY(tenant_id,original_delivery_id) REFERENCES conversion_deliveries(tenant_id,id),
 FOREIGN KEY(tenant_id,created_by) REFERENCES tenant_memberships(tenant_id,principal_id),
 CHECK((operation='SEND')=(original_delivery_id IS NULL))
);
CREATE TABLE conversion_delivery_decisions (
 id uuid PRIMARY KEY DEFAULT gen_random_uuid(), tenant_id uuid NOT NULL REFERENCES tenants,
 delivery_id uuid NOT NULL, decision text NOT NULL CHECK(decision IN('AUTHORIZE','REJECT')),
 payload_hash text NOT NULL, expires_at timestamptz NOT NULL, comment text NOT NULL,
 created_by uuid NOT NULL, created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
 UNIQUE(tenant_id,id),
 FOREIGN KEY(tenant_id,delivery_id) REFERENCES conversion_deliveries(tenant_id,id),
 FOREIGN KEY(tenant_id,created_by) REFERENCES tenant_memberships(tenant_id,principal_id)
);
CREATE TABLE conversion_delivery_attempts (
 id uuid PRIMARY KEY DEFAULT gen_random_uuid(), tenant_id uuid NOT NULL REFERENCES tenants,
 delivery_id uuid NOT NULL, operation text NOT NULL CHECK(operation IN('DISPATCH','RECONCILE')),
 attempt int NOT NULL CHECK(attempt BETWEEN 1 AND 10), skill_run_id uuid NOT NULL,
 created_by uuid NOT NULL, created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
 UNIQUE(tenant_id,id), UNIQUE(tenant_id,delivery_id,operation,attempt),
 FOREIGN KEY(tenant_id,delivery_id) REFERENCES conversion_deliveries(tenant_id,id),
 FOREIGN KEY(tenant_id,created_by) REFERENCES tenant_memberships(tenant_id,principal_id),
 FOREIGN KEY(tenant_id,skill_run_id) REFERENCES skill_runs(tenant_id,id),
 CHECK(operation<>'DISPATCH' OR attempt=1)
);
CREATE TABLE conversion_delivery_results (
 id uuid PRIMARY KEY DEFAULT gen_random_uuid(), tenant_id uuid NOT NULL REFERENCES tenants,
 attempt_id uuid NOT NULL, status text NOT NULL CHECK(status IN('RECEIVED','REVOKED','NOT_FOUND','UNKNOWN_OUTCOME','NOT_SENT')),
 receipt jsonb, signature text, created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
 UNIQUE(tenant_id,id), UNIQUE(tenant_id,attempt_id),
 FOREIGN KEY(tenant_id,attempt_id) REFERENCES conversion_delivery_attempts(tenant_id,id),
 CHECK((status IN('RECEIVED','REVOKED','NOT_FOUND'))=(receipt IS NOT NULL AND signature IS NOT NULL))
);
DO $$ DECLARE t text; BEGIN
 FOREACH t IN ARRAY ARRAY['business_identities','business_identity_reviews','conversion_transports','conversion_deliveries',
 'conversion_delivery_decisions','conversion_delivery_attempts','conversion_delivery_results'] LOOP
 EXECUTE format('ALTER TABLE %I ENABLE ROW LEVEL SECURITY',t);
 EXECUTE format('ALTER TABLE %I FORCE ROW LEVEL SECURITY',t);
 EXECUTE format('CREATE POLICY tenant_boundary ON %I USING(tenant_id=public.context_tenant()) WITH CHECK(tenant_id=public.context_tenant())',t);
 EXECUTE format('CREATE TRIGGER immutable BEFORE UPDATE OR DELETE ON %I FOR EACH ROW EXECUTE FUNCTION immutable_record()',t);
 EXECUTE format('REVOKE ALL ON %I FROM PUBLIC,mediaos_runtime',t);
 EXECUTE format('GRANT SELECT ON %I TO mediaos_runtime',t);
 END LOOP;
END $$;

-- Provisioning requires the migration identity, unavailable to the API workload.
CREATE FUNCTION private.provision_conversion_transport(tenantid uuid,destinationid uuid,endpointvalue text,retentiondays int,secret text) RETURNS uuid
 LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,public AS $$
 DECLARE t public.conversion_transports; BEGIN
 IF session_user='mediaos_runtime' OR length(secret)<32 OR length(secret)>512 THEN
 RAISE EXCEPTION 'Privileged provisioning and a strong shared secret required' USING ERRCODE='42501'; END IF;
 SELECT * INTO t FROM public.conversion_transports WHERE tenant_id=tenantid AND destination_version_id=destinationid;
 IF t.id IS NOT NULL THEN
 IF t.endpoint<>endpointvalue OR t.max_retention_days<>retentiondays OR NOT EXISTS(
 SELECT 1 FROM private.conversion_transport_keys WHERE transport_id=t.id AND signing_key=public.digest(secret,'sha256')) THEN
 RAISE EXCEPTION 'Transport is immutable; provision a new destination revision' USING ERRCODE='23505'; END IF;
 RETURN t.id; END IF;
 INSERT INTO public.conversion_transports(tenant_id,destination_version_id,endpoint,max_retention_days)
 VALUES(tenantid,destinationid,endpointvalue,retentiondays) RETURNING * INTO t;
 INSERT INTO private.conversion_transport_keys VALUES(t.id,public.digest(secret,'sha256'));
 RETURN t.id;
 END $$;
REVOKE ALL ON FUNCTION private.provision_conversion_transport(uuid,uuid,text,int,text) FROM PUBLIC,mediaos_runtime;

CREATE FUNCTION public.register_business_identity(p jsonb,key text,inputhash text) RETURNS uuid
 LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,public AS $$
 DECLARE b public.business_identities; prior public.business_identities; BEGIN
 IF NOT public.has_role('OPERATOR') THEN RAISE EXCEPTION 'Operator required' USING ERRCODE='42501'; END IF;
 PERFORM private.conversion_idempotency(key,inputhash,jsonb_build_object('payload',p));
 PERFORM pg_advisory_xact_lock(hashtextextended('business-key:'||public.context_tenant()::text||':'||key,0));
 SELECT * INTO b FROM public.business_identities WHERE tenant_id=public.context_tenant() AND idempotency_key=key;
 IF b.id IS NOT NULL THEN
 IF b.input_hash<>inputhash THEN RAISE EXCEPTION 'Business key conflict' USING ERRCODE='23505'; END IF; RETURN b.id; END IF;
 PERFORM private.conversion_object(p,ARRAY['schema_version','business_reference','legal_name','country','registry_reference','evidence_origin','evidence_sha256','mode','retain_until']);
 PERFORM private.conversion_text(p,'business_reference',128,'^[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}$');
 PERFORM private.conversion_text(p,'registry_reference',128,'^[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}$');
 PERFORM private.conversion_text(p,'legal_name',200); PERFORM private.conversion_text(p,'country',2,'^[A-Z]{2}$');
 PERFORM private.conversion_text(p,'evidence_origin',512); PERFORM private.conversion_text(p,'evidence_sha256',64,'^[0-9a-f]{64}$');
 PERFORM private.conversion_text(p,'retain_until',40,'^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(\.\d{1,6})?(Z|\+00:00)$');
 IF p->'schema_version' IS DISTINCT FROM '1'::jsonb OR coalesce(p->>'mode','') NOT IN('MANUAL','FIXTURE')
 OR (p->>'retain_until')::timestamptz<=clock_timestamp() OR (p->>'retain_until')::timestamptz>clock_timestamp()+interval '90 days' THEN
 RAISE EXCEPTION 'Invalid bounded identity policy' USING ERRCODE='23514'; END IF;
 PERFORM pg_advisory_xact_lock(hashtextextended('business:'||public.context_tenant()::text||':'||(p->>'business_reference'),0));
 SELECT * INTO prior FROM public.business_identities WHERE tenant_id=public.context_tenant() AND business_reference=p->>'business_reference' ORDER BY revision DESC LIMIT 1;
 IF prior.id IS NOT NULL AND prior.mode<>p->>'mode' THEN RAISE EXCEPTION 'Fixture identity provenance is permanent' USING ERRCODE='23514'; END IF;
 INSERT INTO public.business_identities(tenant_id,business_reference,revision,mode,retain_until,payload,content_hash,idempotency_key,input_hash,created_by)
 VALUES(public.context_tenant(),p->>'business_reference',coalesce(prior.revision,0)+1,p->>'mode',(p->>'retain_until')::timestamptz,p,private.visual_hash(p),key,inputhash,public.context_principal()) RETURNING * INTO b;
 RETURN b.id;
 END $$;
CREATE FUNCTION public.review_business_identity(identityid uuid,p jsonb) RETURNS uuid
 LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,public AS $$
 DECLARE b public.business_identities; r public.business_identity_reviews; BEGIN
 IF NOT public.has_role('APPROVER') THEN RAISE EXCEPTION 'Approver required' USING ERRCODE='42501'; END IF;
 SELECT * INTO b FROM public.business_identities WHERE tenant_id=public.context_tenant() AND id=identityid;
 IF b.id IS NULL THEN RAISE EXCEPTION 'Business identity not found' USING ERRCODE='P0002'; END IF;
 PERFORM pg_advisory_xact_lock(hashtextextended('business:'||b.tenant_id::text||':'||b.business_reference,0));
 PERFORM private.conversion_object(p,ARRAY['content_hash','identity_attested','comment']); PERFORM private.conversion_text(p,'comment',2000);
 IF p->>'content_hash' IS DISTINCT FROM b.content_hash OR p->'identity_attested' IS DISTINCT FROM 'true'::jsonb
 OR b.mode<>'MANUAL' OR b.retain_until<=clock_timestamp() OR EXISTS(SELECT 1 FROM public.business_identities WHERE tenant_id=b.tenant_id AND business_reference=b.business_reference AND revision>b.revision) THEN
 RAISE EXCEPTION 'Exact current manual business identity required' USING ERRCODE='23514'; END IF;
 SELECT * INTO r FROM public.business_identity_reviews WHERE tenant_id=b.tenant_id AND identity_id=b.id;
 IF r.id IS NOT NULL THEN
 IF r.comment<>p->>'comment' THEN RAISE EXCEPTION 'Immutable business review' USING ERRCODE='23505'; END IF; RETURN r.id; END IF;
 INSERT INTO public.business_identity_reviews(tenant_id,identity_id,content_hash,comment,created_by)
 VALUES(b.tenant_id,b.id,b.content_hash,p->>'comment',public.context_principal()) RETURNING id INTO r.id; RETURN r.id;
 END $$;

CREATE FUNCTION private.lock_conversion_delivery(deliveryid uuid) RETURNS public.conversion_deliveries
 LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,public AS $$
 DECLARE d public.conversion_deliveries; BEGIN
 SELECT * INTO d FROM public.conversion_deliveries WHERE tenant_id=public.context_tenant() AND id=deliveryid;
 IF d.id IS NULL THEN RAISE EXCEPTION 'Delivery not found' USING ERRCODE='P0002'; END IF;
 PERFORM pg_advisory_xact_lock(hashtextextended('conversion-request:'||d.tenant_id::text||':'||d.request_reference,0));
 RETURN d;
 END $$;
CREATE FUNCTION private.guard_conversion_delivery(deliveryid uuid) RETURNS public.conversion_deliveries
 LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,public AS $$
 DECLARE d public.conversion_deliveries; r public.conversion_requests; b public.business_identities; BEGIN
 d:=private.lock_conversion_delivery(deliveryid);
 IF d.operation='SEND' THEN
 r:=private.guard_conversion(d.request_id);
 SELECT * INTO b FROM public.business_identities WHERE tenant_id=d.tenant_id AND id=d.business_identity_id;
 PERFORM pg_advisory_xact_lock(hashtextextended('business:'||b.tenant_id::text||':'||b.business_reference,0));
 IF b.mode<>'MANUAL' OR b.retain_until<=clock_timestamp() OR d.expires_at<=clock_timestamp()
 OR EXISTS(SELECT 1 FROM public.business_identities WHERE tenant_id=b.tenant_id AND business_reference=b.business_reference AND revision>b.revision)
 OR NOT EXISTS(SELECT 1 FROM public.business_identity_reviews WHERE tenant_id=b.tenant_id AND identity_id=b.id) THEN
 RAISE EXCEPTION 'Current reviewed business identity and unexpired handoff required' USING ERRCODE='23514'; END IF;
 END IF;
 IF EXISTS(SELECT 1 FROM public.conversion_delivery_decisions WHERE tenant_id=d.tenant_id AND delivery_id=d.id AND decision='REJECT') THEN
 RAISE EXCEPTION 'Rejected handoff' USING ERRCODE='23514'; END IF;
 RETURN d;
 END $$;

CREATE FUNCTION public.create_conversion_delivery(requestid uuid,p jsonb,key text,inputhash text) RETURNS uuid
 LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,public AS $$
 DECLARE r public.conversion_requests; b public.business_identities; t public.conversion_transports; d public.conversion_deliveries;
 new_id uuid:=gen_random_uuid(); body jsonb; expires timestamptz; BEGIN
 IF NOT public.has_role('OPERATOR') THEN RAISE EXCEPTION 'Operator required' USING ERRCODE='42501'; END IF;
 PERFORM private.conversion_idempotency(key,inputhash,jsonb_build_object('request_id',requestid,'payload',p));
 PERFORM pg_advisory_xact_lock(hashtextextended('conversion-delivery-key:'||public.context_tenant()::text||':'||key,0));
 SELECT * INTO d FROM public.conversion_deliveries WHERE tenant_id=public.context_tenant() AND idempotency_key=key;
 IF d.id IS NOT NULL THEN
 IF d.input_hash<>inputhash THEN RAISE EXCEPTION 'Delivery key conflict' USING ERRCODE='23505'; END IF; RETURN d.id; END IF;
 r:=private.guard_conversion(requestid);
 PERFORM private.conversion_object(p,ARRAY['business_identity_id','transport_id','attestation_id','request_hash']);
 IF p->>'request_hash' IS DISTINCT FROM r.content_hash THEN RAISE EXCEPTION 'Exact request required' USING ERRCODE='23514'; END IF;
 SELECT * INTO b FROM public.business_identities WHERE tenant_id=r.tenant_id AND id=(p->>'business_identity_id')::uuid AND business_reference=r.business_reference;
 SELECT * INTO t FROM public.conversion_transports WHERE tenant_id=r.tenant_id AND id=(p->>'transport_id')::uuid AND destination_version_id=r.destination_version_id;
 IF b.id IS NULL OR t.id IS NULL OR NOT EXISTS(SELECT 1 FROM public.conversion_attestations WHERE tenant_id=r.tenant_id AND request_id=r.id AND id=(p->>'attestation_id')::uuid) THEN
 RAISE EXCEPTION 'Owned exact identity transport and consent attestation required' USING ERRCODE='P0002'; END IF;
 IF EXISTS(SELECT 1 FROM public.conversion_deliveries prior WHERE prior.tenant_id=r.tenant_id AND prior.request_reference=r.request_reference AND prior.operation='SEND'
 AND NOT EXISTS(SELECT 1 FROM public.conversion_delivery_decisions WHERE tenant_id=prior.tenant_id AND delivery_id=prior.id AND decision='REJECT')) THEN
 RAISE EXCEPTION 'Existing handoff cannot be duplicated with another key or revision' USING ERRCODE='23505'; END IF;
 expires:=least(r.expires_at,b.retain_until,clock_timestamp()+make_interval(days=>t.max_retention_days));
 body:=jsonb_build_object('schema_version',1,'delivery_id',new_id,'operation','SEND','request_id',r.id,'request_hash',r.content_hash,
 'subject_reference',r.subject_reference,'business_reference',r.business_reference,'business_identity_id',b.id,'business_identity_hash',b.content_hash,
 'country',b.payload->>'country','registry_reference',b.payload->>'registry_reference','purpose',r.purpose,'consent_attestation_id',p->>'attestation_id',
 'consent_provenance','OPERATOR_ASSERTION_REVIEWED','retain_until',to_char(expires AT TIME ZONE 'UTC','YYYY-MM-DD"T"HH24:MI:SS.US"Z"'),
 'audit_completed',false);
 INSERT INTO public.conversion_deliveries(id,tenant_id,request_id,workflow_run_id,request_reference,business_identity_id,transport_id,attestation_id,operation,payload,payload_hash,expires_at,idempotency_key,input_hash,created_by)
 VALUES(new_id,r.tenant_id,r.id,r.workflow_run_id,r.request_reference,b.id,t.id,(p->>'attestation_id')::uuid,'SEND',body,private.visual_hash(body),expires,key,inputhash,public.context_principal()) RETURNING * INTO d;
 PERFORM private.guard_conversion_delivery(d.id);
 PERFORM private.audit_conversion(r,'CONVERSION_HANDOFF_PLANNED',jsonb_build_object('delivery_id',d.id,'payload_hash',d.payload_hash)); RETURN d.id;
 END $$;

CREATE FUNCTION public.create_conversion_revocation(originalid uuid,key text,inputhash text) RETURNS uuid
 LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,public AS $$
 DECLARE old public.conversion_deliveries; d public.conversion_deliveries; r public.conversion_requests; body jsonb; newid uuid:=gen_random_uuid(); BEGIN
 IF NOT public.has_role('OPERATOR') THEN RAISE EXCEPTION 'Operator required' USING ERRCODE='42501'; END IF;
 PERFORM private.conversion_idempotency(key,inputhash,jsonb_build_object('original_delivery_id',originalid));
 PERFORM pg_advisory_xact_lock(hashtextextended('conversion-delivery-key:'||public.context_tenant()::text||':'||key,0));
 old:=private.lock_conversion_delivery(originalid);
 SELECT * INTO d FROM public.conversion_deliveries WHERE tenant_id=old.tenant_id AND idempotency_key=key;
 IF d.id IS NOT NULL THEN
 IF d.input_hash<>inputhash THEN RAISE EXCEPTION 'Revocation key conflict' USING ERRCODE='23505'; END IF; RETURN d.id; END IF;
 IF old.operation<>'SEND' OR NOT EXISTS(SELECT 1 FROM public.conversion_delivery_attempts WHERE tenant_id=old.tenant_id AND delivery_id=old.id AND operation='DISPATCH')
 OR (old.expires_at>clock_timestamp() AND NOT EXISTS(SELECT 1 FROM public.conversion_revocations WHERE tenant_id=old.tenant_id AND request_reference=old.request_reference)) THEN
 RAISE EXCEPTION 'Previously dispatched revoked or retention-expired handoff required' USING ERRCODE='23514'; END IF;
 IF EXISTS(SELECT 1 FROM public.conversion_deliveries prior WHERE prior.tenant_id=old.tenant_id AND prior.original_delivery_id=old.id
 AND NOT EXISTS(SELECT 1 FROM public.conversion_delivery_decisions WHERE tenant_id=prior.tenant_id AND delivery_id=prior.id AND decision='REJECT')) THEN
 RAISE EXCEPTION 'Existing revocation cannot be duplicated' USING ERRCODE='23505'; END IF;
 body:=jsonb_build_object('schema_version',1,'delivery_id',newid,'operation','REVOKE','original_delivery_id',old.id,'original_payload_hash',old.payload_hash,'audit_completed',false);
 INSERT INTO public.conversion_deliveries(id,tenant_id,request_id,workflow_run_id,request_reference,business_identity_id,transport_id,attestation_id,operation,original_delivery_id,payload,payload_hash,expires_at,idempotency_key,input_hash,created_by)
 VALUES(newid,old.tenant_id,old.request_id,old.workflow_run_id,old.request_reference,old.business_identity_id,old.transport_id,old.attestation_id,'REVOKE',old.id,body,private.visual_hash(body),clock_timestamp()+interval '1 day',key,inputhash,public.context_principal()) RETURNING * INTO d;
 SELECT * INTO r FROM public.conversion_requests WHERE tenant_id=d.tenant_id AND id=d.request_id;
 PERFORM private.audit_conversion(r,'CONVERSION_REVOCATION_PLANNED',jsonb_build_object('delivery_id',d.id,'original_delivery_id',old.id)); RETURN d.id;
 END $$;

CREATE FUNCTION public.authorize_conversion_delivery(deliveryid uuid,p jsonb) RETURNS uuid
 LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,public AS $$
 DECLARE d public.conversion_deliveries; a public.conversion_delivery_decisions; r public.conversion_requests; BEGIN
 IF NOT public.has_role('APPROVER') THEN RAISE EXCEPTION 'Approver required' USING ERRCODE='42501'; END IF;
 d:=private.lock_conversion_delivery(deliveryid);
 PERFORM private.conversion_object(p,ARRAY['payload_hash','decision','comment']); PERFORM private.conversion_text(p,'comment',2000);
 IF p->>'payload_hash' IS DISTINCT FROM d.payload_hash OR coalesce(p->>'decision','') NOT IN('AUTHORIZE','REJECT') THEN
 RAISE EXCEPTION 'Exact handoff decision required' USING ERRCODE='23514'; END IF;
 IF EXISTS(SELECT 1 FROM public.conversion_delivery_attempts WHERE tenant_id=d.tenant_id AND delivery_id=d.id) THEN
 RAISE EXCEPTION 'Dispatched handoff decision is immutable' USING ERRCODE='23514'; END IF;
 IF p->>'decision'='AUTHORIZE' THEN PERFORM private.guard_conversion_delivery(deliveryid); END IF;
 SELECT * INTO a FROM public.conversion_delivery_decisions WHERE tenant_id=d.tenant_id AND delivery_id=d.id AND decision=p->>'decision' ORDER BY created_at DESC LIMIT 1;
 IF a.id IS NOT NULL THEN
 IF a.comment=p->>'comment' AND (a.decision='REJECT' OR a.expires_at>clock_timestamp()) THEN RETURN a.id; END IF; END IF;
 INSERT INTO public.conversion_delivery_decisions(tenant_id,delivery_id,decision,payload_hash,expires_at,comment,created_by)
 VALUES(d.tenant_id,d.id,p->>'decision',d.payload_hash,clock_timestamp()+interval '15 minutes',p->>'comment',public.context_principal()) RETURNING * INTO a;
 SELECT * INTO r FROM public.conversion_requests WHERE tenant_id=d.tenant_id AND id=d.request_id;
 PERFORM private.audit_conversion(r,'CONVERSION_HANDOFF_DECISION',jsonb_build_object('delivery_id',d.id,'decision',a.decision,'authorization_id',a.id)); RETURN a.id;
 END $$;

CREATE FUNCTION private.lock_conversion_execution(deliveryid uuid) RETURNS void
 LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,public AS $$ BEGIN
 IF NOT EXISTS(SELECT 1 FROM public.conversion_deliveries WHERE tenant_id=public.context_tenant() AND id=deliveryid) THEN
 RAISE EXCEPTION 'Delivery not found' USING ERRCODE='P0002'; END IF;
 -- Match the service session lock before acquiring the consent-series lock.
 -- A direct SQL reconcile must wait for a live dispatcher, not mark it orphaned.
 PERFORM pg_advisory_xact_lock(hashtextextended('conversion-execution:'||public.context_tenant()::text||':'||deliveryid::text,0));
 END $$;

CREATE FUNCTION public.begin_conversion_delivery(deliveryid uuid,action text) RETURNS uuid
 LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,public AS $$
 DECLARE d public.conversion_deliveries; a public.conversion_delivery_attempts; n int; sid uuid; r public.conversion_requests; orphan uuid; BEGIN
 IF NOT public.has_role('OPERATOR') THEN RAISE EXCEPTION 'Operator required' USING ERRCODE='42501'; END IF;
 PERFORM private.lock_conversion_execution(deliveryid);
 d:=private.lock_conversion_delivery(deliveryid);
 IF action NOT IN('DISPATCH','RECONCILE') THEN RAISE EXCEPTION 'Invalid action' USING ERRCODE='23514'; END IF;
 SELECT coalesce(max(attempt),0)+1 INTO n FROM public.conversion_delivery_attempts WHERE tenant_id=d.tenant_id AND delivery_id=d.id AND operation=action;
 IF n>10 OR (action='DISPATCH' AND n>1) THEN RAISE EXCEPTION 'No blind replay or unbounded reads' USING ERRCODE='23514'; END IF;
 IF action='DISPATCH' THEN
 PERFORM private.guard_conversion_delivery(d.id);
 IF NOT EXISTS(SELECT 1 FROM public.conversion_delivery_decisions WHERE tenant_id=d.tenant_id AND delivery_id=d.id AND decision='AUTHORIZE' AND expires_at>clock_timestamp()) THEN
 RAISE EXCEPTION 'Fresh exact outbound authorization required' USING ERRCODE='23514'; END IF;
 ELSE
 IF NOT EXISTS(SELECT 1 FROM public.conversion_delivery_attempts WHERE tenant_id=d.tenant_id AND delivery_id=d.id AND operation='DISPATCH') THEN
 RAISE EXCEPTION 'Only a previously dispatched handoff can be reconciled' USING ERRCODE='23514'; END IF;
 IF EXISTS(SELECT 1 FROM public.conversion_delivery_attempts x JOIN public.conversion_delivery_results y ON y.tenant_id=x.tenant_id AND y.attempt_id=x.id
 WHERE x.tenant_id=d.tenant_id AND x.delivery_id=d.id AND y.status IN('RECEIVED','REVOKED')) THEN
 RAISE EXCEPTION 'Confirmed receipt already exists' USING ERRCODE='23514'; END IF;
 -- A read can recover after process interruption, but never retries a POST.
 FOR orphan IN SELECT x.id FROM public.conversion_delivery_attempts x WHERE x.tenant_id=d.tenant_id AND x.delivery_id=d.id
 AND NOT EXISTS(SELECT 1 FROM public.conversion_delivery_results y WHERE y.tenant_id=x.tenant_id AND y.attempt_id=x.id) LOOP
 PERFORM public.finish_conversion_delivery(orphan,'UNKNOWN_OUTCOME',NULL,NULL);
 END LOOP;
 END IF;
 INSERT INTO public.skill_runs(tenant_id,workflow_run_id,skill_identifier,skill_version,input_schema_version,output_schema_version,provider,model,adapter,attempt,step_key,input_hash,started_at,status,input_tokens,output_tokens,cost,is_mock)
 VALUES(d.tenant_id,d.workflow_run_id,'conversion.'||lower(action),'1.0.0',1,1,'deterministic','none','signed-webhook-v1',n,'conversion.'||lower(action)||':'||d.id||':'||n,d.payload_hash,clock_timestamp(),'RUNNING',0,0,0,false) RETURNING id INTO sid;
 INSERT INTO public.conversion_delivery_attempts(tenant_id,delivery_id,operation,attempt,skill_run_id,created_by)
 VALUES(d.tenant_id,d.id,action,n,sid,public.context_principal()) RETURNING * INTO a;
 SELECT * INTO r FROM public.conversion_requests WHERE tenant_id=d.tenant_id AND id=d.request_id;
 PERFORM private.audit_conversion(r,'CONVERSION_HANDOFF_ATTEMPT_STARTED',jsonb_build_object('delivery_id',d.id,'attempt_id',a.id,'skill_run_id',sid,'operation',action)); RETURN a.id;
 END $$;

-- Held through the bounded HTTP call. Revocation and new revisions take the same series lock.
CREATE FUNCTION public.guard_conversion_dispatch(attemptid uuid) RETURNS void
 LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,public AS $$
 DECLARE a public.conversion_delivery_attempts; d public.conversion_deliveries; BEGIN
 IF NOT public.has_role('OPERATOR') THEN RAISE EXCEPTION 'Operator required' USING ERRCODE='42501'; END IF;
 SELECT * INTO a FROM public.conversion_delivery_attempts WHERE tenant_id=public.context_tenant() AND id=attemptid;
 IF a.id IS NULL THEN RAISE EXCEPTION 'Attempt not found' USING ERRCODE='P0002'; END IF;
 PERFORM private.lock_conversion_execution(a.delivery_id);
 d:=private.lock_conversion_delivery(a.delivery_id);
 IF EXISTS(SELECT 1 FROM public.conversion_delivery_results WHERE tenant_id=a.tenant_id AND attempt_id=a.id)
 OR a.created_at<clock_timestamp()-interval '2 minutes' THEN RAISE EXCEPTION 'Attempt cannot be replayed' USING ERRCODE='23514'; END IF;
 IF a.operation='DISPATCH' THEN
 PERFORM private.guard_conversion_delivery(d.id);
 IF NOT EXISTS(SELECT 1 FROM public.conversion_delivery_decisions WHERE tenant_id=d.tenant_id AND delivery_id=d.id AND decision='AUTHORIZE' AND expires_at>clock_timestamp()) THEN
 RAISE EXCEPTION 'Fresh outbound authorization required' USING ERRCODE='23514'; END IF;
 END IF;
 END $$;

CREATE FUNCTION public.finish_conversion_delivery(attemptid uuid,resultstatus text,body jsonb,sig text) RETURNS uuid
 LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,public AS $$
 DECLARE a public.conversion_delivery_attempts; d public.conversion_deliveries; result public.conversion_delivery_results; r public.conversion_requests; expected text; finished timestamptz; BEGIN
 IF NOT public.has_role('OPERATOR') THEN RAISE EXCEPTION 'Operator required' USING ERRCODE='42501'; END IF;
 SELECT * INTO a FROM public.conversion_delivery_attempts WHERE tenant_id=public.context_tenant() AND id=attemptid;
 IF a.id IS NULL THEN RAISE EXCEPTION 'Attempt not found' USING ERRCODE='P0002'; END IF;
 PERFORM private.lock_conversion_execution(a.delivery_id);
 d:=private.lock_conversion_delivery(a.delivery_id);
 SELECT * INTO result FROM public.conversion_delivery_results WHERE tenant_id=d.tenant_id AND attempt_id=a.id;
 IF result.id IS NOT NULL THEN
 IF result.status IS DISTINCT FROM resultstatus OR result.receipt IS DISTINCT FROM body OR result.signature IS DISTINCT FROM sig THEN
 RAISE EXCEPTION 'Immutable attempt result' USING ERRCODE='23505'; END IF; RETURN result.id; END IF;
 IF resultstatus IN('RECEIVED','REVOKED','NOT_FOUND') THEN
 PERFORM private.conversion_object(body,ARRAY['schema_version','delivery_id','payload_hash','status','receipt_reference','audit_completed']);
 PERFORM private.conversion_text(body,'receipt_reference',128,'^[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}$');
 SELECT encode(public.hmac(convert_to(private.visual_hash(body),'UTF8'),signing_key,'sha256'),'hex') INTO expected FROM private.conversion_transport_keys WHERE transport_id=d.transport_id;
 IF sig IS DISTINCT FROM expected OR body->>'delivery_id' IS DISTINCT FROM d.id::text OR body->>'payload_hash' IS DISTINCT FROM d.payload_hash
 OR body->>'status' IS DISTINCT FROM resultstatus OR body->'schema_version' IS DISTINCT FROM '1'::jsonb OR body->'audit_completed' IS DISTINCT FROM 'false'::jsonb
 OR (resultstatus='RECEIVED' AND d.operation<>'SEND') OR (resultstatus='REVOKED' AND d.operation<>'REVOKE') OR (resultstatus='NOT_FOUND' AND a.operation<>'RECONCILE') THEN
 RAISE EXCEPTION 'Authentic exact destination receipt required' USING ERRCODE='23514'; END IF;
 ELSIF resultstatus NOT IN('UNKNOWN_OUTCOME','NOT_SENT') OR body IS NOT NULL OR sig IS NOT NULL THEN
 RAISE EXCEPTION 'Invalid outcome' USING ERRCODE='23514'; END IF;
 INSERT INTO public.conversion_delivery_results(tenant_id,attempt_id,status,receipt,signature) VALUES(d.tenant_id,a.id,resultstatus,body,sig) RETURNING * INTO result;
 finished:=clock_timestamp();
 UPDATE public.skill_runs SET status=CASE WHEN resultstatus IN('RECEIVED','REVOKED','NOT_FOUND') THEN 'SUCCEEDED' ELSE 'FAILED' END,
 ended_at=finished,latency_ms=greatest(0,extract(epoch FROM finished-started_at)*1000),retryable=false,
 error_category=CASE WHEN resultstatus IN('UNKNOWN_OUTCOME','NOT_SENT') THEN resultstatus ELSE NULL END,
 output=jsonb_build_object('delivery_id',d.id,'attempt_id',a.id,'status',resultstatus,'audit_completed',false)
 WHERE tenant_id=d.tenant_id AND id=a.skill_run_id;
 INSERT INTO public.cost_events(tenant_id,workflow_run_id,skill_run_id,provider,model,input_tokens,output_tokens,cost,currency,price_version)
 VALUES(d.tenant_id,d.workflow_run_id,a.skill_run_id,'deterministic','none',0,0,0,'USD','transport-zero-v1');
 SELECT * INTO r FROM public.conversion_requests WHERE tenant_id=d.tenant_id AND id=d.request_id;
 PERFORM private.audit_conversion(r,'CONVERSION_HANDOFF_OUTCOME',jsonb_build_object('delivery_id',d.id,'attempt_id',a.id,'status',resultstatus,'network_performed',resultstatus<>'NOT_SENT','delivered',resultstatus='RECEIVED'));
 RETURN result.id;
 END $$;
REVOKE ALL ON FUNCTION private.lock_conversion_delivery(uuid),private.guard_conversion_delivery(uuid),private.lock_conversion_execution(uuid) FROM PUBLIC,mediaos_runtime;
REVOKE ALL ON FUNCTION public.register_business_identity(jsonb,text,text),public.review_business_identity(uuid,jsonb),public.create_conversion_delivery(uuid,jsonb,text,text),
 public.create_conversion_revocation(uuid,text,text),public.authorize_conversion_delivery(uuid,jsonb),public.begin_conversion_delivery(uuid,text),
 public.guard_conversion_dispatch(uuid),public.finish_conversion_delivery(uuid,text,jsonb,text) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION public.register_business_identity(jsonb,text,text),public.review_business_identity(uuid,jsonb),public.create_conversion_delivery(uuid,jsonb,text,text),
 public.create_conversion_revocation(uuid,text,text),public.authorize_conversion_delivery(uuid,jsonb),public.begin_conversion_delivery(uuid,text),
 public.guard_conversion_dispatch(uuid),public.finish_conversion_delivery(uuid,text,jsonb,text) TO mediaos_runtime;
