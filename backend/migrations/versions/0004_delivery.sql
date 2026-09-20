-- Internal dry-run preflight only. There is no live target, network dispatch,
-- platform identifier or publish transition in this migration.
CREATE TABLE delivery_targets (
 id uuid PRIMARY KEY DEFAULT gen_random_uuid(), tenant_id uuid NOT NULL REFERENCES tenants,
 target_key text NOT NULL CHECK(target_key ~ '^[A-Za-z0-9][A-Za-z0-9_-]{0,63}$'),
 version int NOT NULL CHECK(version>0), schema_version int NOT NULL CHECK(schema_version=1),
 display_name text NOT NULL CHECK(length(btrim(display_name)) BETWEEN 1 AND 200),
 platform text NOT NULL CHECK(length(btrim(platform)) BETWEEN 1 AND 64),
 adapter_key text NOT NULL CHECK(adapter_key='dry-run-v1'),
 mode text NOT NULL DEFAULT 'DRY_RUN' CHECK(mode='DRY_RUN'), enabled boolean NOT NULL DEFAULT false,
 payload jsonb NOT NULL, content_hash text NOT NULL CHECK(content_hash ~ '^[0-9a-f]{64}$'),
 created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
 UNIQUE(tenant_id,id), UNIQUE(tenant_id,target_key,version),
 CHECK(payload='{"schema_version":1,"mode":"DRY_RUN","adapter_version":"dry-run-v1"}'::jsonb),
 CHECK(content_hash=private.visual_hash(payload))
);

ALTER TABLE visual_approval_records ADD CONSTRAINT visual_approval_delivery_lineage
 UNIQUE(tenant_id,id,render_run_id,workflow_run_id,asset_version_id,research_version_id,
 qa_report_id,content_approval_record_id,manifest_hash);

CREATE TABLE delivery_runs (
 id uuid PRIMARY KEY DEFAULT gen_random_uuid(), tenant_id uuid NOT NULL REFERENCES tenants,
 workflow_run_id uuid NOT NULL, render_run_id uuid NOT NULL, target_id uuid NOT NULL,
 asset_version_id uuid NOT NULL, research_version_id uuid NOT NULL, qa_report_id uuid NOT NULL,
 content_approval_record_id uuid NOT NULL, visual_approval_record_id uuid NOT NULL,
 manifest_hash text NOT NULL CHECK(manifest_hash ~ '^[0-9a-f]{64}$'),
 mode text NOT NULL DEFAULT 'DRY_RUN' CHECK(mode='DRY_RUN'), created_by uuid NOT NULL,
 correlation_id uuid NOT NULL, idempotency_key text NOT NULL CHECK(length(idempotency_key) BETWEEN 1 AND 128),
 input_hash text NOT NULL CHECK(input_hash ~ '^[0-9a-f]{64}$'),
 status text NOT NULL DEFAULT 'CREATED'
   CHECK(status IN ('CREATED','VALIDATING','DRY_RUN_COMPLETE','BLOCKED','RETRY_WAIT','FAILED')),
 attempt_count int NOT NULL DEFAULT 0 CHECK(attempt_count BETWEEN 0 AND 3),
 retry_at timestamptz, error_category text CHECK(error_category ~ '^[A-Z][A-Z0-9_]{0,79}$'),
 started_at timestamptz, ended_at timestamptz, created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
 payload jsonb, payload_hash text CHECK(payload_hash ~ '^[0-9a-f]{64}$'),
 receipt jsonb, receipt_hash text CHECK(receipt_hash ~ '^[0-9a-f]{64}$'),
 UNIQUE(tenant_id,id), UNIQUE(tenant_id,idempotency_key),
 FOREIGN KEY(tenant_id,workflow_run_id) REFERENCES workflow_runs(tenant_id,id),
 FOREIGN KEY(tenant_id,target_id) REFERENCES delivery_targets(tenant_id,id),
 FOREIGN KEY(tenant_id,render_run_id,workflow_run_id,asset_version_id,research_version_id,qa_report_id,manifest_hash)
   REFERENCES render_runs(tenant_id,id,workflow_run_id,asset_version_id,research_version_id,qa_report_id,manifest_hash),
 FOREIGN KEY(tenant_id,content_approval_record_id,workflow_run_id,asset_version_id,research_version_id,qa_report_id)
   REFERENCES approval_records(tenant_id,id,workflow_run_id,asset_version_id,research_version_id,qa_report_id),
 FOREIGN KEY(tenant_id,visual_approval_record_id,render_run_id,workflow_run_id,asset_version_id,research_version_id,
   qa_report_id,content_approval_record_id,manifest_hash)
   REFERENCES visual_approval_records(tenant_id,id,render_run_id,workflow_run_id,asset_version_id,research_version_id,
   qa_report_id,content_approval_record_id,manifest_hash),
 FOREIGN KEY(tenant_id,created_by) REFERENCES tenant_memberships(tenant_id,principal_id),
 CHECK((status='DRY_RUN_COMPLETE')=(payload IS NOT NULL)),
 CHECK((payload IS NULL)=(payload_hash IS NULL)),
 CHECK((payload IS NULL)=(receipt IS NULL)),
 CHECK((receipt IS NULL)=(receipt_hash IS NULL)),
 CHECK(payload IS NULL OR (jsonb_typeof(payload)='object' AND octet_length(payload::text)<=262144)),
 CHECK(receipt IS NULL OR (jsonb_typeof(receipt)='object' AND octet_length(receipt::text)<=262144)),
 CHECK((status='RETRY_WAIT')=(retry_at IS NOT NULL)),
 CHECK((status IN ('DRY_RUN_COMPLETE','BLOCKED','FAILED'))=(ended_at IS NOT NULL)),
 CHECK(status<>'VALIDATING' OR (attempt_count>0 AND started_at IS NOT NULL)),
 CHECK(status<>'DRY_RUN_COMPLETE' OR (attempt_count>0 AND started_at IS NOT NULL)),
 CHECK(started_at IS NULL OR started_at>=created_at),
 CHECK(ended_at IS NULL OR ended_at>=coalesce(started_at,created_at))
);
CREATE INDEX delivery_workflow_history ON delivery_runs(tenant_id,workflow_run_id,created_at);
CREATE INDEX delivery_retry_due ON delivery_runs(tenant_id,status,retry_at) WHERE status='RETRY_WAIT';

