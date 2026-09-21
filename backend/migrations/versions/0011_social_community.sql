-- PLATFORM means an observed signed platform event, never an operator assertion.
-- Existing register_community_event remains strictly MANUAL/FIXTURE.
ALTER TABLE community_events DROP CONSTRAINT community_events_mode_check;
ALTER TABLE community_events ADD CHECK(mode IN ('MANUAL','FIXTURE','PLATFORM'));
ALTER TABLE social_publish_runs ADD UNIQUE(tenant_id,id,workflow_run_id,connection_id);
ALTER TABLE community_decisions ADD UNIQUE(tenant_id,id,review_id,draft_hash,qa_hash);

CREATE TABLE social_comment_links (
 id uuid PRIMARY KEY DEFAULT gen_random_uuid(),tenant_id uuid NOT NULL REFERENCES tenants,
 event_id uuid NOT NULL,workflow_run_id uuid NOT NULL,webhook_event_id uuid NOT NULL,
 publish_run_id uuid NOT NULL,connection_id uuid NOT NULL,
 comment_id text NOT NULL CHECK(comment_id ~ '^[0-9]{1,64}$'),
 sequence bigint GENERATED ALWAYS AS IDENTITY,created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
 UNIQUE(tenant_id,id),UNIQUE(tenant_id,event_id),UNIQUE(tenant_id,webhook_event_id),
 UNIQUE(tenant_id,id,event_id,workflow_run_id,connection_id,publish_run_id,comment_id),
 FOREIGN KEY(tenant_id,event_id,workflow_run_id) REFERENCES community_events(tenant_id,id,workflow_run_id),
 FOREIGN KEY(tenant_id,webhook_event_id) REFERENCES social_webhook_events(tenant_id,id),
 FOREIGN KEY(tenant_id,publish_run_id,workflow_run_id,connection_id)
 REFERENCES social_publish_runs(tenant_id,id,workflow_run_id,connection_id)
);
CREATE TABLE social_reply_runs (
 id uuid PRIMARY KEY DEFAULT gen_random_uuid(),tenant_id uuid NOT NULL REFERENCES tenants,
 review_id uuid NOT NULL,event_id uuid NOT NULL,workflow_run_id uuid NOT NULL,
 comment_link_id uuid NOT NULL,connection_id uuid NOT NULL,publish_run_id uuid NOT NULL,comment_id text NOT NULL,
 asset_version_id uuid NOT NULL,research_version_id uuid NOT NULL,qa_report_id uuid NOT NULL,
 community_decision_id uuid NOT NULL,draft_hash text NOT NULL,qa_hash text NOT NULL,
 exact_text text NOT NULL CHECK(length(exact_text) BETWEEN 1 AND 2200),
 text_hash text NOT NULL CHECK(text_hash=private.visual_hash(to_jsonb(exact_text))),
 idempotency_key text NOT NULL CHECK(length(idempotency_key) BETWEEN 1 AND 128),
 input_hash text NOT NULL CHECK(input_hash ~ '^[0-9a-f]{64}$'),
 status text NOT NULL DEFAULT 'AWAITING_REPLY_APPROVAL' CHECK(status IN
 ('AWAITING_REPLY_APPROVAL','AUTHORIZED','SENDING','SENT','REJECTED','BLOCKED','FAILED','UNKNOWN_OUTCOME')),
 reply_id text CHECK(reply_id ~ '^[0-9]{1,64}$'),sent_at timestamptz,
 sequence bigint GENERATED ALWAYS AS IDENTITY,created_by uuid NOT NULL,correlation_id uuid NOT NULL,
 created_at timestamptz NOT NULL DEFAULT clock_timestamp(),updated_at timestamptz NOT NULL DEFAULT clock_timestamp(),
 UNIQUE(tenant_id,id),UNIQUE(tenant_id,id,workflow_run_id),UNIQUE(tenant_id,id,text_hash),
 UNIQUE(tenant_id,idempotency_key),
 FOREIGN KEY(tenant_id,review_id,event_id,workflow_run_id,asset_version_id,research_version_id,qa_report_id,draft_hash,qa_hash)
 REFERENCES community_reviews(tenant_id,id,event_id,workflow_run_id,asset_version_id,research_version_id,qa_report_id,draft_hash,qa_hash),
 FOREIGN KEY(tenant_id,community_decision_id,review_id,draft_hash,qa_hash)
 REFERENCES community_decisions(tenant_id,id,review_id,draft_hash,qa_hash),
 FOREIGN KEY(tenant_id,comment_link_id,event_id,workflow_run_id,connection_id,publish_run_id,comment_id)
 REFERENCES social_comment_links(tenant_id,id,event_id,workflow_run_id,connection_id,publish_run_id,comment_id),
 FOREIGN KEY(tenant_id,created_by) REFERENCES tenant_memberships(tenant_id,principal_id),
 CHECK((status='SENT')=(reply_id IS NOT NULL AND sent_at IS NOT NULL))
);
CREATE UNIQUE INDEX social_comment_single_dispatch ON social_reply_runs(tenant_id,connection_id,comment_id)
 WHERE status IN ('SENDING','SENT','UNKNOWN_OUTCOME');
