-- Internal manual event review only. No sends, platform identity or consent inference.
CREATE TABLE community_events (
 id uuid PRIMARY KEY DEFAULT gen_random_uuid(), tenant_id uuid NOT NULL REFERENCES tenants,
 workflow_run_id uuid NOT NULL, mode text NOT NULL CHECK(mode IN ('MANUAL','FIXTURE')),
 origin text NOT NULL CHECK(length(origin) BETWEEN 1 AND 512 AND length(btrim(origin))>0),
 participant_reference text NOT NULL CHECK(length(participant_reference) BETWEEN 1 AND 128 AND length(btrim(participant_reference))>0),
 comment_text text NOT NULL CHECK(length(comment_text) BETWEEN 1 AND 4000 AND length(btrim(comment_text))>0),
 captured_at timestamptz NOT NULL, schema_version int NOT NULL CHECK(schema_version=1),
 payload jsonb NOT NULL, content_hash text NOT NULL CHECK(content_hash=private.visual_hash(payload)),
 created_by uuid NOT NULL, correlation_id uuid NOT NULL,
 idempotency_key text NOT NULL CHECK(length(idempotency_key) BETWEEN 1 AND 128),
 input_hash text NOT NULL CHECK(input_hash ~ '^[0-9a-f]{64}$'),
 created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
 UNIQUE(tenant_id,id), UNIQUE(tenant_id,id,workflow_run_id), UNIQUE(tenant_id,idempotency_key),
 FOREIGN KEY(tenant_id,workflow_run_id) REFERENCES workflow_runs(tenant_id,id),
 FOREIGN KEY(tenant_id,created_by) REFERENCES tenant_memberships(tenant_id,principal_id),
 CHECK(captured_at<=created_at)
);
CREATE TABLE community_reviews (
 id uuid PRIMARY KEY DEFAULT gen_random_uuid(), tenant_id uuid NOT NULL REFERENCES tenants,
 event_id uuid NOT NULL, workflow_run_id uuid NOT NULL, revision int NOT NULL CHECK(revision>0),
 influencer_id uuid NOT NULL, influencer_version_id uuid NOT NULL, character_config_version_id uuid NOT NULL,
 asset_version_id uuid NOT NULL, research_version_id uuid NOT NULL, brief_id uuid NOT NULL,
 qa_report_id uuid NOT NULL, content_approval_record_id uuid NOT NULL,
 fact_ids jsonb NOT NULL CHECK(jsonb_typeof(fact_ids)='array' AND jsonb_array_length(fact_ids)<=5),
 policy_hash text NOT NULL CHECK(policy_hash ~ '^[0-9a-f]{64}$'),
 status text NOT NULL DEFAULT 'CREATED' CHECK(status IN ('CREATED','CLASSIFYING','CLASSIFIED','DRAFTING',
 'DRAFT_COMPLETE','QA_RUNNING','HUMAN_REVIEW','BLOCKED','REVISION_REQUIRED','AWAITING_REVIEW',
 'REVIEWED_DRAFT','REJECTED','RETRY_WAIT','FAILED')),
 active_stage text CHECK(active_stage IN ('classify','draft','qa')),
 attempt_count int NOT NULL DEFAULT 0 CHECK(attempt_count BETWEEN 0 AND 3),
 retry_at timestamptz, error_category text CHECK(error_category ~ '^[A-Z][A-Z0-9_]{0,79}$'),
 classification jsonb, classification_hash text, draft jsonb, draft_hash text, qa jsonb, qa_hash text,
 created_by uuid NOT NULL, correlation_id uuid NOT NULL,
 idempotency_key text NOT NULL CHECK(length(idempotency_key) BETWEEN 1 AND 128),
 input_hash text NOT NULL CHECK(input_hash ~ '^[0-9a-f]{64}$'),
 created_at timestamptz NOT NULL DEFAULT clock_timestamp(), started_at timestamptz, ended_at timestamptz,
 UNIQUE(tenant_id,id), UNIQUE(tenant_id,event_id,revision), UNIQUE(tenant_id,idempotency_key),
 UNIQUE(tenant_id,id,asset_version_id,brief_id,research_version_id),
 UNIQUE(tenant_id,id,event_id,workflow_run_id,asset_version_id,research_version_id,qa_report_id,draft_hash,qa_hash),
 FOREIGN KEY(tenant_id,event_id,workflow_run_id) REFERENCES community_events(tenant_id,id,workflow_run_id),
 FOREIGN KEY(tenant_id,asset_version_id,workflow_run_id,research_version_id)
   REFERENCES content_asset_versions(tenant_id,id,workflow_run_id,research_version_id),
 FOREIGN KEY(tenant_id,asset_version_id,brief_id,research_version_id)
   REFERENCES content_asset_versions(tenant_id,id,brief_id,research_version_id),
 FOREIGN KEY(tenant_id,content_approval_record_id,workflow_run_id,asset_version_id,research_version_id,qa_report_id)
   REFERENCES approval_records(tenant_id,id,workflow_run_id,asset_version_id,research_version_id,qa_report_id),
 FOREIGN KEY(tenant_id,influencer_version_id,influencer_id) REFERENCES influencer_versions(tenant_id,id,influencer_id),
 FOREIGN KEY(tenant_id,character_config_version_id,influencer_id) REFERENCES character_config_versions(tenant_id,id,influencer_id),
 FOREIGN KEY(tenant_id,created_by) REFERENCES tenant_memberships(tenant_id,principal_id),
 CHECK((classification IS NULL)=(classification_hash IS NULL)),
 CHECK(classification IS NULL OR classification_hash=private.visual_hash(classification)),
 CHECK((draft IS NULL)=(draft_hash IS NULL)), CHECK(draft IS NULL OR draft_hash=private.visual_hash(draft)),
 CHECK((qa IS NULL)=(qa_hash IS NULL)), CHECK(qa IS NULL OR qa_hash=private.visual_hash(qa)),
 CHECK((status='RETRY_WAIT')=(retry_at IS NOT NULL)),
 CHECK((status IN ('HUMAN_REVIEW','BLOCKED','REVISION_REQUIRED','AWAITING_REVIEW','REVIEWED_DRAFT','REJECTED','FAILED'))=(ended_at IS NOT NULL))
);
CREATE TABLE community_reply_claims (
 id uuid PRIMARY KEY DEFAULT gen_random_uuid(), tenant_id uuid NOT NULL REFERENCES tenants,
 review_id uuid NOT NULL, asset_version_id uuid NOT NULL, brief_id uuid NOT NULL, research_version_id uuid NOT NULL,
 fact_id uuid NOT NULL, parent_field_path text NOT NULL, block_index int NOT NULL CHECK(block_index BETWEEN 1 AND 5),
 created_at timestamptz NOT NULL DEFAULT clock_timestamp(), UNIQUE(tenant_id,id), UNIQUE(tenant_id,review_id,fact_id),
 FOREIGN KEY(tenant_id,review_id,asset_version_id,brief_id,research_version_id)
   REFERENCES community_reviews(tenant_id,id,asset_version_id,brief_id,research_version_id),
 FOREIGN KEY(tenant_id,asset_version_id,parent_field_path,fact_id)
   REFERENCES content_claims(tenant_id,asset_version_id,field_path,fact_id),
 FOREIGN KEY(tenant_id,brief_id,research_version_id,fact_id)
   REFERENCES brief_facts(tenant_id,brief_id,research_version_id,fact_id)
);
CREATE TABLE community_decisions (
 id uuid PRIMARY KEY DEFAULT gen_random_uuid(), tenant_id uuid NOT NULL REFERENCES tenants,
 review_id uuid NOT NULL, event_id uuid NOT NULL, workflow_run_id uuid NOT NULL,
 asset_version_id uuid NOT NULL, research_version_id uuid NOT NULL, qa_report_id uuid NOT NULL,
 draft_hash text NOT NULL, qa_hash text NOT NULL, decision text NOT NULL CHECK(decision IN ('APPROVE','REJECT')),
 approver_id uuid NOT NULL, comment text CHECK(length(comment)<=2000), created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
 UNIQUE(tenant_id,id), UNIQUE(tenant_id,review_id),
 FOREIGN KEY(tenant_id,review_id,event_id,workflow_run_id,asset_version_id,research_version_id,qa_report_id,draft_hash,qa_hash)
   REFERENCES community_reviews(tenant_id,id,event_id,workflow_run_id,asset_version_id,research_version_id,qa_report_id,draft_hash,qa_hash),
 FOREIGN KEY(tenant_id,approver_id) REFERENCES tenant_memberships(tenant_id,principal_id)
);
CREATE INDEX community_event_history ON community_events(tenant_id,workflow_run_id,created_at);
CREATE INDEX community_review_history ON community_reviews(tenant_id,event_id,revision);
CREATE TRIGGER immutable BEFORE UPDATE OR DELETE ON community_events FOR EACH ROW EXECUTE FUNCTION immutable_record();
CREATE TRIGGER immutable BEFORE UPDATE OR DELETE ON community_reply_claims FOR EACH ROW EXECUTE FUNCTION immutable_record();
CREATE TRIGGER immutable BEFORE UPDATE OR DELETE ON community_decisions FOR EACH ROW EXECUTE FUNCTION immutable_record();
CREATE FUNCTION private.guard_community_history() RETURNS trigger LANGUAGE plpgsql SET search_path=pg_catalog,public AS $$
 BEGIN
 IF TG_OP='DELETE' OR OLD.status IN ('HUMAN_REVIEW','BLOCKED','REVISION_REQUIRED','REVIEWED_DRAFT','REJECTED','FAILED')
 OR (OLD.status='AWAITING_REVIEW' AND NEW.status NOT IN ('REVIEWED_DRAFT','REJECTED'))
 OR (OLD.classification IS NOT NULL AND NEW.classification IS DISTINCT FROM OLD.classification)
 OR (OLD.draft IS NOT NULL AND NEW.draft IS DISTINCT FROM OLD.draft)
 OR (OLD.qa IS NOT NULL AND NEW.qa IS DISTINCT FROM OLD.qa) THEN
 RAISE EXCEPTION 'Community history is immutable; create a new review' USING ERRCODE='23514'; END IF;
 RETURN NEW; END $$;