CREATE TRIGGER immutable BEFORE UPDATE OR DELETE ON delivery_targets
 FOR EACH ROW EXECUTE FUNCTION immutable_record();
CREATE FUNCTION private.guard_delivery_target_version() RETURNS trigger
 LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,public AS $$
 DECLARE expected int;
 BEGIN
 PERFORM pg_advisory_xact_lock(hashtextextended('delivery-target:'||NEW.tenant_id::text||':'||NEW.target_key,0));
 SELECT coalesce(max(version),0)+1 INTO expected FROM public.delivery_targets
 WHERE tenant_id=NEW.tenant_id AND target_key=NEW.target_key;
 IF NEW.version<>expected THEN RAISE EXCEPTION 'Delivery target revision must be consecutive' USING ERRCODE='23514'; END IF;
 RETURN NEW;
 END $$;
CREATE TRIGGER delivery_target_version_guard BEFORE INSERT ON delivery_targets
 FOR EACH ROW EXECUTE FUNCTION private.guard_delivery_target_version();

-- Final receipts are historical observations, never a reusable authorization to
-- dispatch. They cannot be changed into a new result or a live delivery.
CREATE FUNCTION private.guard_terminal_delivery() RETURNS trigger
 LANGUAGE plpgsql SET search_path=pg_catalog,public AS $$
 BEGIN
 IF TG_OP='DELETE' OR OLD.status IN ('DRY_RUN_COMPLETE','BLOCKED','FAILED') THEN
 RAISE EXCEPTION 'Delivery history is immutable; create a new request' USING ERRCODE='23514'; END IF;
 RETURN NEW;
 END $$;
CREATE TRIGGER immutable_delivery_history BEFORE UPDATE OR DELETE ON delivery_runs
 FOR EACH ROW EXECUTE FUNCTION private.guard_terminal_delivery();