CREATE TABLE social_reply_decisions (
 id uuid PRIMARY KEY DEFAULT gen_random_uuid(),tenant_id uuid NOT NULL REFERENCES tenants,
 reply_run_id uuid NOT NULL,text_hash text NOT NULL,
 decision text NOT NULL CHECK(decision IN ('AUTHORIZE_REPLY','REJECT')),
 created_by uuid NOT NULL,comment text CHECK(length(comment)<=2000),
 created_at timestamptz NOT NULL DEFAULT clock_timestamp(),UNIQUE(tenant_id,id),UNIQUE(tenant_id,reply_run_id),
 FOREIGN KEY(tenant_id,reply_run_id,text_hash) REFERENCES social_reply_runs(tenant_id,id,text_hash),
 FOREIGN KEY(tenant_id,created_by) REFERENCES tenant_memberships(tenant_id,principal_id)
);
CREATE TABLE social_reply_jobs (
 id uuid PRIMARY KEY DEFAULT gen_random_uuid(),tenant_id uuid NOT NULL REFERENCES tenants,
 reply_run_id uuid NOT NULL,workflow_run_id uuid NOT NULL,skill_run_id uuid NOT NULL,
 attempt int NOT NULL CHECK(attempt BETWEEN 1 AND 3),input_hash text NOT NULL CHECK(input_hash ~ '^[0-9a-f]{64}$'),
 status text NOT NULL DEFAULT 'RUNNING' CHECK(status IN ('RUNNING','SUCCEEDED','FAILED','UNKNOWN_OUTCOME')),
 retryable boolean NOT NULL DEFAULT false,retry_at timestamptz,error_category text CHECK(error_category ~ '^[A-Z][A-Z0-9_]{0,79}$'),
 result jsonb,result_hash text,started_at timestamptz NOT NULL DEFAULT clock_timestamp(),ended_at timestamptz,
 UNIQUE(tenant_id,id),UNIQUE(tenant_id,reply_run_id,attempt),
 FOREIGN KEY(tenant_id,reply_run_id,workflow_run_id) REFERENCES social_reply_runs(tenant_id,id,workflow_run_id),
 FOREIGN KEY(tenant_id,skill_run_id,workflow_run_id) REFERENCES skill_runs(tenant_id,id,workflow_run_id),
 CHECK((status='RUNNING')=(ended_at IS NULL)),
 CHECK((result IS NULL)=(result_hash IS NULL)),CHECK(result IS NULL OR result_hash=private.visual_hash(result)),
 CHECK((status='SUCCEEDED')=(result IS NOT NULL))
);
CREATE UNIQUE INDEX social_reply_active_job ON social_reply_jobs(tenant_id,reply_run_id) WHERE status='RUNNING';
CREATE UNIQUE INDEX social_reply_completed_job ON social_reply_jobs(tenant_id,reply_run_id) WHERE status='SUCCEEDED';
DO $$ DECLARE t text; BEGIN
 FOREACH t IN ARRAY ARRAY['social_comment_links','social_reply_runs','social_reply_decisions','social_reply_jobs'] LOOP
 EXECUTE format('ALTER TABLE %I ENABLE ROW LEVEL SECURITY',t);
 EXECUTE format('ALTER TABLE %I FORCE ROW LEVEL SECURITY',t);
 EXECUTE format('CREATE POLICY tenant_boundary ON %I USING (tenant_id=public.context_tenant()) WITH CHECK (tenant_id=public.context_tenant())',t);
 EXECUTE format('REVOKE ALL ON %I FROM PUBLIC,mediaos_runtime',t);
 EXECUTE format('GRANT SELECT ON %I TO mediaos_runtime',t);
 IF t IN ('social_comment_links','social_reply_decisions') THEN
 EXECUTE format('CREATE TRIGGER immutable BEFORE UPDATE OR DELETE ON %I FOR EACH ROW EXECUTE FUNCTION immutable_record()',t);
 END IF; END LOOP; END $$;