CREATE TRIGGER immutable_community_history BEFORE UPDATE OR DELETE ON community_reviews
 FOR EACH ROW EXECUTE FUNCTION private.guard_community_history();
DO $$ DECLARE t text; BEGIN
 FOREACH t IN ARRAY ARRAY['community_events','community_reviews','community_reply_claims','community_decisions'] LOOP
 EXECUTE format('ALTER TABLE %I ENABLE ROW LEVEL SECURITY',t);
 EXECUTE format('ALTER TABLE %I FORCE ROW LEVEL SECURITY',t);
 EXECUTE format('CREATE POLICY tenant_boundary ON %I USING (tenant_id=public.context_tenant()) WITH CHECK (tenant_id=public.context_tenant())',t);
 EXECUTE format('REVOKE ALL ON %I FROM PUBLIC,mediaos_runtime',t);
 EXECUTE format('GRANT SELECT ON %I TO mediaos_runtime',t);
 END LOOP; END $$;

CREATE FUNCTION private.audit_community(r public.community_reviews,event text,after_status text,extra jsonb DEFAULT '{}'::jsonb)
 RETURNS void LANGUAGE sql SECURITY DEFINER SET search_path=pg_catalog,public AS $$
 INSERT INTO public.audit_events(tenant_id,workflow_run_id,actor_id,event_type,correlation_id,details)
 VALUES(r.tenant_id,r.workflow_run_id,public.context_principal(),event,r.correlation_id,
 jsonb_build_object('community_review_id',r.id,'community_event_id',r.event_id,'revision',r.revision,
 'from_community_status',r.status,'to_community_status',after_status,'network_performed',false)||extra) $$;