DO $$ DECLARE t text; BEGIN
 FOREACH t IN ARRAY ARRAY['delivery_targets','delivery_runs'] LOOP
 EXECUTE format('ALTER TABLE %I ENABLE ROW LEVEL SECURITY',t);
 EXECUTE format('ALTER TABLE %I FORCE ROW LEVEL SECURITY',t);
 EXECUTE format('CREATE POLICY tenant_boundary ON %I USING (tenant_id=public.context_tenant()) WITH CHECK (tenant_id=public.context_tenant())',t);
 EXECUTE format('REVOKE ALL ON %I FROM PUBLIC,mediaos_runtime',t);
 EXECUTE format('GRANT SELECT ON %I TO mediaos_runtime',t);
 END LOOP;
 END $$;

CREATE FUNCTION private.audit_delivery(d public.delivery_runs, event text, before_status text, after_status text,
 extra jsonb DEFAULT '{}'::jsonb) RETURNS void
 LANGUAGE sql SECURITY DEFINER SET search_path=pg_catalog,public AS $$
 INSERT INTO public.audit_events(tenant_id,workflow_run_id,actor_id,event_type,correlation_id,details)
 VALUES(d.tenant_id,d.workflow_run_id,public.context_principal(),event,d.correlation_id,
 jsonb_build_object('delivery_run_id',d.id,'render_run_id',d.render_run_id,'target_id',d.target_id,
 'mode','DRY_RUN','from_delivery_status',before_status,'to_delivery_status',after_status)||extra) $$;

CREATE FUNCTION private.check_delivery_target(targetid uuid) RETURNS void
 LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,public AS $$
 DECLARE target public.delivery_targets;
 BEGIN
 SELECT * INTO target FROM public.delivery_targets WHERE tenant_id=public.context_tenant() AND id=targetid;
 IF target.id IS NULL THEN RAISE EXCEPTION 'Delivery target not found' USING ERRCODE='P0002'; END IF;
 PERFORM pg_advisory_xact_lock(hashtextextended('delivery-target:'||target.tenant_id::text||':'||target.target_key,0));
 IF NOT target.enabled OR target.mode<>'DRY_RUN' OR target.adapter_key<>'dry-run-v1'
 OR EXISTS(SELECT 1 FROM public.delivery_targets newer WHERE newer.tenant_id=target.tenant_id
 AND newer.target_key=target.target_key AND newer.version>target.version) THEN
 RAISE EXCEPTION 'Latest enabled dry-run target required' USING ERRCODE='23514'; END IF;
 END $$;

-- Caller locks the workflow first. The shared export guard then locks the render,
-- influencer configuration and opportunity, and validates current byte-manifest
-- lineage and source policy. Service code separately checks the actual PNG/ZIP bytes.
CREATE FUNCTION private.check_delivery_lineage(d public.delivery_runs) RETURNS void
 LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,public AS $$
 DECLARE r public.render_runs;
 BEGIN
 IF d.tenant_id IS DISTINCT FROM public.context_tenant() OR d.mode<>'DRY_RUN' THEN
 RAISE EXCEPTION 'Owned dry-run delivery required' USING ERRCODE='42501'; END IF;
 PERFORM public.check_render_export(d.render_run_id);
 -- Match the service, which obtains export/workflow/source locks before packaging
 -- bytes and only then calls completion. Target-last prevents cross-workflow
 -- deadlocks between deliveries that share an influencer and target.
 PERFORM private.check_delivery_target(d.target_id);
 SELECT * INTO r FROM public.render_runs WHERE tenant_id=d.tenant_id AND id=d.render_run_id;
 IF r.workflow_run_id IS DISTINCT FROM d.workflow_run_id OR r.asset_version_id IS DISTINCT FROM d.asset_version_id
 OR r.research_version_id IS DISTINCT FROM d.research_version_id OR r.qa_report_id IS DISTINCT FROM d.qa_report_id
 OR r.manifest_hash IS DISTINCT FROM d.manifest_hash
 OR NOT EXISTS(SELECT 1 FROM public.visual_approval_records WHERE tenant_id=d.tenant_id AND id=d.visual_approval_record_id
 AND render_run_id=r.id AND content_approval_record_id=d.content_approval_record_id AND decision='APPROVE') THEN
 RAISE EXCEPTION 'Delivery approval lineage mismatch' USING ERRCODE='23514'; END IF;
 END $$;