CREATE FUNCTION public.social_link_comment(webhookid uuid) RETURNS uuid
 LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,public AS $$
 DECLARE h public.social_webhook_events;c public.social_connections;p public.social_publish_runs;
 w public.workflow_runs;l public.social_comment_links;v jsonb;eventid uuid;k text; BEGIN
 IF NOT public.has_role('SOCIAL') THEN RAISE EXCEPTION 'Connector required' USING ERRCODE='42501'; END IF;
 SELECT * INTO h FROM public.social_webhook_events WHERE tenant_id=public.context_tenant() AND id=webhookid;
 IF h.id IS NULL THEN RAISE EXCEPTION 'Webhook not found' USING ERRCODE='P0002'; END IF;
 PERFORM private.conversion_object(h.payload,ARRAY['schema_version','account_id','comment_id','media_id','text','sender_id','username','occurred_at','event_hash']);
 SELECT * INTO c FROM public.social_connections WHERE tenant_id=h.tenant_id AND id=h.connection_id;
 IF h.payload->'schema_version' IS DISTINCT FROM '1'::jsonb OR h.payload->>'account_id' IS DISTINCT FROM c.account_id
 OR jsonb_typeof(h.payload->'comment_id') IS DISTINCT FROM 'string' OR h.payload->>'comment_id' !~ '^[0-9]{1,64}$'
 OR jsonb_typeof(h.payload->'media_id') IS DISTINCT FROM 'string' OR h.payload->>'media_id' !~ '^[0-9]{1,64}$'
 OR jsonb_typeof(h.payload->'text') IS DISTINCT FROM 'string'
 OR jsonb_typeof(h.payload->'event_hash') IS DISTINCT FROM 'string' OR h.payload->>'event_hash' !~ '^[0-9a-f]{64}$'
 OR jsonb_typeof(h.payload->'occurred_at') IS DISTINCT FROM 'string'
 OR h.payload->>'occurred_at' !~ '^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(\.\d{1,6})?(Z|\+00:00)$'
 OR NOT (h.payload->'username'='null'::jsonb OR (jsonb_typeof(h.payload->'username')='string' AND length(h.payload->>'username')<=64))
 OR NOT (h.payload->'sender_id'='null'::jsonb OR (jsonb_typeof(h.payload->'sender_id')='string' AND h.payload->>'sender_id' ~ '^[0-9]{1,64}$')) THEN
 RAISE EXCEPTION 'Normalized owned comment required' USING ERRCODE='23514'; END IF;
 -- Oversized/blank or unrelated post observations remain saved webhooks, not fabricated reviews.
 IF length(h.payload->>'text') NOT BETWEEN 1 AND 4000 OR length(btrim(h.payload->>'text'))=0 THEN RETURN NULL; END IF;
 SELECT * INTO p FROM public.social_publish_runs WHERE tenant_id=h.tenant_id AND connection_id=h.connection_id
 AND post_id=h.payload->>'media_id' AND status='PUBLISHED' ORDER BY created_at,id LIMIT 1;
 IF p.id IS NULL THEN RETURN NULL; END IF;
 SELECT * INTO w FROM public.workflow_runs WHERE tenant_id=p.tenant_id AND id=p.workflow_run_id FOR UPDATE;
 PERFORM private.social_connection_current(c.id);
 PERFORM pg_advisory_xact_lock(hashtextextended('social-comment:'||h.tenant_id::text||':'||c.id::text||':'||(h.payload->>'comment_id'),0));
 SELECT * INTO l FROM public.social_comment_links WHERE tenant_id=h.tenant_id AND webhook_event_id=h.id;
 IF l.id IS NOT NULL THEN RETURN l.event_id; END IF;
 v:=jsonb_build_object('schema_version',1,'mode','PLATFORM','origin','instagram:media:'||p.post_id||':comment:'||(h.payload->>'comment_id'),
 'participant_reference',CASE WHEN h.payload->>'sender_id' IS NULL THEN 'comment:'||(h.payload->>'comment_id') ELSE 'instagram:'||(h.payload->>'sender_id') END,
 'comment_text',h.payload->>'text','captured_at',to_char(h.captured_at AT TIME ZONE 'UTC','YYYY-MM-DD"T"HH24:MI:SS.US')||'+00:00');
 k:='instagram-webhook:'||h.id::text;
 INSERT INTO public.community_events(tenant_id,workflow_run_id,mode,origin,participant_reference,comment_text,captured_at,
 schema_version,payload,content_hash,created_by,correlation_id,idempotency_key,input_hash)
 VALUES(h.tenant_id,w.id,'PLATFORM',v->>'origin',v->>'participant_reference',v->>'comment_text',h.captured_at,1,v,
 private.visual_hash(v),public.context_principal(),w.correlation_id,k,private.visual_hash(jsonb_build_object('workflow_run_id',w.id,'payload',v))) RETURNING id INTO eventid;
 INSERT INTO public.social_comment_links(tenant_id,event_id,workflow_run_id,webhook_event_id,publish_run_id,connection_id,comment_id)
 VALUES(h.tenant_id,eventid,w.id,h.id,p.id,c.id,h.payload->>'comment_id');
 INSERT INTO public.audit_events(tenant_id,workflow_run_id,actor_id,event_type,correlation_id,details)
 VALUES(h.tenant_id,w.id,public.context_principal(),'PLATFORM_COMMENT_LINKED',w.correlation_id,
 jsonb_build_object('community_event_id',eventid,'webhook_event_id',h.id,'publish_run_id',p.id));
 RETURN eventid; END $$;