CREATE FUNCTION public.register_community_event(runid uuid,p jsonb,key text,inputhash text) RETURNS uuid
 LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,public AS $$
 DECLARE w public.workflow_runs; e public.community_events; captured timestamptz;
 keys text[]:=ARRAY['schema_version','mode','origin','participant_reference','comment_text','captured_at'];
 BEGIN
 IF NOT public.has_role('OPERATOR') THEN RAISE EXCEPTION 'Operator required' USING ERRCODE='42501'; END IF;
 IF p IS NULL OR jsonb_typeof(p) IS DISTINCT FROM 'object' OR NOT(p ?& keys) OR p-keys<>'{}'::jsonb
 OR p->'schema_version' IS DISTINCT FROM '1'::jsonb OR p->>'mode' NOT IN ('MANUAL','FIXTURE')
 OR jsonb_typeof(p->'mode') IS DISTINCT FROM 'string'
 OR jsonb_typeof(p->'origin') IS DISTINCT FROM 'string' OR length(p->>'origin') NOT BETWEEN 1 AND 512 OR length(btrim(p->>'origin'))=0
 OR jsonb_typeof(p->'participant_reference') IS DISTINCT FROM 'string' OR length(p->>'participant_reference') NOT BETWEEN 1 AND 128 OR length(btrim(p->>'participant_reference'))=0
 OR jsonb_typeof(p->'comment_text') IS DISTINCT FROM 'string' OR length(p->>'comment_text') NOT BETWEEN 1 AND 4000 OR length(btrim(p->>'comment_text'))=0
 OR jsonb_typeof(p->'captured_at') IS DISTINCT FROM 'string'
 OR p->>'captured_at' !~ '^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(\.\d{1,6})?(Z|\+00:00)$'
 OR key IS NULL OR length(key) NOT BETWEEN 1 AND 128
 OR inputhash IS DISTINCT FROM private.visual_hash(jsonb_build_object('workflow_run_id',runid,'payload',p)) THEN
 RAISE EXCEPTION 'Invalid bounded community event payload' USING ERRCODE='23514'; END IF;
 BEGIN captured:=(p->>'captured_at')::timestamptz;
 EXCEPTION WHEN invalid_datetime_format OR datetime_field_overflow THEN
 RAISE EXCEPTION 'Invalid capture timestamp' USING ERRCODE='23514'; END;
 IF captured>clock_timestamp() THEN RAISE EXCEPTION 'Future capture timestamp denied' USING ERRCODE='23514'; END IF;
 SELECT * INTO w FROM public.workflow_runs WHERE tenant_id=public.context_tenant() AND id=runid;
 IF w.id IS NULL THEN RAISE EXCEPTION 'Workflow not found' USING ERRCODE='P0002'; END IF;
 INSERT INTO public.community_events(tenant_id,workflow_run_id,mode,origin,participant_reference,comment_text,
 captured_at,schema_version,payload,content_hash,created_by,correlation_id,idempotency_key,input_hash)
 VALUES(w.tenant_id,w.id,p->>'mode',p->>'origin',p->>'participant_reference',p->>'comment_text',captured,1,p,
 private.visual_hash(p),public.context_principal(),w.correlation_id,key,inputhash)
 ON CONFLICT(tenant_id,idempotency_key) DO NOTHING RETURNING * INTO e;
 IF e.id IS NULL THEN
 SELECT * INTO e FROM public.community_events WHERE tenant_id=w.tenant_id AND idempotency_key=key;
 IF e.input_hash IS DISTINCT FROM inputhash THEN RAISE EXCEPTION 'Community event idempotency conflict' USING ERRCODE='23505'; END IF;
 ELSE
 INSERT INTO public.audit_events(tenant_id,workflow_run_id,actor_id,event_type,correlation_id,details)
 VALUES(e.tenant_id,e.workflow_run_id,public.context_principal(),'COMMUNITY_EVENT_REGISTERED',e.correlation_id,
 jsonb_build_object('community_event_id',e.id,'mode',e.mode,'content_hash',e.content_hash));
 END IF;
 RETURN e.id; END $$;