CREATE FUNCTION public.start_delivery(renderid uuid, targetid uuid, key text, inputhash text) RETURNS uuid
 LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,public AS $$
 DECLARE r public.render_runs; w public.workflow_runs; approval public.visual_approval_records;
 d public.delivery_runs; expected_hash text;
 BEGIN
 IF NOT public.has_role('OPERATOR') THEN RAISE EXCEPTION 'Operator required' USING ERRCODE='42501'; END IF;
 IF renderid IS NULL OR targetid IS NULL OR key IS NULL OR length(key) NOT BETWEEN 1 AND 128 THEN
 RAISE EXCEPTION 'Render, target and idempotency key required' USING ERRCODE='23514'; END IF;
 SELECT * INTO r FROM public.render_runs WHERE tenant_id=public.context_tenant() AND id=renderid;
 IF r.id IS NULL THEN RAISE EXCEPTION 'Render not found' USING ERRCODE='P0002'; END IF;
 SELECT * INTO w FROM public.workflow_runs WHERE tenant_id=r.tenant_id AND id=r.workflow_run_id FOR UPDATE;
 expected_hash:=private.visual_hash(jsonb_build_object('render_run_id',renderid,'target_id',targetid,
 'manifest_hash',r.manifest_hash,'mode','DRY_RUN','adapter_version','dry-run-v1'));
 IF inputhash IS DISTINCT FROM expected_hash THEN
 RAISE EXCEPTION 'Delivery input hash differs from pinned request' USING ERRCODE='23514'; END IF;
 SELECT * INTO d FROM public.delivery_runs WHERE tenant_id=r.tenant_id AND idempotency_key=key;
 IF d.id IS NOT NULL THEN
 IF d.input_hash<>inputhash OR d.render_run_id<>renderid OR d.target_id<>targetid THEN
 RAISE EXCEPTION 'Delivery idempotency payload conflict' USING ERRCODE='23505'; END IF;
 -- Replaying a completed key reads immutable history; it does not execute again.
 RETURN d.id;
 END IF;
 PERFORM public.check_render_export(renderid);
 PERFORM private.check_delivery_target(targetid);
 SELECT * INTO approval FROM public.visual_approval_records WHERE tenant_id=r.tenant_id
 AND render_run_id=renderid AND manifest_hash=r.manifest_hash AND decision='APPROVE';
 IF approval.id IS NULL THEN RAISE EXCEPTION 'Exact visual approval required' USING ERRCODE='23514'; END IF;
 INSERT INTO public.delivery_runs(tenant_id,workflow_run_id,render_run_id,target_id,asset_version_id,research_version_id,
 qa_report_id,content_approval_record_id,visual_approval_record_id,manifest_hash,created_by,correlation_id,idempotency_key,input_hash)
 VALUES(r.tenant_id,w.id,r.id,targetid,r.asset_version_id,r.research_version_id,r.qa_report_id,
 approval.content_approval_record_id,approval.id,r.manifest_hash,public.context_principal(),w.correlation_id,key,inputhash)
 ON CONFLICT(tenant_id,idempotency_key) DO NOTHING RETURNING * INTO d;
 IF d.id IS NULL THEN
 SELECT * INTO d FROM public.delivery_runs WHERE tenant_id=r.tenant_id AND idempotency_key=key;
 IF d.input_hash<>inputhash OR d.render_run_id<>renderid OR d.target_id<>targetid THEN
 RAISE EXCEPTION 'Delivery idempotency payload conflict' USING ERRCODE='23505'; END IF;
 ELSE PERFORM private.audit_delivery(d,'DELIVERY_CREATED',NULL,'CREATED');
 END IF;
 RETURN d.id;
 END $$;

