-- Account identity survives token reconnection; historical publications stay immutable.
ALTER TABLE social_publish_runs ADD CONSTRAINT social_published_account_identity
 UNIQUE(tenant_id,account_id,post_id);
ALTER TABLE social_publish_runs ADD CONSTRAINT social_publish_account_lineage
 UNIQUE(tenant_id,id,workflow_run_id,account_id);
ALTER TABLE social_comment_links ADD COLUMN account_id text;
ALTER TABLE social_reply_runs ADD COLUMN account_id text;
-- Derived backfill only. Existing event/connection/publication evidence is unchanged.
ALTER TABLE social_comment_links DISABLE TRIGGER immutable;
UPDATE social_comment_links l SET account_id=c.account_id FROM social_connections c
 WHERE c.tenant_id=l.tenant_id AND c.id=l.connection_id;
ALTER TABLE social_comment_links ENABLE TRIGGER immutable;
UPDATE social_reply_runs r SET account_id=c.account_id FROM social_connections c
 WHERE c.tenant_id=r.tenant_id AND c.id=r.connection_id;
ALTER TABLE social_comment_links ALTER COLUMN account_id SET NOT NULL;
ALTER TABLE social_reply_runs ALTER COLUMN account_id SET NOT NULL;
-- Remove only the old constraint conflating the intake and publication connection.
DO $$ DECLARE item record; BEGIN
 FOR item IN SELECT conname FROM pg_constraint WHERE conrelid='public.social_comment_links'::regclass
 AND contype='f' AND confrelid='public.social_publish_runs'::regclass LOOP
 EXECUTE format('ALTER TABLE public.social_comment_links DROP CONSTRAINT %I',item.conname);
 END LOOP;
END $$;
ALTER TABLE social_comment_links ADD CONSTRAINT social_comment_publication_account
 FOREIGN KEY(tenant_id,publish_run_id,workflow_run_id,account_id)
 REFERENCES social_publish_runs(tenant_id,id,workflow_run_id,account_id);
ALTER TABLE social_comment_links ADD CONSTRAINT social_comment_connection_account
 FOREIGN KEY(tenant_id,connection_id,account_id) REFERENCES social_connections(tenant_id,id,account_id);
ALTER TABLE social_comment_links ADD CONSTRAINT social_comment_link_account
 UNIQUE(tenant_id,id,event_id,workflow_run_id,publish_run_id,comment_id,account_id);
DO $$ DECLARE item record; BEGIN
 FOR item IN SELECT conname FROM pg_constraint WHERE conrelid='public.social_reply_runs'::regclass
 AND contype='f' AND confrelid='public.social_comment_links'::regclass LOOP
 EXECUTE format('ALTER TABLE public.social_reply_runs DROP CONSTRAINT %I',item.conname);
 END LOOP;
END $$;
ALTER TABLE social_reply_runs ADD CONSTRAINT social_reply_link_account
 FOREIGN KEY(tenant_id,comment_link_id,event_id,workflow_run_id,publish_run_id,comment_id,account_id)
 REFERENCES social_comment_links(tenant_id,id,event_id,workflow_run_id,publish_run_id,comment_id,account_id);
ALTER TABLE social_reply_runs ADD CONSTRAINT social_reply_connection_account
 FOREIGN KEY(tenant_id,connection_id,account_id) REFERENCES social_connections(tenant_id,id,account_id);
DROP INDEX social_comment_single_dispatch;
CREATE UNIQUE INDEX social_comment_single_dispatch ON social_reply_runs(tenant_id,account_id,comment_id)
 WHERE status IN ('SENDING','SENT','UNKNOWN_OUTCOME');
CREATE INDEX social_comment_account_history ON social_comment_links(tenant_id,account_id,comment_id,sequence);