-- Caller obtains workflow, then event/review locks. The inherited QA guard finally
-- locks any live opportunity, serializing source changes with reply decisions.
CREATE FUNCTION private.community_parent_findings(r public.community_reviews) RETURNS jsonb
 LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,public AS $$
 DECLARE w public.workflow_runs; result jsonb:='[]'::jsonb;
 BEGIN
 SELECT * INTO w FROM public.workflow_runs WHERE tenant_id=r.tenant_id AND id=r.workflow_run_id;
 IF w.state IS DISTINCT FROM 'APPROVED' OR w.asset_version_id IS DISTINCT FROM r.asset_version_id
 OR w.research_version_id IS DISTINCT FROM r.research_version_id OR w.qa_report_id IS DISTINCT FROM r.qa_report_id
 OR w.influencer_version_id IS DISTINCT FROM r.influencer_version_id
 OR NOT EXISTS(SELECT 1 FROM public.approval_records a WHERE a.tenant_id=r.tenant_id AND a.id=r.content_approval_record_id
 AND a.workflow_run_id=w.id AND a.asset_version_id=w.asset_version_id AND a.research_version_id=w.research_version_id
 AND a.qa_report_id=w.qa_report_id AND a.decision='APPROVE')
 OR EXISTS(SELECT 1 FROM public.community_reviews newer WHERE newer.tenant_id=r.tenant_id AND newer.event_id=r.event_id AND newer.revision>r.revision) THEN
 result:=result||jsonb_build_array(jsonb_build_object('code','COMMUNITY_STALE_PARENT','category','WORKFLOW','severity','BLOCKED',
 'message','Current approved parent and latest review revision required')); END IF;
 RETURN result||public.qa_findings(r.workflow_run_id); END $$;

CREATE FUNCTION public.community_parent_findings(reviewid uuid) RETURNS jsonb
 LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,public AS $$
 DECLARE r public.community_reviews; wid uuid;
 BEGIN
 SELECT workflow_run_id INTO wid FROM public.community_reviews WHERE tenant_id=public.context_tenant() AND id=reviewid;
 IF wid IS NULL THEN RAISE EXCEPTION 'Review not found' USING ERRCODE='P0002'; END IF;
 PERFORM 1 FROM public.workflow_runs WHERE tenant_id=public.context_tenant() AND id=wid FOR UPDATE;
 SELECT * INTO r FROM public.community_reviews WHERE tenant_id=public.context_tenant() AND id=reviewid;
 RETURN private.community_parent_findings(r); END $$;

CREATE FUNCTION public.start_community_review(eventid uuid,selected_facts jsonb,key text,inputhash text) RETURNS uuid
 LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,public AS $$
 DECLARE e public.community_events; w public.workflow_runs; r public.community_reviews; cfg public.character_config_versions;
 approval public.approval_records; fid uuid;
 BEGIN
 IF NOT public.has_role('OPERATOR') THEN RAISE EXCEPTION 'Operator required' USING ERRCODE='42501'; END IF;
 IF key IS NULL OR length(key) NOT BETWEEN 1 AND 128 OR selected_facts IS NULL OR jsonb_typeof(selected_facts) IS DISTINCT FROM 'array'
 OR jsonb_array_length(selected_facts)>5 OR EXISTS(SELECT 1 FROM jsonb_array_elements(selected_facts) x WHERE jsonb_typeof(x)<>'string')
 OR (SELECT count(*) FROM jsonb_array_elements(selected_facts))<>(SELECT count(DISTINCT value) FROM jsonb_array_elements(selected_facts))
 OR inputhash IS DISTINCT FROM private.visual_hash(jsonb_build_object('event_id',eventid,'fact_ids',selected_facts)) THEN
 RAISE EXCEPTION 'Invalid community review request' USING ERRCODE='23514'; END IF;
 SELECT * INTO e FROM public.community_events WHERE tenant_id=public.context_tenant() AND id=eventid;
 IF e.id IS NULL THEN RAISE EXCEPTION 'Event not found' USING ERRCODE='P0002'; END IF;
 SELECT * INTO w FROM public.workflow_runs WHERE tenant_id=e.tenant_id AND id=e.workflow_run_id FOR UPDATE;
 PERFORM 1 FROM public.community_events WHERE tenant_id=e.tenant_id AND id=e.id FOR UPDATE;
 PERFORM pg_advisory_xact_lock(hashtextextended('community-review:'||e.tenant_id::text||':'||key,0));
 SELECT * INTO r FROM public.community_reviews WHERE tenant_id=e.tenant_id AND idempotency_key=key;
 IF r.id IS NOT NULL THEN
 IF r.input_hash IS DISTINCT FROM inputhash THEN RAISE EXCEPTION 'Community review idempotency conflict' USING ERRCODE='23505'; END IF;
 RETURN r.id; END IF;
 SELECT * INTO approval FROM public.approval_records WHERE tenant_id=w.tenant_id AND workflow_run_id=w.id
 AND asset_version_id=w.asset_version_id AND research_version_id=w.research_version_id AND qa_report_id=w.qa_report_id AND decision='APPROVE';
 IF w.state<>'APPROVED' OR approval.id IS NULL OR jsonb_array_length(public.qa_findings(w.id))>0 THEN
 RAISE EXCEPTION 'Current approved passing parent required' USING ERRCODE='23514'; END IF;
 FOR fid IN SELECT value::uuid FROM jsonb_array_elements_text(selected_facts) LOOP
 IF NOT EXISTS(SELECT 1 FROM public.content_claims cc JOIN public.brief_facts bf
 ON bf.tenant_id=cc.tenant_id AND bf.brief_id=cc.brief_id AND bf.research_version_id=cc.research_version_id AND bf.fact_id=cc.fact_id
 WHERE cc.tenant_id=w.tenant_id AND cc.asset_version_id=w.asset_version_id AND cc.fact_id=fid
 AND bf.brief_id=w.brief_id AND bf.research_version_id=w.research_version_id) THEN
 RAISE EXCEPTION 'Selected fact must belong to exact approved parent' USING ERRCODE='23514'; END IF;
 END LOOP;
 SELECT c.* INTO cfg FROM public.character_config_versions c JOIN public.influencer_versions iv
 ON iv.tenant_id=c.tenant_id AND iv.character_config_version_id=c.id WHERE iv.tenant_id=w.tenant_id AND iv.id=w.influencer_version_id;
 INSERT INTO public.community_reviews(tenant_id,event_id,workflow_run_id,revision,influencer_id,influencer_version_id,
 character_config_version_id,asset_version_id,research_version_id,brief_id,qa_report_id,content_approval_record_id,
 fact_ids,policy_hash,created_by,correlation_id,idempotency_key,input_hash)
 VALUES(w.tenant_id,e.id,w.id,(SELECT coalesce(max(revision),0)+1 FROM public.community_reviews WHERE tenant_id=w.tenant_id AND event_id=e.id),
 w.influencer_id,w.influencer_version_id,cfg.id,w.asset_version_id,w.research_version_id,w.brief_id,w.qa_report_id,approval.id,
 selected_facts,private.visual_hash(coalesce(cfg.payload->'community_policy','null'::jsonb)),public.context_principal(),w.correlation_id,key,inputhash)
 RETURNING * INTO r;
 PERFORM private.audit_community(r,'COMMUNITY_REVIEW_CREATED','CREATED');
 RETURN r.id; END $$;