CREATE FUNCTION private.social_reply_current(r public.social_reply_runs) RETURNS void
 LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,public AS $$
 DECLARE review public.community_reviews;p public.social_publish_runs;l public.social_comment_links;expected text; BEGIN
 IF r.tenant_id IS DISTINCT FROM public.context_tenant() THEN RAISE EXCEPTION 'Owned reply required' USING ERRCODE='42501'; END IF;
 PERFORM 1 FROM public.workflow_runs WHERE tenant_id=r.tenant_id AND id=r.workflow_run_id FOR UPDATE;
 SELECT * INTO review FROM public.community_reviews WHERE tenant_id=r.tenant_id AND id=r.review_id FOR UPDATE;
 SELECT * INTO l FROM public.social_comment_links WHERE tenant_id=r.tenant_id AND id=r.comment_link_id;
 SELECT * INTO p FROM public.social_publish_runs WHERE tenant_id=r.tenant_id AND id=l.publish_run_id;
 PERFORM private.social_connection_current(r.connection_id);
 PERFORM pg_advisory_xact_lock(hashtextextended('social-comment:'||r.tenant_id::text||':'||r.connection_id::text||':'||r.comment_id,0));
 SELECT string_agg(value->>'text',E'\n' ORDER BY ordinal) INTO expected FROM jsonb_array_elements(review.draft->'blocks') WITH ORDINALITY b(value,ordinal);
 expected:=expected||E'\n'||(review.draft->>'disclosure');
 IF review.status IS DISTINCT FROM 'REVIEWED_DRAFT' OR review.draft_hash IS DISTINCT FROM r.draft_hash OR review.qa_hash IS DISTINCT FROM r.qa_hash
 OR private.community_qa(review) IS DISTINCT FROM review.qa OR review.qa->>'status' IS DISTINCT FROM 'PASS'
 OR r.exact_text IS DISTINCT FROM expected OR r.text_hash IS DISTINCT FROM private.visual_hash(to_jsonb(expected))
 OR p.status IS DISTINCT FROM 'PUBLISHED' OR p.asset_version_id IS DISTINCT FROM review.asset_version_id
 OR p.research_version_id IS DISTINCT FROM review.research_version_id OR p.qa_report_id IS DISTINCT FROM review.qa_report_id
 OR NOT EXISTS(SELECT 1 FROM public.community_events WHERE tenant_id=r.tenant_id AND id=r.event_id AND mode='PLATFORM')
 OR NOT EXISTS(SELECT 1 FROM public.community_decisions WHERE tenant_id=r.tenant_id AND id=r.community_decision_id
 AND review_id=r.review_id AND decision='APPROVE' AND draft_hash=r.draft_hash AND qa_hash=r.qa_hash)
 OR NOT EXISTS(SELECT 1 FROM public.social_connections WHERE tenant_id=r.tenant_id AND id=r.connection_id AND scopes ? 'instagram_business_manage_comments')
 OR EXISTS(SELECT 1 FROM public.social_comment_links WHERE tenant_id=r.tenant_id AND connection_id=r.connection_id AND comment_id=r.comment_id AND sequence>l.sequence)
 OR EXISTS(SELECT 1 FROM public.social_reply_runs newer WHERE newer.tenant_id=r.tenant_id AND newer.connection_id=r.connection_id
 AND newer.comment_id=r.comment_id AND (newer.sequence>r.sequence OR (newer.id<>r.id AND newer.status IN ('SENDING','SENT','UNKNOWN_OUTCOME')))) THEN
 RAISE EXCEPTION 'Current approved exact platform reply required' USING ERRCODE='23514'; END IF;
 END $$;