CREATE OR REPLACE FUNCTION public.social_link_comment(webhookid uuid) RETURNS uuid
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
 SELECT * INTO p FROM public.social_publish_runs WHERE tenant_id=h.tenant_id AND account_id=c.account_id
 AND post_id=h.payload->>'media_id' AND status='PUBLISHED' ORDER BY created_at,id LIMIT 1;
 IF p.id IS NULL THEN RETURN NULL; END IF;
 SELECT * INTO w FROM public.workflow_runs WHERE tenant_id=p.tenant_id AND id=p.workflow_run_id FOR UPDATE;
 -- Intake retains its original connection in h. A replay after reconnect may
 -- complete an unfinished link using the current connection for that account.
 SELECT * INTO c FROM public.social_connections WHERE tenant_id=h.tenant_id AND account_id=p.account_id
 ORDER BY version DESC LIMIT 1;
 PERFORM private.social_connection_current(c.id);
 IF c.influencer_id IS DISTINCT FROM w.influencer_id THEN
 RAISE EXCEPTION 'Comment account influencer mismatch' USING ERRCODE='23514'; END IF;
 PERFORM pg_advisory_xact_lock(hashtextextended('social-comment:'||h.tenant_id::text||':'||c.account_id||':'||(h.payload->>'comment_id'),0));
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
 INSERT INTO public.social_comment_links(tenant_id,event_id,workflow_run_id,webhook_event_id,publish_run_id,connection_id,comment_id,account_id)
 VALUES(h.tenant_id,eventid,w.id,h.id,p.id,c.id,h.payload->>'comment_id',c.account_id);
 INSERT INTO public.audit_events(tenant_id,workflow_run_id,actor_id,event_type,correlation_id,details)
 VALUES(h.tenant_id,w.id,public.context_principal(),'PLATFORM_COMMENT_LINKED',w.correlation_id,
 jsonb_build_object('community_event_id',eventid,'webhook_event_id',h.id,'publish_run_id',p.id));
 RETURN eventid; END $$;

CREATE OR REPLACE FUNCTION private.social_reply_current(r public.social_reply_runs) RETURNS void
 LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,public AS $$
 DECLARE review public.community_reviews;p public.social_publish_runs;l public.social_comment_links;c public.social_connections;expected text; BEGIN
 IF r.tenant_id IS DISTINCT FROM public.context_tenant() THEN RAISE EXCEPTION 'Owned reply required' USING ERRCODE='42501'; END IF;
 PERFORM 1 FROM public.workflow_runs WHERE tenant_id=r.tenant_id AND id=r.workflow_run_id FOR UPDATE;
 SELECT * INTO review FROM public.community_reviews WHERE tenant_id=r.tenant_id AND id=r.review_id FOR UPDATE;
 SELECT * INTO l FROM public.social_comment_links WHERE tenant_id=r.tenant_id AND id=r.comment_link_id;
 SELECT * INTO p FROM public.social_publish_runs WHERE tenant_id=r.tenant_id AND id=l.publish_run_id;
 SELECT * INTO c FROM public.social_connections WHERE tenant_id=r.tenant_id AND id=r.connection_id;
 PERFORM private.social_connection_current(r.connection_id);
 PERFORM pg_advisory_xact_lock(hashtextextended('social-comment:'||r.tenant_id::text||':'||r.account_id||':'||r.comment_id,0));
 SELECT string_agg(value->>'text',E'\n' ORDER BY ordinal) INTO expected FROM jsonb_array_elements(review.draft->'blocks') WITH ORDINALITY b(value,ordinal);
 expected:=expected||E'\n'||(review.draft->>'disclosure');
 IF c.account_id IS DISTINCT FROM r.account_id OR l.account_id IS DISTINCT FROM r.account_id
 OR p.account_id IS DISTINCT FROM r.account_id
 OR NOT EXISTS(SELECT 1 FROM public.workflow_runs WHERE tenant_id=r.tenant_id AND id=r.workflow_run_id AND influencer_id=c.influencer_id)
 OR review.status IS DISTINCT FROM 'REVIEWED_DRAFT' OR review.draft_hash IS DISTINCT FROM r.draft_hash OR review.qa_hash IS DISTINCT FROM r.qa_hash
 OR private.community_qa(review) IS DISTINCT FROM review.qa OR review.qa->>'status' IS DISTINCT FROM 'PASS'
 OR r.exact_text IS DISTINCT FROM expected OR r.text_hash IS DISTINCT FROM private.visual_hash(to_jsonb(expected))
 OR p.status IS DISTINCT FROM 'PUBLISHED' OR p.asset_version_id IS DISTINCT FROM review.asset_version_id
 OR p.research_version_id IS DISTINCT FROM review.research_version_id OR p.qa_report_id IS DISTINCT FROM review.qa_report_id
 OR NOT EXISTS(SELECT 1 FROM public.community_events WHERE tenant_id=r.tenant_id AND id=r.event_id AND mode='PLATFORM')
 OR NOT EXISTS(SELECT 1 FROM public.community_decisions WHERE tenant_id=r.tenant_id AND id=r.community_decision_id
 AND review_id=r.review_id AND decision='APPROVE' AND draft_hash=r.draft_hash AND qa_hash=r.qa_hash)
 OR NOT EXISTS(SELECT 1 FROM public.social_connections WHERE tenant_id=r.tenant_id AND id=r.connection_id AND scopes ? 'instagram_business_manage_comments')
 OR EXISTS(SELECT 1 FROM public.social_comment_links WHERE tenant_id=r.tenant_id AND account_id=r.account_id AND comment_id=r.comment_id AND sequence>l.sequence)
 OR EXISTS(SELECT 1 FROM public.social_reply_runs newer WHERE newer.tenant_id=r.tenant_id AND newer.account_id=r.account_id
 AND newer.comment_id=r.comment_id AND (newer.sequence>r.sequence OR (newer.id<>r.id AND newer.status IN ('SENDING','SENT','UNKNOWN_OUTCOME')))) THEN
 RAISE EXCEPTION 'Current approved exact platform reply required' USING ERRCODE='23514'; END IF;
 END $$;