CREATE FUNCTION private.community_classification(r public.community_reviews) RETURNS jsonb
 LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,public AS $$
 DECLARE p jsonb; phrase text; category text:='HUMAN_REVIEW'; reason text:='UNRECOGNIZED_INPUT';
 BEGIN
 SELECT payload->'community_policy' INTO p FROM public.character_config_versions WHERE tenant_id=r.tenant_id AND id=r.character_config_version_id;
 SELECT btrim(comment_text,' ') INTO phrase FROM public.community_events WHERE tenant_id=r.tenant_id AND id=r.event_id;
 IF p IS NULL OR p='null'::jsonb THEN reason:='POLICY_MISSING';
 ELSIF p->'acknowledgement_phrases' ? phrase THEN category:='ACKNOWLEDGEMENT'; reason:='EXACT_ACKNOWLEDGEMENT';
 ELSIF p->'source_request_phrases' ? phrase THEN category:='SOURCE_REQUEST'; reason:='EXACT_SOURCE_REQUEST'; END IF;
 RETURN jsonb_build_object('schema_version',1,'category',category,'reason_code',reason); END $$;

CREATE FUNCTION private.community_draft(r public.community_reviews) RETURNS jsonb
 LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,public AS $$
 DECLARE cfg jsonb; p jsonb; blocks jsonb; fid uuid; fact public.facts;
 BEGIN
 SELECT payload INTO cfg FROM public.character_config_versions WHERE tenant_id=r.tenant_id AND id=r.character_config_version_id;
 p:=cfg->'community_policy';
 IF p IS NULL OR p='null'::jsonb OR r.classification->>'category' NOT IN ('ACKNOWLEDGEMENT','SOURCE_REQUEST') THEN
 RAISE EXCEPTION 'Recognized configured classification required' USING ERRCODE='23514'; END IF;
 blocks:=jsonb_build_array(jsonb_build_object('kind','CREATIVE','text',CASE WHEN r.classification->>'category'='ACKNOWLEDGEMENT'
 THEN p->>'acknowledgement_text' ELSE p->>'source_intro' END,'fact_ids','[]'::jsonb));
 IF r.classification->>'category'='SOURCE_REQUEST' THEN
 FOR fid IN SELECT value::uuid FROM jsonb_array_elements_text(r.fact_ids) LOOP
 SELECT * INTO fact FROM public.facts WHERE tenant_id=r.tenant_id AND id=fid;
 IF fact.id IS NULL OR NOT EXISTS(SELECT 1 FROM public.content_claims WHERE tenant_id=r.tenant_id AND asset_version_id=r.asset_version_id AND fact_id=fid) THEN
 RAISE EXCEPTION 'Exact approved fact required' USING ERRCODE='23514'; END IF;
 blocks:=blocks||jsonb_build_array(jsonb_build_object('kind','FACT','text',fact.statement,'fact_ids',jsonb_build_array(fid)));
 END LOOP; END IF;
 RETURN jsonb_build_object('schema_version',1,'influencer_version_id',r.influencer_version_id,
 'character_config_version_id',r.character_config_version_id,'policy_hash',r.policy_hash,'language',cfg->>'language',
 'blocks',blocks,'disclosure',cfg->>'disclosure'); END $$;