CREATE FUNCTION public.claim_delivery(deliveryid uuid) RETURNS void
 LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,public AS $$
 DECLARE d public.delivery_runs; rid uuid;
 BEGIN
 IF NOT public.has_role('OPERATOR') THEN RAISE EXCEPTION 'Operator required' USING ERRCODE='42501'; END IF;
 SELECT workflow_run_id INTO rid FROM public.delivery_runs WHERE tenant_id=public.context_tenant() AND id=deliveryid;
 IF rid IS NULL THEN RAISE EXCEPTION 'Delivery not found' USING ERRCODE='P0002'; END IF;
 PERFORM 1 FROM public.workflow_runs WHERE tenant_id=public.context_tenant() AND id=rid FOR UPDATE;
 SELECT * INTO d FROM public.delivery_runs WHERE tenant_id=public.context_tenant() AND id=deliveryid FOR UPDATE;
 IF d.status NOT IN ('CREATED','VALIDATING','RETRY_WAIT') OR d.attempt_count>=3 THEN
 RAISE EXCEPTION 'Delivery attempt is terminal or exhausted' USING ERRCODE='23514'; END IF;
 IF d.retry_at>clock_timestamp() THEN RAISE EXCEPTION 'Delivery retry backoff is active' USING ERRCODE='23514'; END IF;
 PERFORM private.check_delivery_lineage(d);
 UPDATE public.delivery_runs SET status='VALIDATING',attempt_count=attempt_count+1,retry_at=NULL,
 error_category=NULL,started_at=coalesce(started_at,clock_timestamp()) WHERE id=d.id;
 PERFORM private.audit_delivery(d,CASE WHEN d.attempt_count=0 THEN 'DELIVERY_STARTED' ELSE 'DELIVERY_RESUMED' END,
 d.status,'VALIDATING',jsonb_build_object('attempt',d.attempt_count+1));
 END $$;