CREATE OR REPLACE FUNCTION public.social_start_reply(reviewid uuid,key text,inputhash text) RETURNS uuid
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
 r.comment_link_id:=l.id;r.account_id:=l.account_id;r.publish_run_id:=l.publish_run_id;r.comment_id:=l.comment_id;
 -- Reconnection never rewrites an intent or transfers its outbound approval.
 -- A new key can create a new intent for the reviewed text, pinned to the new
 -- connection, which must receive its own AUTHORIZE_REPLY decision.
 SELECT id INTO r.connection_id FROM public.social_connections WHERE tenant_id=r.tenant_id AND account_id=r.account_id
 ORDER BY version DESC LIMIT 1;
 r.asset_version_id:=v.asset_version_id;r.research_version_id:=v.research_version_id;r.qa_report_id:=v.qa_report_id;
 r.community_decision_id:=d.id;r.draft_hash:=v.draft_hash;r.qa_hash:=v.qa_hash;
 SELECT string_agg(value->>'text',E'\n' ORDER BY ordinal)||E'\n'||(v.draft->>'disclosure') INTO r.exact_text
 FROM jsonb_array_elements(v.draft->'blocks') WITH ORDINALITY b(value,ordinal);
 r.text_hash:=private.visual_hash(to_jsonb(r.exact_text));r.correlation_id:=v.correlation_id;
 PERFORM private.social_reply_current(r);
 INSERT INTO public.social_reply_runs(id,tenant_id,review_id,event_id,workflow_run_id,comment_link_id,connection_id,account_id,publish_run_id,
 comment_id,asset_version_id,research_version_id,qa_report_id,community_decision_id,draft_hash,qa_hash,exact_text,text_hash,
 idempotency_key,input_hash,created_by,correlation_id)
 VALUES(r.id,r.tenant_id,r.review_id,r.event_id,r.workflow_run_id,r.comment_link_id,r.connection_id,r.account_id,r.publish_run_id,r.comment_id,
 r.asset_version_id,r.research_version_id,r.qa_report_id,r.community_decision_id,r.draft_hash,r.qa_hash,r.exact_text,r.text_hash,
 key,inputhash,public.context_principal(),r.correlation_id) RETURNING * INTO r;
 PERFORM private.social_reply_audit(r,'SOCIAL_REPLY_CREATED');RETURN r.id; END $$;