CREATE FUNCTION private.community_qa(r public.community_reviews) RETURNS jsonb
 LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,public AS $$
 DECLARE findings jsonb; cfg jsonb; mode text; result text; replylength int;
 BEGIN
 findings:=private.community_parent_findings(r);
 SELECT payload INTO cfg FROM public.character_config_versions WHERE tenant_id=r.tenant_id AND id=r.character_config_version_id;
 SELECT e.mode INTO mode FROM public.community_events e WHERE e.tenant_id=r.tenant_id AND e.id=r.event_id;
 IF mode='FIXTURE' THEN findings:=findings||jsonb_build_array(jsonb_build_object('code','COMMUNITY_FIXTURE','category','WORKFLOW','severity','BLOCKED','message','Fixture events cannot be approved')); END IF;
 IF r.draft IS DISTINCT FROM private.community_draft(r) THEN findings:=findings||jsonb_build_array(jsonb_build_object('code','COMMUNITY_UNSUPPORTED','category','EVIDENCE','severity','BLOCKED','message','Reply differs from configured templates and exact approved facts')); END IF;
 IF coalesce(r.draft->>'disclosure','')='' OR r.draft->>'disclosure' IS DISTINCT FROM cfg->>'disclosure' THEN findings:=findings||jsonb_build_array(jsonb_build_object('code','COMMUNITY_DISCLOSURE','category','CONTENT','severity','BLOCKED','message','Mandatory AI disclosure mismatch')); END IF;
 IF r.classification->>'category'='SOURCE_REQUEST' AND jsonb_array_length(r.fact_ids)=0 THEN findings:=findings||jsonb_build_array(jsonb_build_object('code','COMMUNITY_EVIDENCE','category','EVIDENCE','severity','BLOCKED','message','Source requests require selected approved facts')); END IF;
 IF r.classification->>'category'='ACKNOWLEDGEMENT' AND jsonb_array_length(r.fact_ids)>0 THEN findings:=findings||jsonb_build_array(jsonb_build_object('code','COMMUNITY_UNUSED_FACTS','category','CONTENT','severity','BLOCKED','message','Acknowledgements cannot attach unused facts')); END IF;
 SELECT coalesce(sum(length(value->>'text')),0)+jsonb_array_length(r.draft->'blocks')+length(r.draft->>'disclosure')
 INTO replylength FROM jsonb_array_elements(r.draft->'blocks');
 IF replylength>(cfg->'community_policy'->>'max_reply_chars')::int THEN findings:=findings||jsonb_build_array(jsonb_build_object('code','COMMUNITY_LENGTH','category','CONTENT','severity','REVISION_REQUIRED','message','Reply exceeds configured character limit')); END IF;
 result:=CASE WHEN EXISTS(SELECT 1 FROM jsonb_array_elements(findings) x WHERE x->>'severity'='BLOCKED') THEN 'BLOCKED'
 WHEN jsonb_array_length(findings)>0 THEN 'REVISION_REQUIRED' ELSE 'PASS' END;
 RETURN jsonb_build_object('schema_version',1,'status',result,'findings',findings); END $$;

CREATE FUNCTION public.claim_community_stage(reviewid uuid,stage text) RETURNS void
 LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,public AS $$
 DECLARE r public.community_reviews; wid uuid; nextstatus text;
 BEGIN
 IF NOT public.has_role('OPERATOR') THEN RAISE EXCEPTION 'Operator required' USING ERRCODE='42501'; END IF;
 SELECT workflow_run_id INTO wid FROM public.community_reviews WHERE tenant_id=public.context_tenant() AND id=reviewid;
 IF wid IS NULL THEN RAISE EXCEPTION 'Review not found' USING ERRCODE='P0002'; END IF;
 PERFORM 1 FROM public.workflow_runs WHERE tenant_id=public.context_tenant() AND id=wid FOR UPDATE;
 SELECT * INTO r FROM public.community_reviews WHERE tenant_id=public.context_tenant() AND id=reviewid FOR UPDATE;
 IF stage IS NULL OR stage NOT IN ('classify','draft','qa') OR r.attempt_count>=3 OR r.retry_at>clock_timestamp()
 OR NOT ((stage='classify' AND r.status IN ('CREATED','CLASSIFYING')) OR (stage='draft' AND r.status IN ('CLASSIFIED','DRAFTING'))
 OR (stage='qa' AND r.status IN ('DRAFT_COMPLETE','QA_RUNNING')) OR (r.status='RETRY_WAIT' AND r.active_stage=stage)) THEN
 RAISE EXCEPTION 'Community stage, state or retry policy invalid' USING ERRCODE='23514'; END IF;
 IF jsonb_array_length(private.community_parent_findings(r))>0 THEN
 RAISE EXCEPTION 'Community parent no longer eligible' USING ERRCODE='23514'; END IF;
 nextstatus:=CASE stage WHEN 'classify' THEN 'CLASSIFYING' WHEN 'draft' THEN 'DRAFTING' ELSE 'QA_RUNNING' END;
 UPDATE public.community_reviews SET status=nextstatus,active_stage=stage,attempt_count=attempt_count+1,
 retry_at=NULL,error_category=NULL,started_at=coalesce(started_at,clock_timestamp()) WHERE id=r.id;
 PERFORM private.audit_community(r,'COMMUNITY_STAGE_STARTED',nextstatus,jsonb_build_object('stage',stage,'attempt',r.attempt_count+1));
 END $$;