CREATE FUNCTION private.validate_delivery_result(d public.delivery_runs, p jsonb, ph text, receipt jsonb, rh text)
 RETURNS void LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,public AS $$
 DECLARE r public.render_runs; slide jsonb; original jsonb; idx int:=0; media_hashes jsonb:='[]'::jsonb;
 validated_time timestamptz;
 plan_keys text[]:=ARRAY['schema_version','mode','adapter_version','target_id','render_run_id','manifest_hash',
 'language','caption','slides','package_sha256','post_id'];
 receipt_keys text[]:=ARRAY['schema_version','status','mode','adapter_version','network_performed','package_sha256',
 'payload_sha256','manifest_hash','caption_sha256','slide_sha256','validated_at','post_id','published_at'];
 BEGIN
 SELECT * INTO r FROM public.render_runs WHERE tenant_id=d.tenant_id AND id=d.render_run_id;
 IF p IS NULL OR jsonb_typeof(p) IS DISTINCT FROM 'object' OR octet_length(p::text)>262144
 OR NOT(p ?& plan_keys) OR p-plan_keys<>'{}'::jsonb
 OR ph IS DISTINCT FROM private.visual_hash(p)
 OR p->'schema_version' IS DISTINCT FROM '1'::jsonb OR p->>'mode' IS DISTINCT FROM 'DRY_RUN'
 OR p->>'adapter_version' IS DISTINCT FROM 'dry-run-v1' OR p->'post_id' IS DISTINCT FROM 'null'::jsonb
 OR p->>'target_id' IS DISTINCT FROM d.target_id::text OR p->>'render_run_id' IS DISTINCT FROM r.id::text
 OR p->>'manifest_hash' IS DISTINCT FROM r.manifest_hash
 OR p->>'language' IS DISTINCT FROM r.manifest->>'language' OR p->'caption' IS DISTINCT FROM r.manifest->'caption'
 OR jsonb_typeof(p->'slides') IS DISTINCT FROM 'array'
 OR jsonb_array_length(p->'slides')<>jsonb_array_length(r.manifest->'slides')
 OR coalesce(p->>'package_sha256','') !~ '^[0-9a-f]{64}$' THEN
 RAISE EXCEPTION 'Delivery payload differs from the exact approved render' USING ERRCODE='23514'; END IF;
 FOR slide IN SELECT value FROM jsonb_array_elements(p->'slides') LOOP
 idx:=idx+1; original:=r.manifest->'slides'->(idx-1);
 IF jsonb_typeof(slide) IS DISTINCT FROM 'object'
 OR NOT(slide ?& ARRAY['index','filename','sha256','width','height','media_type'])
 OR slide-ARRAY['index','filename','sha256','width','height','media_type']<>'{}'::jsonb
 OR slide->'index' IS DISTINCT FROM to_jsonb(idx)
 OR slide->>'filename' IS DISTINCT FROM original->>'filename'
 OR slide->>'sha256' IS DISTINCT FROM original->>'sha256'
 OR slide->'width' IS DISTINCT FROM original->'width' OR slide->'height' IS DISTINCT FROM original->'height'
 OR slide->>'media_type' IS DISTINCT FROM 'image/png' THEN
 RAISE EXCEPTION 'Delivery media differs from approved ordered PNGs' USING ERRCODE='23514'; END IF;
 media_hashes:=media_hashes||jsonb_build_array(slide->>'sha256');
 END LOOP;
 IF receipt IS NULL OR jsonb_typeof(receipt) IS DISTINCT FROM 'object' OR octet_length(receipt::text)>262144
 OR NOT(receipt ?& receipt_keys) OR receipt-receipt_keys<>'{}'::jsonb
 OR rh IS DISTINCT FROM private.visual_hash(receipt)
 OR receipt->'schema_version' IS DISTINCT FROM '1'::jsonb
 OR receipt->>'status' IS DISTINCT FROM 'DRY_RUN_COMPLETE' OR receipt->>'mode' IS DISTINCT FROM 'DRY_RUN'
 OR receipt->>'adapter_version' IS DISTINCT FROM 'dry-run-v1'
 OR receipt->'network_performed' IS DISTINCT FROM 'false'::jsonb
 OR receipt->'post_id' IS DISTINCT FROM 'null'::jsonb OR receipt->'published_at' IS DISTINCT FROM 'null'::jsonb
 OR receipt->>'package_sha256' IS DISTINCT FROM p->>'package_sha256'
 OR receipt->>'payload_sha256' IS DISTINCT FROM ph OR receipt->>'manifest_hash' IS DISTINCT FROM d.manifest_hash
 OR receipt->>'caption_sha256' IS DISTINCT FROM encode(public.digest(p->'caption'->>'text','sha256'),'hex')
 OR receipt->'slide_sha256' IS DISTINCT FROM media_hashes
 OR jsonb_typeof(receipt->'validated_at') IS DISTINCT FROM 'string'
 OR receipt->>'validated_at' !~ '(Z|\+00:00)$' THEN
 RAISE EXCEPTION 'Delivery receipt must describe the checked dry-run package only' USING ERRCODE='23514'; END IF;
 BEGIN validated_time:=(receipt->>'validated_at')::timestamptz;
 EXCEPTION WHEN invalid_datetime_format OR datetime_field_overflow THEN
 RAISE EXCEPTION 'Invalid delivery validation timestamp' USING ERRCODE='23514'; END;
 IF validated_time<d.started_at OR validated_time>clock_timestamp() THEN
 RAISE EXCEPTION 'Delivery receipt timestamp is outside this execution' USING ERRCODE='23514'; END IF;
 END $$;

CREATE FUNCTION public.complete_delivery(deliveryid uuid, payload jsonb, payloadhash text, receipt jsonb, receipthash text)
 RETURNS void LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,public AS $$
 DECLARE d public.delivery_runs; rid uuid;
 BEGIN
 IF NOT public.has_role('OPERATOR') THEN RAISE EXCEPTION 'Operator required' USING ERRCODE='42501'; END IF;
 SELECT workflow_run_id INTO rid FROM public.delivery_runs WHERE tenant_id=public.context_tenant() AND id=deliveryid;
 IF rid IS NULL THEN RAISE EXCEPTION 'Delivery not found' USING ERRCODE='P0002'; END IF;
 PERFORM 1 FROM public.workflow_runs WHERE tenant_id=public.context_tenant() AND id=rid FOR UPDATE;
 SELECT * INTO d FROM public.delivery_runs WHERE tenant_id=public.context_tenant() AND id=deliveryid FOR UPDATE;
 IF d.status<>'VALIDATING' THEN RAISE EXCEPTION 'Active delivery validation required' USING ERRCODE='23514'; END IF;
 PERFORM private.check_delivery_lineage(d);
 PERFORM private.validate_delivery_result(d,payload,payloadhash,receipt,receipthash);
 UPDATE public.delivery_runs SET status='DRY_RUN_COMPLETE',payload=complete_delivery.payload,payload_hash=payloadhash,
 receipt=complete_delivery.receipt,receipt_hash=receipthash,ended_at=clock_timestamp(),retry_at=NULL,error_category=NULL WHERE id=d.id;
 PERFORM private.audit_delivery(d,'DELIVERY_DRY_RUN_COMPLETED','VALIDATING','DRY_RUN_COMPLETE',
 jsonb_build_object('payload_hash',payloadhash,'receipt_hash',receipthash,'network_performed',false));
 END $$;