CREATE FUNCTION private.social_reply_audit(r public.social_reply_runs,event text,extra jsonb DEFAULT '{}'::jsonb) RETURNS void
 LANGUAGE sql SECURITY DEFINER SET search_path=pg_catalog,public AS $$
 INSERT INTO public.audit_events(tenant_id,workflow_run_id,actor_id,event_type,correlation_id,details)
 VALUES(r.tenant_id,r.workflow_run_id,public.context_principal(),event,r.correlation_id,
 jsonb_build_object('reply_run_id',r.id,'community_review_id',r.review_id,'status',r.status,'text_hash',r.text_hash)||extra) $$;

CREATE FUNCTION public.social_start_reply(reviewid uuid,key text,inputhash text) RETURNS uuid
 LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,public AS $$
 DECLARE v public.community_reviews;l public.social_comment_links;r public.social_reply_runs;d public.community_decisions; BEGIN
 IF NOT public.has_role('OPERATOR') THEN RAISE EXCEPTION 'Operator required' USING ERRCODE='42501'; END IF;
 IF key IS NULL OR length(key) NOT BETWEEN 1 AND 128 OR inputhash IS DISTINCT FROM private.visual_hash(jsonb_build_object('review_id',reviewid)) THEN
 RAISE EXCEPTION 'Valid reply idempotency input required' USING ERRCODE='23514'; END IF;
 PERFORM pg_advisory_xact_lock(hashtextextended('social-reply-key:'||public.context_tenant()::text||':'||key,0));
 SELECT * INTO r FROM public.social_reply_runs WHERE tenant_id=public.context_tenant() AND idempotency_key=key;
 IF r.id IS NOT NULL THEN
 IF r.input_hash IS DISTINCT FROM inputhash THEN RAISE EXCEPTION 'Reply key conflict' USING ERRCODE='23505'; END IF; RETURN r.id; END IF;
 SELECT * INTO v FROM public.community_reviews WHERE tenant_id=public.context_tenant() AND id=reviewid;
 IF v.id IS NULL THEN RAISE EXCEPTION 'Review not found' USING ERRCODE='P0002'; END IF;
 SELECT * INTO l FROM public.social_comment_links WHERE tenant_id=v.tenant_id AND event_id=v.event_id;
 SELECT * INTO d FROM public.community_decisions WHERE tenant_id=v.tenant_id AND review_id=v.id AND decision='APPROVE';
 IF l.id IS NULL OR d.id IS NULL THEN RAISE EXCEPTION 'Reviewed platform comment required' USING ERRCODE='23514'; END IF;
 r.id:=gen_random_uuid();r.tenant_id:=v.tenant_id;r.review_id:=v.id;r.event_id:=v.event_id;r.workflow_run_id:=v.workflow_run_id;
 r.comment_link_id:=l.id;r.connection_id:=l.connection_id;r.publish_run_id:=l.publish_run_id;r.comment_id:=l.comment_id;
 r.asset_version_id:=v.asset_version_id;r.research_version_id:=v.research_version_id;r.qa_report_id:=v.qa_report_id;
 r.community_decision_id:=d.id;r.draft_hash:=v.draft_hash;r.qa_hash:=v.qa_hash;
 SELECT string_agg(value->>'text',E'\n' ORDER BY ordinal)||E'\n'||(v.draft->>'disclosure') INTO r.exact_text
 FROM jsonb_array_elements(v.draft->'blocks') WITH ORDINALITY b(value,ordinal);
 r.text_hash:=private.visual_hash(to_jsonb(r.exact_text));r.correlation_id:=v.correlation_id;
 PERFORM private.social_reply_current(r);
 INSERT INTO public.social_reply_runs(id,tenant_id,review_id,event_id,workflow_run_id,comment_link_id,connection_id,publish_run_id,
 comment_id,asset_version_id,research_version_id,qa_report_id,community_decision_id,draft_hash,qa_hash,exact_text,text_hash,
 idempotency_key,input_hash,created_by,correlation_id)
 VALUES(r.id,r.tenant_id,r.review_id,r.event_id,r.workflow_run_id,r.comment_link_id,r.connection_id,r.publish_run_id,r.comment_id,
 r.asset_version_id,r.research_version_id,r.qa_report_id,r.community_decision_id,r.draft_hash,r.qa_hash,r.exact_text,r.text_hash,
 key,inputhash,public.context_principal(),r.correlation_id) RETURNING * INTO r;
 PERFORM private.social_reply_audit(r,'SOCIAL_REPLY_CREATED');RETURN r.id; END $$;