CREATE FUNCTION public.complete_community_stage(reviewid uuid,stage text,result jsonb,resulthash text) RETURNS void
 LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,public AS $$
 DECLARE r public.community_reviews; wid uuid; expected jsonb; nextstatus text; fid uuid; idx int:=0; path text;
 BEGIN
 IF NOT public.has_role('OPERATOR') THEN RAISE EXCEPTION 'Operator required' USING ERRCODE='42501'; END IF;
 SELECT workflow_run_id INTO wid FROM public.community_reviews WHERE tenant_id=public.context_tenant() AND id=reviewid;
 IF wid IS NULL THEN RAISE EXCEPTION 'Review not found' USING ERRCODE='P0002'; END IF;
 PERFORM 1 FROM public.workflow_runs WHERE tenant_id=public.context_tenant() AND id=wid FOR UPDATE;
 SELECT * INTO r FROM public.community_reviews WHERE tenant_id=public.context_tenant() AND id=reviewid FOR UPDATE;
 IF stage IS NULL OR stage NOT IN ('classify','draft','qa') OR r.active_stage IS DISTINCT FROM stage
 OR r.status IS DISTINCT FROM (CASE stage WHEN 'classify' THEN 'CLASSIFYING' WHEN 'draft' THEN 'DRAFTING' ELSE 'QA_RUNNING' END)
 OR NOT EXISTS(SELECT 1 FROM public.skill_runs s WHERE s.tenant_id=r.tenant_id AND s.workflow_run_id=r.workflow_run_id
 AND s.step_key='community.'||stage||':'||r.id::text AND s.attempt=r.attempt_count AND s.status='RUNNING' AND s.input_hash=r.input_hash) THEN
 RAISE EXCEPTION 'Active persisted community stage attempt required' USING ERRCODE='23514'; END IF;
 IF stage<>'qa' AND jsonb_array_length(private.community_parent_findings(r))>0 THEN
 RAISE EXCEPTION 'Community parent no longer eligible' USING ERRCODE='23514'; END IF;
 expected:=CASE stage WHEN 'classify' THEN private.community_classification(r) WHEN 'draft' THEN private.community_draft(r) ELSE private.community_qa(r) END;
 IF result IS DISTINCT FROM expected OR resulthash IS DISTINCT FROM private.visual_hash(expected) THEN
 RAISE EXCEPTION 'Community output differs from deterministic guarded result' USING ERRCODE='23514'; END IF;
 IF stage='classify' THEN
 nextstatus:=CASE WHEN expected->>'category'='HUMAN_REVIEW' THEN 'HUMAN_REVIEW' ELSE 'CLASSIFIED' END;
 UPDATE public.community_reviews SET classification=expected,classification_hash=resulthash WHERE id=r.id;
 ELSIF stage='draft' THEN
 nextstatus:='DRAFT_COMPLETE';
 UPDATE public.community_reviews SET draft=expected,draft_hash=resulthash WHERE id=r.id;
 IF r.classification->>'category'='SOURCE_REQUEST' THEN
 FOR fid IN SELECT value::uuid FROM jsonb_array_elements_text(r.fact_ids) LOOP
 idx:=idx+1;
 SELECT field_path INTO path FROM public.content_claims WHERE tenant_id=r.tenant_id AND asset_version_id=r.asset_version_id AND fact_id=fid ORDER BY field_path LIMIT 1;
 INSERT INTO public.community_reply_claims(tenant_id,review_id,asset_version_id,brief_id,research_version_id,fact_id,parent_field_path,block_index)
 VALUES(r.tenant_id,r.id,r.asset_version_id,r.brief_id,r.research_version_id,fid,path,idx);
 END LOOP; END IF;
 ELSE
 nextstatus:=CASE WHEN expected->>'status'='PASS' THEN 'AWAITING_REVIEW' ELSE expected->>'status' END;
 UPDATE public.community_reviews SET qa=expected,qa_hash=resulthash WHERE id=r.id;
 END IF;
 UPDATE public.community_reviews SET status=nextstatus,active_stage=NULL,attempt_count=0,retry_at=NULL,error_category=NULL,
 ended_at=CASE WHEN nextstatus IN ('HUMAN_REVIEW','AWAITING_REVIEW','BLOCKED','REVISION_REQUIRED') THEN clock_timestamp() ELSE NULL END WHERE id=r.id;
 PERFORM private.audit_community(r,'COMMUNITY_STAGE_COMPLETED',nextstatus,jsonb_build_object('stage',stage,'output_hash',resulthash));
 END $$;