CREATE FUNCTION public.fail_delivery(deliveryid uuid, category text, retryable boolean) RETURNS void
 LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,public AS $$
 DECLARE d public.delivery_runs; rid uuid; outcome text; next_retry timestamptz;
 BEGIN
 IF NOT public.has_role('OPERATOR') THEN RAISE EXCEPTION 'Operator required' USING ERRCODE='42501'; END IF;
 IF category IS NULL OR category !~ '^[A-Z][A-Z0-9_]{0,79}$' OR retryable IS NULL
 OR (category='POLICY_BLOCKED' AND retryable) THEN
 RAISE EXCEPTION 'Valid deterministic failure category and retry policy required' USING ERRCODE='23514'; END IF;
 SELECT workflow_run_id INTO rid FROM public.delivery_runs WHERE tenant_id=public.context_tenant() AND id=deliveryid;
 IF rid IS NULL THEN RAISE EXCEPTION 'Delivery not found' USING ERRCODE='P0002'; END IF;
 PERFORM 1 FROM public.workflow_runs WHERE tenant_id=public.context_tenant() AND id=rid FOR UPDATE;
 SELECT * INTO d FROM public.delivery_runs WHERE tenant_id=public.context_tenant() AND id=deliveryid FOR UPDATE;
 IF d.status NOT IN ('CREATED','VALIDATING','RETRY_WAIT') THEN
 RAISE EXCEPTION 'Delivery is already terminal' USING ERRCODE='23514'; END IF;
 outcome:=CASE WHEN category='POLICY_BLOCKED' THEN 'BLOCKED'
 WHEN retryable AND d.attempt_count<3 THEN 'RETRY_WAIT' ELSE 'FAILED' END;
 IF outcome='RETRY_WAIT' THEN
 next_retry:=clock_timestamp()+make_interval(secs=>power(2,greatest(d.attempt_count-1,0))::int);
 next_retry:=greatest(next_retry,d.retry_at);
 END IF;
 UPDATE public.delivery_runs SET status=outcome,error_category=category,retry_at=next_retry,
 ended_at=CASE WHEN outcome='RETRY_WAIT' THEN NULL ELSE clock_timestamp() END WHERE id=d.id;
 PERFORM private.audit_delivery(d,CASE WHEN outcome='RETRY_WAIT' THEN 'DELIVERY_RETRY_SCHEDULED'
 WHEN outcome='BLOCKED' THEN 'DELIVERY_BLOCKED' ELSE 'DELIVERY_FAILED' END,d.status,outcome,
 jsonb_build_object('attempt',d.attempt_count,'error_category',category,'retryable',outcome='RETRY_WAIT','retry_at',next_retry));
 END $$;

REVOKE ALL ON FUNCTION private.guard_delivery_target_version(),private.guard_terminal_delivery(),
 private.audit_delivery(public.delivery_runs,text,text,text,jsonb),private.check_delivery_target(uuid),
 private.check_delivery_lineage(public.delivery_runs),private.validate_delivery_result(public.delivery_runs,jsonb,text,jsonb,text)
 FROM PUBLIC,mediaos_runtime;
REVOKE ALL ON FUNCTION public.start_delivery(uuid,uuid,text,text),public.claim_delivery(uuid),
 public.complete_delivery(uuid,jsonb,text,jsonb,text),public.fail_delivery(uuid,text,boolean) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION public.start_delivery(uuid,uuid,text,text),public.claim_delivery(uuid),
 public.complete_delivery(uuid,jsonb,text,jsonb,text),public.fail_delivery(uuid,text,boolean) TO mediaos_runtime;