CREATE FUNCTION public.social_decide_reply(replyid uuid,p jsonb) RETURNS void
 LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,public AS $$
 DECLARE r public.social_reply_runs; BEGIN
 IF NOT public.has_role('APPROVER') THEN RAISE EXCEPTION 'Approver required' USING ERRCODE='42501'; END IF;
 PERFORM private.conversion_object(p,ARRAY['decision','text_hash','comment']);
 SELECT * INTO r FROM public.social_reply_runs WHERE tenant_id=public.context_tenant() AND id=replyid;
 IF r.id IS NULL THEN RAISE EXCEPTION 'Reply not found' USING ERRCODE='P0002'; END IF;
 PERFORM private.social_reply_current(r);
 SELECT * INTO r FROM public.social_reply_runs WHERE tenant_id=r.tenant_id AND id=r.id FOR UPDATE;
 IF r.status<>'AWAITING_REPLY_APPROVAL' OR p->>'decision' IS NULL OR p->>'decision' NOT IN ('AUTHORIZE_REPLY','REJECT')
 OR p->>'text_hash' IS DISTINCT FROM r.text_hash OR NOT (p->'comment'='null'::jsonb OR
 (jsonb_typeof(p->'comment')='string' AND length(p->>'comment')<=2000)) THEN
 RAISE EXCEPTION 'Exact reply authorization required' USING ERRCODE='23514'; END IF;
 INSERT INTO public.social_reply_decisions(tenant_id,reply_run_id,text_hash,decision,created_by,comment)
 VALUES(r.tenant_id,r.id,r.text_hash,p->>'decision',public.context_principal(),p->>'comment');
 UPDATE public.social_reply_runs SET status=CASE p->>'decision' WHEN 'AUTHORIZE_REPLY' THEN 'AUTHORIZED' ELSE 'REJECTED' END,
 updated_at=clock_timestamp() WHERE id=r.id RETURNING * INTO r;
 PERFORM private.social_reply_audit(r,'SOCIAL_REPLY_DECIDED'); END $$;