CREATE FUNCTION public.fail_community_stage(reviewid uuid,category text,retryable boolean) RETURNS void
 LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,public AS $$
 DECLARE r public.community_reviews; wid uuid; outcome text; next_retry timestamptz;
 BEGIN
 IF NOT public.has_role('OPERATOR') THEN RAISE EXCEPTION 'Operator required' USING ERRCODE='42501'; END IF;
 IF category IS NULL OR category !~ '^[A-Z][A-Z0-9_]{0,79}$' OR retryable IS NULL OR (category='POLICY_BLOCKED' AND retryable) THEN
 RAISE EXCEPTION 'Valid failure category and retry policy required' USING ERRCODE='23514'; END IF;
 SELECT workflow_run_id INTO wid FROM public.community_reviews WHERE tenant_id=public.context_tenant() AND id=reviewid;
 IF wid IS NULL THEN RAISE EXCEPTION 'Review not found' USING ERRCODE='P0002'; END IF;
 PERFORM 1 FROM public.workflow_runs WHERE tenant_id=public.context_tenant() AND id=wid FOR UPDATE;
 SELECT * INTO r FROM public.community_reviews WHERE tenant_id=public.context_tenant() AND id=reviewid FOR UPDATE;
 IF r.status NOT IN ('CREATED','CLASSIFYING','CLASSIFIED','DRAFTING','DRAFT_COMPLETE','QA_RUNNING','RETRY_WAIT') THEN
 RAISE EXCEPTION 'Community review is terminal' USING ERRCODE='23514'; END IF;
 outcome:=CASE WHEN category='POLICY_BLOCKED' THEN 'BLOCKED' WHEN retryable AND r.attempt_count<3 AND r.active_stage IS NOT NULL THEN 'RETRY_WAIT' ELSE 'FAILED' END;
 IF outcome='RETRY_WAIT' THEN next_retry:=greatest(r.retry_at,clock_timestamp()+make_interval(secs=>power(2,greatest(r.attempt_count-1,0))::int)); END IF;
 UPDATE public.community_reviews SET status=outcome,retry_at=next_retry,error_category=category,
 ended_at=CASE WHEN outcome='RETRY_WAIT' THEN NULL ELSE clock_timestamp() END WHERE id=r.id;
 PERFORM private.audit_community(r,CASE WHEN outcome='RETRY_WAIT' THEN 'COMMUNITY_RETRY_SCHEDULED' ELSE 'COMMUNITY_STAGE_FAILED' END,
 outcome,jsonb_build_object('stage',r.active_stage,'attempt',r.attempt_count,'error_category',category,'retryable',outcome='RETRY_WAIT','retry_at',next_retry));
 END $$;

CREATE FUNCTION public.decide_community_review(reviewid uuid,drafthash text,qahash text,decision text,comment text DEFAULT NULL) RETURNS uuid
 LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,public AS $$
 DECLARE r public.community_reviews; wid uuid; result uuid; expected jsonb;
 BEGIN
 IF NOT public.has_role('APPROVER') THEN RAISE EXCEPTION 'Approver required' USING ERRCODE='42501'; END IF;
 IF decision IS NULL OR decision NOT IN ('APPROVE','REJECT') OR length(comment)>2000 THEN RAISE EXCEPTION 'Invalid community decision' USING ERRCODE='23514'; END IF;
 SELECT workflow_run_id INTO wid FROM public.community_reviews WHERE tenant_id=public.context_tenant() AND id=reviewid;
 IF wid IS NULL THEN RAISE EXCEPTION 'Review not found' USING ERRCODE='P0002'; END IF;
 PERFORM 1 FROM public.workflow_runs WHERE tenant_id=public.context_tenant() AND id=wid FOR UPDATE;
 SELECT * INTO r FROM public.community_reviews WHERE tenant_id=public.context_tenant() AND id=reviewid FOR UPDATE;
 IF r.status<>'AWAITING_REVIEW' OR r.draft_hash IS DISTINCT FROM drafthash OR r.qa_hash IS DISTINCT FROM qahash
 OR r.qa->>'status' IS DISTINCT FROM 'PASS' THEN
 RAISE EXCEPTION 'Exact current passing community draft required' USING ERRCODE='23514'; END IF;
 expected:=private.community_qa(r);
 IF expected IS DISTINCT FROM r.qa OR expected->>'status'<>'PASS' THEN
 RAISE EXCEPTION 'Community approval denied by current evidence and revision policy' USING ERRCODE='23514'; END IF;
 INSERT INTO public.community_decisions(tenant_id,review_id,event_id,workflow_run_id,asset_version_id,research_version_id,qa_report_id,draft_hash,qa_hash,decision,approver_id,comment)
 VALUES(r.tenant_id,r.id,r.event_id,r.workflow_run_id,r.asset_version_id,r.research_version_id,r.qa_report_id,r.draft_hash,r.qa_hash,
 decision,public.context_principal(),comment) RETURNING id INTO result;
 UPDATE public.community_reviews SET status=CASE WHEN decision='APPROVE' THEN 'REVIEWED_DRAFT' ELSE 'REJECTED' END WHERE id=r.id;
 PERFORM private.audit_community(r,'COMMUNITY_DECISION_RECORDED',CASE WHEN decision='APPROVE' THEN 'REVIEWED_DRAFT' ELSE 'REJECTED' END,
 jsonb_build_object('decision_id',result,'decision',decision,'draft_hash',drafthash,'qa_hash',qahash,'sent',false));
 RETURN result; END $$;

REVOKE ALL ON FUNCTION private.guard_community_history(),private.audit_community(public.community_reviews,text,text,jsonb),
 private.community_parent_findings(public.community_reviews),private.community_classification(public.community_reviews),
 private.community_draft(public.community_reviews),private.community_qa(public.community_reviews) FROM PUBLIC,mediaos_runtime;
REVOKE ALL ON FUNCTION public.register_community_event(uuid,jsonb,text,text),public.start_community_review(uuid,jsonb,text,text),
 public.community_parent_findings(uuid),public.claim_community_stage(uuid,text),public.complete_community_stage(uuid,text,jsonb,text),
 public.fail_community_stage(uuid,text,boolean),public.decide_community_review(uuid,text,text,text,text) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION public.register_community_event(uuid,jsonb,text,text),public.start_community_review(uuid,jsonb,text,text),
 public.community_parent_findings(uuid),public.claim_community_stage(uuid,text),public.complete_community_stage(uuid,text,jsonb,text),
 public.fail_community_stage(uuid,text,boolean),public.decide_community_review(uuid,text,text,text,text) TO mediaos_runtime;