CREATE FUNCTION public.social_reserve_reply(replyid uuid) RETURNS uuid
 LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,public AS $$
 DECLARE r public.social_reply_runs;j public.social_reply_jobs;sid uuid;result uuid;n int; BEGIN
 IF NOT public.has_role('SOCIAL') THEN RAISE EXCEPTION 'Connector required' USING ERRCODE='42501'; END IF;
 SELECT * INTO r FROM public.social_reply_runs WHERE tenant_id=public.context_tenant() AND id=replyid;
 IF r.id IS NULL THEN RAISE EXCEPTION 'Reply not found' USING ERRCODE='P0002'; END IF;
 PERFORM private.social_reply_current(r);
 SELECT * INTO r FROM public.social_reply_runs WHERE tenant_id=r.tenant_id AND id=r.id FOR UPDATE;
 SELECT * INTO j FROM public.social_reply_jobs WHERE tenant_id=r.tenant_id AND reply_run_id=r.id ORDER BY attempt DESC LIMIT 1;
 IF r.status<>'AUTHORIZED' OR NOT EXISTS(SELECT 1 FROM public.social_reply_decisions WHERE tenant_id=r.tenant_id AND reply_run_id=r.id
 AND decision='AUTHORIZE_REPLY' AND text_hash=r.text_hash) OR (j.id IS NOT NULL AND
 (j.status IN ('RUNNING','SUCCEEDED','UNKNOWN_OUTCOME') OR NOT j.retryable OR j.retry_at>clock_timestamp())) THEN
 RAISE EXCEPTION 'Authorized reply and retry checkpoint required' USING ERRCODE='23514'; END IF;
 n:=coalesce(j.attempt,0)+1;
 IF n>3 THEN RAISE EXCEPTION 'Reply attempts exhausted' USING ERRCODE='23514'; END IF;
 INSERT INTO public.skill_runs(tenant_id,workflow_run_id,step_key,skill_identifier,skill_version,input_schema_version,
 output_schema_version,provider,model,adapter,attempt,input_hash,status,is_mock,cost)
 VALUES(r.tenant_id,r.workflow_run_id,'social.reply:'||r.id::text,'social.reply','1.0.0',1,1,'instagram','graph-api',
 'instagram-login-v1',n,r.text_hash,'RUNNING',false,NULL) RETURNING id INTO sid;
 INSERT INTO public.cost_events(tenant_id,workflow_run_id,skill_run_id,provider,model,input_tokens,output_tokens,cost,currency,price_version)
 VALUES(r.tenant_id,r.workflow_run_id,sid,'instagram','graph-api',0,0,NULL,'USD','provider-unreported');
 INSERT INTO public.social_reply_jobs(tenant_id,reply_run_id,workflow_run_id,skill_run_id,attempt,input_hash)
 VALUES(r.tenant_id,r.id,r.workflow_run_id,sid,n,r.text_hash) RETURNING id INTO result;
 UPDATE public.social_reply_runs SET status='SENDING',updated_at=clock_timestamp() WHERE id=r.id RETURNING * INTO r;
 PERFORM private.social_reply_audit(r,'SOCIAL_REPLY_STARTED',jsonb_build_object('job_id',result,'attempt',n));
 RETURN result; END $$;

CREATE FUNCTION public.social_guard_reply(jobid uuid) RETURNS void
 LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,public AS $$
 DECLARE r public.social_reply_runs;j public.social_reply_jobs; BEGIN
 IF NOT public.has_role('SOCIAL') THEN RAISE EXCEPTION 'Connector required' USING ERRCODE='42501'; END IF;
 SELECT * INTO j FROM public.social_reply_jobs WHERE tenant_id=public.context_tenant() AND id=jobid;
 SELECT * INTO r FROM public.social_reply_runs WHERE tenant_id=public.context_tenant() AND id=j.reply_run_id;
 IF j.status IS DISTINCT FROM 'RUNNING' OR r.status IS DISTINCT FROM 'SENDING' THEN
 RAISE EXCEPTION 'Reserved reply attempt required' USING ERRCODE='23514'; END IF;
 PERFORM private.social_reply_current(r);
 IF NOT EXISTS(SELECT 1 FROM public.social_reply_decisions WHERE tenant_id=r.tenant_id AND reply_run_id=r.id
 AND decision='AUTHORIZE_REPLY' AND text_hash=r.text_hash) THEN
 RAISE EXCEPTION 'Explicit reply authorization required' USING ERRCODE='23514'; END IF;
 END $$;

CREATE FUNCTION public.social_finish_reply(jobid uuid,p jsonb) RETURNS void
 LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,public AS $$
 DECLARE j public.social_reply_jobs;r public.social_reply_runs;captured timestamptz; BEGIN
 IF NOT public.has_role('SOCIAL') THEN RAISE EXCEPTION 'Connector required' USING ERRCODE='42501'; END IF;
 SELECT * INTO j FROM public.social_reply_jobs WHERE tenant_id=public.context_tenant() AND id=jobid;
 SELECT * INTO r FROM public.social_reply_runs WHERE tenant_id=public.context_tenant() AND id=j.reply_run_id;
 IF r.id IS NULL THEN RAISE EXCEPTION 'Reply not found' USING ERRCODE='P0002'; END IF;
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

CREATE FUNCTION public.social_fail_reply(jobid uuid,category text,retry boolean,unknown boolean,delay_seconds int DEFAULT 0) RETURNS void
 LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,public AS $$
 DECLARE r public.social_reply_runs;j public.social_reply_jobs;can_retry boolean;retrytime timestamptz; BEGIN
 IF NOT public.has_role('SOCIAL') THEN RAISE EXCEPTION 'Connector required' USING ERRCODE='42501'; END IF;
 SELECT * INTO j FROM public.social_reply_jobs WHERE tenant_id=public.context_tenant() AND id=jobid;
 SELECT * INTO r FROM public.social_reply_runs WHERE tenant_id=public.context_tenant() AND id=j.reply_run_id FOR UPDATE;
 SELECT * INTO j FROM public.social_reply_jobs WHERE tenant_id=public.context_tenant() AND id=jobid FOR UPDATE;
 IF j.status IS DISTINCT FROM 'RUNNING' OR category IS NULL OR category!~'^[A-Z][A-Z0-9_]{0,79}$'
 OR retry IS NULL OR unknown IS NULL OR delay_seconds IS NULL OR delay_seconds NOT BETWEEN 0 AND 86400
 OR (category IN ('UNKNOWN_OUTCOME','PROCESS_INTERRUPTED','LOCAL_RECEIPT_FAILED') AND NOT unknown)
 OR (retry AND category<>'RATE_LIMITED') THEN
 RAISE EXCEPTION 'Valid bounded reply failure required' USING ERRCODE='23514'; END IF;
 can_retry:=retry AND NOT unknown AND j.attempt<3;
 IF can_retry THEN retrytime:=clock_timestamp()+make_interval(secs=>greatest(delay_seconds,power(2,j.attempt)::int)); END IF;
 UPDATE public.social_reply_jobs SET status=CASE WHEN unknown THEN 'UNKNOWN_OUTCOME' ELSE 'FAILED' END,
 retryable=can_retry,retry_at=retrytime,error_category=category,ended_at=clock_timestamp() WHERE id=j.id;
 UPDATE public.skill_runs SET status='FAILED',retryable=can_retry,retry_at=retrytime,error_category=category,
 ended_at=clock_timestamp(),latency_ms=extract(epoch FROM clock_timestamp()-j.started_at)*1000 WHERE id=j.skill_run_id;
 UPDATE public.social_reply_runs SET status=CASE WHEN unknown THEN 'UNKNOWN_OUTCOME' WHEN can_retry THEN 'AUTHORIZED'
 WHEN category='POLICY_BLOCKED' THEN 'BLOCKED' ELSE 'FAILED' END,updated_at=clock_timestamp() WHERE id=r.id RETURNING * INTO r;
 PERFORM private.social_reply_audit(r,'SOCIAL_REPLY_FAILED',jsonb_build_object('job_id',j.id,'attempt',j.attempt,'error_category',category,'retryable',can_retry)); END $$;

REVOKE ALL ON FUNCTION private.social_reply_current(public.social_reply_runs),private.social_reply_audit(public.social_reply_runs,text,jsonb) FROM PUBLIC,mediaos_runtime;
REVOKE ALL ON FUNCTION public.social_link_comment(uuid),public.social_start_reply(uuid,text,text),public.social_decide_reply(uuid,jsonb),
 public.social_reserve_reply(uuid),public.social_guard_reply(uuid),public.social_finish_reply(uuid,jsonb),public.social_fail_reply(uuid,text,boolean,boolean,int) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION public.social_link_comment(uuid),public.social_start_reply(uuid,text,text),public.social_decide_reply(uuid,jsonb),
 public.social_reserve_reply(uuid),public.social_guard_reply(uuid),public.social_finish_reply(uuid,jsonb),public.social_fail_reply(uuid,text,boolean,boolean,int) TO mediaos_runtime;
