-- Render approval extends, rather than replaces, the frozen content approval gate.
-- Only the server renderer reads/writes PNG bytes; PostgreSQL seals their manifest.
CREATE FUNCTION private.visual_canonical(value jsonb) RETURNS text
 LANGUAGE plpgsql IMMUTABLE SET search_path=pg_catalog,public AS $$
 DECLARE result text;
 BEGIN
 CASE jsonb_typeof(value)
 WHEN 'object' THEN
 SELECT '{'||coalesce(string_agg(to_jsonb(k)::text||':'||private.visual_canonical(v),',' ORDER BY k COLLATE "C"),'')||'}'
 INTO result FROM jsonb_each(value) AS fields(k,v);
 WHEN 'array' THEN
 SELECT '['||coalesce(string_agg(private.visual_canonical(v),',' ORDER BY n),'')||']'
 INTO result FROM jsonb_array_elements(value) WITH ORDINALITY AS items(v,n);
 ELSE result:=value::text;
 END CASE;
 RETURN result;
 END $$;
CREATE FUNCTION private.visual_hash(value jsonb) RETURNS text
 LANGUAGE sql IMMUTABLE SET search_path=pg_catalog,public AS $$
 SELECT encode(public.digest(private.visual_canonical(value),'sha256'),'hex') $$;

CREATE TABLE visual_config_versions (
 id uuid PRIMARY KEY DEFAULT gen_random_uuid(), tenant_id uuid NOT NULL REFERENCES tenants,
 influencer_id uuid NOT NULL, version int NOT NULL CHECK(version>0),
 schema_version int NOT NULL CHECK(schema_version=1), payload jsonb NOT NULL,
 content_hash text NOT NULL CHECK(content_hash ~ '^[0-9a-f]{64}$'),
 font_hashes jsonb NOT NULL, reference_sha256 text CHECK(reference_sha256 ~ '^[0-9a-f]{64}$'),
 reference_path text, reference_metadata jsonb NOT NULL DEFAULT '{}',
 created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
 UNIQUE(tenant_id,id), UNIQUE(tenant_id,influencer_id,version),
 FOREIGN KEY(tenant_id,influencer_id) REFERENCES influencers(tenant_id,id),
 CHECK(jsonb_typeof(payload)='object' AND payload->>'schema_version'='1'),
 CHECK(length(btrim(payload->>'display_name')) BETWEEN 1 AND 2000),
 CHECK(length(btrim(payload->>'required_disclosure')) BETWEEN 1 AND 2000),
 CHECK(payload ?& ARRAY['display_name','required_disclosure','regular_font_path','bold_font_path']),
 CHECK(payload->>'regular_font_path' ~ '^[A-Za-z0-9][A-Za-z0-9_./-]*\.(ttf|otf)$'
   AND payload->>'regular_font_path' !~ '(^|/)\.\.(/|$)'),
 CHECK(payload->>'bold_font_path' ~ '^[A-Za-z0-9][A-Za-z0-9_./-]*\.(ttf|otf)$'
   AND payload->>'bold_font_path' !~ '(^|/)\.\.(/|$)'),
 CHECK(jsonb_typeof(font_hashes)='object' AND font_hashes ?& ARRAY['regular','bold']
   AND font_hashes-'regular'-'bold'='{}'::jsonb
   AND font_hashes->>'regular' ~ '^[0-9a-f]{64}$'
   AND font_hashes->>'bold' ~ '^[0-9a-f]{64}$'),
 CHECK(content_hash=private.visual_hash(payload)),
 CHECK((reference_path IS NULL)=(reference_sha256 IS NULL)),
 CHECK(reference_path IS NULL OR (reference_path ~ '^[A-Za-z0-9][A-Za-z0-9_./-]*\.(png|jpg|jpeg|webp)$'
   AND reference_path !~ '(^|/)\.\.(/|$)')),
 CHECK(jsonb_typeof(reference_metadata)='object')
);

CREATE TABLE render_runs (
 id uuid PRIMARY KEY DEFAULT gen_random_uuid(), tenant_id uuid NOT NULL REFERENCES tenants,
 sequence bigint GENERATED ALWAYS AS IDENTITY,
 workflow_run_id uuid NOT NULL, asset_version_id uuid NOT NULL,
 research_version_id uuid NOT NULL, qa_report_id uuid NOT NULL,
 visual_config_version_id uuid NOT NULL, created_by uuid NOT NULL,
 correlation_id uuid NOT NULL, idempotency_key text NOT NULL CHECK(length(idempotency_key) BETWEEN 1 AND 128),
 input_hash text NOT NULL CHECK(input_hash ~ '^[0-9a-f]{64}$'),
 status text NOT NULL DEFAULT 'CREATED'
   CHECK(status IN ('CREATED','RENDERING','PASS','REVISION_REQUIRED','BLOCKED','FAILED')),
 manifest jsonb, manifest_hash text CHECK(manifest_hash ~ '^[0-9a-f]{64}$'),
 error_category text CHECK(length(error_category) BETWEEN 1 AND 80),
 started_at timestamptz, ended_at timestamptz, created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
 UNIQUE(tenant_id,id), UNIQUE(tenant_id,idempotency_key), UNIQUE(sequence),
 UNIQUE(tenant_id,id,workflow_run_id,asset_version_id,research_version_id,qa_report_id,manifest_hash),
 FOREIGN KEY(tenant_id,workflow_run_id) REFERENCES workflow_runs(tenant_id,id),
 FOREIGN KEY(tenant_id,asset_version_id,workflow_run_id,research_version_id)
   REFERENCES content_asset_versions(tenant_id,id,workflow_run_id,research_version_id),
 FOREIGN KEY(tenant_id,qa_report_id,workflow_run_id,asset_version_id,research_version_id)
   REFERENCES qa_reports(tenant_id,id,workflow_run_id,asset_version_id,research_version_id),
 FOREIGN KEY(tenant_id,visual_config_version_id) REFERENCES visual_config_versions(tenant_id,id),
 FOREIGN KEY(tenant_id,created_by) REFERENCES tenant_memberships(tenant_id,principal_id),
 CHECK((status IN ('PASS','REVISION_REQUIRED','BLOCKED'))=(manifest IS NOT NULL)),
 CHECK((manifest IS NULL)=(manifest_hash IS NULL)),
 CHECK(manifest IS NULL OR (jsonb_typeof(manifest)='object' AND octet_length(manifest::text)<=2000000)),
 CHECK((status IN ('PASS','REVISION_REQUIRED','BLOCKED','FAILED'))=(ended_at IS NOT NULL)),
 CHECK(started_at IS NULL OR started_at>=created_at),
 CHECK(ended_at IS NULL OR ended_at>=coalesce(started_at,created_at))
);
CREATE INDEX render_workflow_history ON render_runs(tenant_id,workflow_run_id,sequence);

ALTER TABLE approval_records ADD CONSTRAINT approval_exact_visual_lineage
 UNIQUE(tenant_id,id,workflow_run_id,asset_version_id,research_version_id,qa_report_id);
CREATE TABLE visual_approval_records (
 id uuid PRIMARY KEY DEFAULT gen_random_uuid(), tenant_id uuid NOT NULL REFERENCES tenants,
 render_run_id uuid NOT NULL, workflow_run_id uuid NOT NULL, asset_version_id uuid NOT NULL,
 research_version_id uuid NOT NULL, qa_report_id uuid NOT NULL,
 content_approval_record_id uuid NOT NULL, manifest_hash text NOT NULL CHECK(manifest_hash ~ '^[0-9a-f]{64}$'),
 decision text NOT NULL CHECK(decision IN ('APPROVE','REJECT')), approver_id uuid NOT NULL,
 comment text CHECK(length(comment)<=2000), created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
 UNIQUE(tenant_id,id), UNIQUE(tenant_id,render_run_id),
 FOREIGN KEY(tenant_id,render_run_id,workflow_run_id,asset_version_id,research_version_id,qa_report_id,manifest_hash)
   REFERENCES render_runs(tenant_id,id,workflow_run_id,asset_version_id,research_version_id,qa_report_id,manifest_hash),
 FOREIGN KEY(tenant_id,content_approval_record_id,workflow_run_id,asset_version_id,research_version_id,qa_report_id)
   REFERENCES approval_records(tenant_id,id,workflow_run_id,asset_version_id,research_version_id,qa_report_id),
 FOREIGN KEY(tenant_id,approver_id) REFERENCES tenant_memberships(tenant_id,principal_id)
);

CREATE TRIGGER immutable BEFORE UPDATE OR DELETE ON visual_config_versions
 FOR EACH ROW EXECUTE FUNCTION immutable_record();
CREATE TRIGGER immutable BEFORE UPDATE OR DELETE ON visual_approval_records
 FOR EACH ROW EXECUTE FUNCTION immutable_record();
-- Configuration creation and visual decisions serialize on the influencer, so a
-- newly committed policy cannot race past a latest-version check.
CREATE FUNCTION private.guard_visual_config_version() RETURNS trigger
 LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,public AS $$
 DECLARE expected int;
 BEGIN
 PERFORM 1 FROM public.influencers WHERE tenant_id=NEW.tenant_id AND id=NEW.influencer_id FOR UPDATE;
 SELECT coalesce(max(version),0)+1 INTO expected FROM public.visual_config_versions
 WHERE tenant_id=NEW.tenant_id AND influencer_id=NEW.influencer_id;
 IF NEW.version<>expected THEN RAISE EXCEPTION 'Visual configuration revision must be consecutive' USING ERRCODE='23514'; END IF;
 RETURN NEW;
 END $$;
CREATE TRIGGER visual_config_version_guard BEFORE INSERT ON visual_config_versions
 FOR EACH ROW EXECUTE FUNCTION private.guard_visual_config_version();
DO $$ DECLARE t text; BEGIN
 FOREACH t IN ARRAY ARRAY['visual_config_versions','render_runs','visual_approval_records'] LOOP
 EXECUTE format('ALTER TABLE %I ENABLE ROW LEVEL SECURITY',t);
 EXECUTE format('ALTER TABLE %I FORCE ROW LEVEL SECURITY',t);
 EXECUTE format('CREATE POLICY tenant_boundary ON %I USING (tenant_id=public.context_tenant()) WITH CHECK (tenant_id=public.context_tenant())',t);
 EXECUTE format('REVOKE ALL ON %I FROM PUBLIC,mediaos_runtime',t);
 EXECUTE format('GRANT SELECT ON %I TO mediaos_runtime',t);
 END LOOP;
 END $$;

CREATE FUNCTION private.audit_render(r public.render_runs, event text, before_status text, after_status text,
 extra jsonb DEFAULT '{}'::jsonb) RETURNS void
 LANGUAGE sql SECURITY DEFINER SET search_path=pg_catalog,public AS $$
 INSERT INTO public.audit_events(tenant_id,workflow_run_id,actor_id,event_type,correlation_id,details)
 VALUES(r.tenant_id,r.workflow_run_id,public.context_principal(),event,r.correlation_id,
 jsonb_build_object('render_run_id',r.id,'asset_version_id',r.asset_version_id,
   'visual_config_version_id',r.visual_config_version_id,'from_render_status',before_status,
   'to_render_status',after_status)||extra) $$;

-- Call after locking the workflow and render, in that order. qa_findings locks the
-- bound opportunity as well, serializing approval/export with source changes.
CREATE FUNCTION private.check_render_lineage(r public.render_runs, require_content_approval boolean)
 RETURNS uuid LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,public AS $$
 DECLARE w public.workflow_runs; cfg public.visual_config_versions; q public.qa_reports; approval_id uuid;
 BEGIN
 SELECT * INTO w FROM public.workflow_runs WHERE tenant_id=public.context_tenant() AND id=r.workflow_run_id;
 PERFORM 1 FROM public.influencers WHERE tenant_id=w.tenant_id AND id=w.influencer_id FOR UPDATE;
 SELECT * INTO cfg FROM public.visual_config_versions WHERE tenant_id=public.context_tenant() AND id=r.visual_config_version_id;
 SELECT * INTO q FROM public.qa_reports WHERE tenant_id=public.context_tenant() AND id=r.qa_report_id;
 IF r.tenant_id IS DISTINCT FROM public.context_tenant() OR w.id IS NULL OR cfg.id IS NULL
 OR cfg.influencer_id IS DISTINCT FROM w.influencer_id
 OR EXISTS(SELECT 1 FROM public.visual_config_versions newer WHERE newer.tenant_id=cfg.tenant_id
   AND newer.influencer_id=cfg.influencer_id AND newer.version>cfg.version)
 OR w.state NOT IN ('AWAITING_APPROVAL','APPROVED') OR q.status IS DISTINCT FROM 'PASS'
 OR w.asset_version_id IS DISTINCT FROM r.asset_version_id
 OR w.research_version_id IS DISTINCT FROM r.research_version_id
 OR w.qa_report_id IS DISTINCT FROM r.qa_report_id
 OR EXISTS(SELECT 1 FROM public.qa_reports WHERE tenant_id=w.tenant_id AND asset_version_id=r.asset_version_id AND status='BLOCKED')
 OR jsonb_array_length(public.qa_findings(w.id))<>0 THEN
 RAISE EXCEPTION 'Render requires exact current passing content and fresh evidence' USING ERRCODE='23514'; END IF;
 IF require_content_approval THEN
 SELECT id INTO approval_id FROM public.approval_records WHERE tenant_id=w.tenant_id
 AND workflow_run_id=w.id AND asset_version_id=r.asset_version_id AND research_version_id=r.research_version_id
 AND qa_report_id=r.qa_report_id AND decision='APPROVE';
 IF w.state<>'APPROVED' OR approval_id IS NULL THEN
 RAISE EXCEPTION 'Exact immutable content approval required' USING ERRCODE='23514'; END IF;
 END IF;
 RETURN approval_id;
 END $$;

CREATE FUNCTION public.start_render(rid uuid, aid uuid, configid uuid, key text, inputhash text)
 RETURNS uuid LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,public AS $$
 DECLARE w public.workflow_runs; r public.render_runs; cfg public.visual_config_versions;
 BEGIN
 IF NOT public.has_role('OPERATOR') THEN RAISE EXCEPTION 'Operator required' USING ERRCODE='42501'; END IF;
 IF rid IS NULL OR aid IS NULL OR configid IS NULL OR key IS NULL OR length(key) NOT BETWEEN 1 AND 128
 OR inputhash IS NULL OR inputhash !~ '^[0-9a-f]{64}$' THEN
 RAISE EXCEPTION 'Valid render idempotency key and hash required' USING ERRCODE='23514'; END IF;
 SELECT * INTO w FROM public.workflow_runs WHERE tenant_id=public.context_tenant() AND id=rid FOR UPDATE;
 IF w.id IS NULL THEN RAISE EXCEPTION 'Workflow not found' USING ERRCODE='P0002'; END IF;
 SELECT * INTO r FROM public.render_runs WHERE tenant_id=w.tenant_id AND idempotency_key=key;
 IF r.id IS NOT NULL THEN
 IF r.input_hash<>inputhash OR r.workflow_run_id<>rid OR r.asset_version_id<>aid OR r.visual_config_version_id<>configid THEN
 RAISE EXCEPTION 'Render idempotency payload conflict' USING ERRCODE='23505'; END IF;
 RETURN r.id;
 END IF;
 SELECT * INTO cfg FROM public.visual_config_versions WHERE tenant_id=w.tenant_id AND id=configid;
 IF aid IS NULL OR aid IS DISTINCT FROM w.asset_version_id OR cfg.id IS NULL OR cfg.influencer_id<>w.influencer_id
 OR w.state NOT IN ('AWAITING_APPROVAL','APPROVED') OR w.research_version_id IS NULL OR w.qa_report_id IS NULL THEN
 RAISE EXCEPTION 'Current renderable content and owned visual configuration required' USING ERRCODE='23514'; END IF;
 r.tenant_id:=w.tenant_id; r.workflow_run_id:=w.id; r.asset_version_id:=aid;
 r.research_version_id:=w.research_version_id; r.qa_report_id:=w.qa_report_id; r.visual_config_version_id:=configid;
 PERFORM private.check_render_lineage(r,false);
 INSERT INTO public.render_runs(tenant_id,workflow_run_id,asset_version_id,research_version_id,qa_report_id,
 visual_config_version_id,created_by,correlation_id,idempotency_key,input_hash)
 VALUES(w.tenant_id,w.id,aid,w.research_version_id,w.qa_report_id,configid,public.context_principal(),w.correlation_id,key,inputhash)
 ON CONFLICT(tenant_id,idempotency_key) DO NOTHING RETURNING * INTO r;
 IF r.id IS NULL THEN
 SELECT * INTO r FROM public.render_runs WHERE tenant_id=w.tenant_id AND idempotency_key=key;
 IF r.input_hash<>inputhash OR r.workflow_run_id<>rid OR r.asset_version_id<>aid OR r.visual_config_version_id<>configid THEN
 RAISE EXCEPTION 'Render idempotency payload conflict' USING ERRCODE='23505'; END IF;
 ELSE PERFORM private.audit_render(r,'RENDER_CREATED',NULL,'CREATED');
 END IF;
 RETURN r.id;
 END $$;

CREATE FUNCTION public.claim_render(renderid uuid) RETURNS void
 LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,public AS $$
 DECLARE r public.render_runs; rid uuid; old_status text;
 BEGIN
 IF NOT public.has_role('OPERATOR') THEN RAISE EXCEPTION 'Operator required' USING ERRCODE='42501'; END IF;
 SELECT workflow_run_id INTO rid FROM public.render_runs WHERE tenant_id=public.context_tenant() AND id=renderid;
 IF rid IS NULL THEN RAISE EXCEPTION 'Render not found' USING ERRCODE='P0002'; END IF;
 PERFORM 1 FROM public.workflow_runs WHERE tenant_id=public.context_tenant() AND id=rid FOR UPDATE;
 SELECT * INTO r FROM public.render_runs WHERE tenant_id=public.context_tenant() AND id=renderid FOR UPDATE;
 IF r.status NOT IN ('CREATED','RENDERING') THEN RAISE EXCEPTION 'Render is already terminal' USING ERRCODE='23514'; END IF;
 PERFORM private.check_render_lineage(r,false);
 old_status:=r.status;
 UPDATE public.render_runs SET status='RENDERING',started_at=coalesce(started_at,clock_timestamp()) WHERE id=r.id;
 PERFORM private.audit_render(r,CASE WHEN old_status='RENDERING' THEN 'RENDER_RESUMED' ELSE 'RENDER_STARTED' END,old_status,'RENDERING');
 END $$;

-- The supplied coverage text must correspond to the immutable source draft. For
-- PASS, every code point must also be accounted for by ordered exact line spans.
CREATE FUNCTION private.check_text_coverage(c jsonb, expected_path text, expected_text text,
 expected_facts jsonb, placement text, font_size int, bounds jsonb, passing boolean)
 RETURNS void LANGUAGE plpgsql SET search_path=pg_catalog,public AS $$
 DECLARE line jsonb; pos int:=0; end_pos int; starts int;
 BEGIN
 IF jsonb_typeof(c) IS DISTINCT FROM 'object'
 OR NOT(c ?& ARRAY['field_path','text','text_sha256','fact_ids','placement','lines','exact_coverage','font_size','bounds'])
 OR c-ARRAY['field_path','text','text_sha256','fact_ids','placement','lines','exact_coverage','font_size','bounds']<>'{}'::jsonb
 OR c->>'field_path' IS DISTINCT FROM expected_path OR c->>'text' IS DISTINCT FROM expected_text
 OR c->'fact_ids' IS DISTINCT FROM expected_facts
 OR c->>'text_sha256' IS DISTINCT FROM encode(public.digest(expected_text,'sha256'),'hex')
 OR c->>'placement' IS DISTINCT FROM placement
 OR jsonb_typeof(c->'lines') IS DISTINCT FROM 'array'
 OR jsonb_typeof(c->'exact_coverage') IS DISTINCT FROM 'boolean'
 OR c->'font_size' IS DISTINCT FROM coalesce(to_jsonb(font_size),'null'::jsonb)
 OR c->'bounds' IS DISTINCT FROM coalesce(bounds,'null'::jsonb) THEN
 RAISE EXCEPTION 'Render text coverage differs from immutable content' USING ERRCODE='23514'; END IF;
 IF placement='CAPTION' THEN
 IF c->'lines'<>'[]'::jsonb OR c->'exact_coverage'<>'true'::jsonb THEN
 RAISE EXCEPTION 'Caption coverage must retain exact publication metadata' USING ERRCODE='23514'; END IF;
 RETURN;
 END IF;
 IF font_size IS NULL OR font_size<26 OR jsonb_array_length(bounds)<>4 THEN
 RAISE EXCEPTION 'Safe pinned image typography required' USING ERRCODE='23514'; END IF;
 IF passing AND c->'exact_coverage'<>'true'::jsonb THEN
 RAISE EXCEPTION 'Passing render must cover all text' USING ERRCODE='23514'; END IF;
 IF passing THEN
 IF jsonb_array_length(c->'lines')*(font_size+4)>(bounds->>3)::int-(bounds->>1)::int THEN
 RAISE EXCEPTION 'Rendered text exceeds its fixed vertical region' USING ERRCODE='23514'; END IF;
 FOR line IN SELECT value FROM jsonb_array_elements(c->'lines') LOOP
 IF jsonb_typeof(line) IS DISTINCT FROM 'object'
 OR NOT(line ?& ARRAY['start','end','text','hard_break'])
 OR line-ARRAY['start','end','text','hard_break']<>'{}'::jsonb
 OR jsonb_typeof(line->'start') IS DISTINCT FROM 'number'
 OR jsonb_typeof(line->'end') IS DISTINCT FROM 'number'
 OR jsonb_typeof(line->'text') IS DISTINCT FROM 'string'
 OR line->>'start' !~ '^[0-9]+$' OR line->>'end' !~ '^[0-9]+$'
 OR jsonb_typeof(line->'hard_break') IS DISTINCT FROM 'boolean' THEN
 RAISE EXCEPTION 'Invalid rendered line span' USING ERRCODE='23514'; END IF;
 starts:=(line->>'start')::int; end_pos:=(line->>'end')::int;
 IF starts<>pos OR end_pos<starts OR end_pos>length(expected_text)
 OR position(E'\n' IN line->>'text')>0
 OR ((line->>'text')||(CASE WHEN line->'hard_break'='true'::jsonb THEN E'\n' ELSE '' END))
 IS DISTINCT FROM substring(expected_text FROM starts+1 FOR end_pos-starts) THEN
 RAISE EXCEPTION 'Rendered lines omit or replace source text' USING ERRCODE='23514'; END IF;
 -- Renderer spans include the consumed newline; line.text excludes that glyph.
 pos:=end_pos;
 END LOOP;
 IF pos<>length(expected_text) THEN RAISE EXCEPTION 'Rendered lines truncate text' USING ERRCODE='23514'; END IF;
 END IF;
 END $$;

CREATE FUNCTION private.validate_render_manifest(r public.render_runs, m jsonb, supplied_hash text)
 RETURNS text LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,public AS $$
 DECLARE a public.content_asset_versions; cfg public.visual_config_versions; w public.workflow_runs;
 outcome text; slide jsonb; original jsonb; coverage jsonb; finding jsonb; i int:=0; margin int; right_edge int;
 visual_payload jsonb; expected_keys text[]:=ARRAY['schema_version','renderer_version','pillow_version','status',
 'draft_sha256','visual_config_sha256','font_sha256','reference_sha256','influencer_version_id','language',
 'caption','caption_coverage','slides','findings'];
 BEGIN
 SELECT * INTO a FROM public.content_asset_versions WHERE tenant_id=r.tenant_id AND id=r.asset_version_id;
 SELECT * INTO cfg FROM public.visual_config_versions WHERE tenant_id=r.tenant_id AND id=r.visual_config_version_id;
 SELECT * INTO w FROM public.workflow_runs WHERE tenant_id=r.tenant_id AND id=r.workflow_run_id;
 IF m IS NULL OR jsonb_typeof(m) IS DISTINCT FROM 'object' OR octet_length(m::text)>2000000
 OR NOT(m ?& expected_keys) OR m-expected_keys<>'{}'::jsonb
 OR supplied_hash IS NULL OR supplied_hash IS DISTINCT FROM private.visual_hash(m)
 OR m->>'schema_version' IS DISTINCT FROM '1' OR m->>'renderer_version' IS DISTINCT FROM 'pillow-editorial-v1'
 OR coalesce(length(m->>'pillow_version'),0) NOT BETWEEN 1 AND 40
 OR m->>'draft_sha256' IS DISTINCT FROM private.visual_hash(a.payload)
 OR m->>'influencer_version_id' IS DISTINCT FROM w.influencer_version_id::text
 OR m->>'language' IS DISTINCT FROM a.payload->>'language'
 OR m->'caption' IS DISTINCT FROM a.payload->'caption'
 OR m->'font_sha256' IS DISTINCT FROM cfg.font_hashes
 OR m->'reference_sha256' IS DISTINCT FROM coalesce(to_jsonb(cfg.reference_sha256),'null'::jsonb)
 OR jsonb_typeof(m->'slides') IS DISTINCT FROM 'array'
 OR jsonb_typeof(m->'findings') IS DISTINCT FROM 'array' THEN
 RAISE EXCEPTION 'Invalid render manifest or pinned input hashes' USING ERRCODE='23514'; END IF;
 visual_payload:=jsonb_set(jsonb_set(cfg.payload,'{regular_font_path}',cfg.font_hashes->'regular'),'{bold_font_path}',cfg.font_hashes->'bold');
 IF m->>'visual_config_sha256' IS DISTINCT FROM private.visual_hash(visual_payload)
 OR jsonb_array_length(m->'slides')<>jsonb_array_length(a.payload->'slides') THEN
 RAISE EXCEPTION 'Render configuration or slide count mismatch' USING ERRCODE='23514'; END IF;
 outcome:='PASS';
 FOR finding IN SELECT value FROM jsonb_array_elements(m->'findings') LOOP
 IF jsonb_typeof(finding) IS DISTINCT FROM 'object'
 OR jsonb_typeof(finding->'severity') IS DISTINCT FROM 'string'
 OR finding->>'severity' NOT IN ('BLOCKED','REVISION_REQUIRED')
 OR NOT(finding ?& ARRAY['code','severity','field_path','message','slide_index'])
 OR finding-ARRAY['code','severity','field_path','message','slide_index']<>'{}'::jsonb
 OR coalesce(length(finding->>'code'),0)=0 OR coalesce(length(finding->>'message'),0)=0 THEN
 RAISE EXCEPTION 'Invalid visual QA finding' USING ERRCODE='23514'; END IF;
 IF finding->>'severity'='BLOCKED' THEN outcome:='BLOCKED';
 ELSIF outcome='PASS' THEN outcome:='REVISION_REQUIRED'; END IF;
 END LOOP;
 IF m->>'status' IS DISTINCT FROM outcome THEN
 RAISE EXCEPTION 'Visual QA result must match findings' USING ERRCODE='23514'; END IF;
 -- Preserve deterministic rejection diagnostics instead of turning an expected
 -- disclosure-policy BLOCK into an execution failure. Coverage below still has
 -- to retain the exact draft disclosure, and only PASS can be approved/exported.
 IF cfg.payload->>'required_disclosure' IS DISTINCT FROM a.payload->>'disclosure'
 AND (outcome<>'BLOCKED' OR NOT EXISTS(
   SELECT 1 FROM jsonb_array_elements(m->'findings') AS f
   WHERE f->>'code'='DISCLOSURE_MISMATCH' AND f->>'severity'='BLOCKED'
     AND f->>'field_path'='disclosure')) THEN
 RAISE EXCEPTION 'Disclosure mismatch must produce an explicit visual QA block' USING ERRCODE='23514'; END IF;
 PERFORM private.check_text_coverage(m->'caption_coverage','caption',a.payload->'caption'->>'text',
 a.payload->'caption'->'fact_ids','CAPTION',NULL,NULL,true);
 margin:=(cfg.payload->>'margin')::int; right_edge:=1080-margin;
 FOR slide IN SELECT value FROM jsonb_array_elements(m->'slides') LOOP
 i:=i+1; original:=a.payload->'slides'->(i-1);
 IF jsonb_typeof(slide) IS DISTINCT FROM 'object'
 OR NOT(slide ?& ARRAY['index','filename','sha256','width','height','text_coverage','overflow'])
 OR slide-ARRAY['index','filename','sha256','width','height','text_coverage','overflow']<>'{}'::jsonb
 OR slide->'index' IS DISTINCT FROM to_jsonb(i) OR slide->'width' IS DISTINCT FROM '1080'::jsonb
 OR slide->'height' IS DISTINCT FROM '1350'::jsonb
 OR jsonb_typeof(slide->'text_coverage') IS DISTINCT FROM 'array'
 OR jsonb_array_length(slide->'text_coverage')<>5
 OR jsonb_typeof(slide->'overflow') IS DISTINCT FROM 'boolean' THEN
 RAISE EXCEPTION 'Invalid rendered slide structure' USING ERRCODE='23514'; END IF;
 IF outcome='PASS' THEN
 IF slide->'overflow'<>'false'::jsonb OR slide->>'filename' IS DISTINCT FROM ('slide-'||lpad(i::text,2,'0')||'.png')
 OR coalesce(slide->>'sha256','') !~ '^[0-9a-f]{64}$' THEN
 RAISE EXCEPTION 'Passing slide must have an immutable numbered PNG without overflow' USING ERRCODE='23514'; END IF;
 ELSE
 IF slide->'filename'<>'null'::jsonb OR slide->'sha256'<>'null'::jsonb THEN
 RAISE EXCEPTION 'Failed visual QA cannot carry publishable PNGs' USING ERRCODE='23514'; END IF;
 END IF;
 coverage:=slide->'text_coverage';
 PERFORM private.check_text_coverage(coverage->0,'display_name',cfg.payload->>'display_name','[]'::jsonb,
 'IMAGE',(cfg.payload->>'identity_font_size')::int,jsonb_build_array(margin,72,right_edge-176,168),outcome='PASS');
 PERFORM private.check_text_coverage(coverage->1,'slides['||(i-1)::text||'].headline',original->'headline'->>'text',original->'headline'->'fact_ids',
 'IMAGE',(cfg.payload->>'headline_font_size')::int,jsonb_build_array(margin,238,right_edge,426),outcome='PASS');
 PERFORM private.check_text_coverage(coverage->2,'slides['||(i-1)::text||'].body',original->'body'->>'text',original->'body'->'fact_ids',
 'IMAGE',(cfg.payload->>'body_font_size')::int,jsonb_build_array(margin,458,right_edge,1000),outcome='PASS');
 PERFORM private.check_text_coverage(coverage->3,'cta',a.payload->'cta'->>'text',a.payload->'cta'->'fact_ids',
 'IMAGE',(cfg.payload->>'cta_font_size')::int,jsonb_build_array(margin,1042,right_edge,1142),outcome='PASS');
 PERFORM private.check_text_coverage(coverage->4,'disclosure',a.payload->>'disclosure','[]'::jsonb,
 'IMAGE',(cfg.payload->>'disclosure_font_size')::int,jsonb_build_array(margin,1198,right_edge,1296),outcome='PASS');
 END LOOP;
 RETURN outcome;
 END $$;

CREATE FUNCTION public.complete_render(renderid uuid, manifest jsonb, manifesthash text) RETURNS void
 LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,public AS $$
 DECLARE r public.render_runs; rid uuid; outcome text;
 BEGIN
 IF NOT public.has_role('OPERATOR') THEN RAISE EXCEPTION 'Operator required' USING ERRCODE='42501'; END IF;
 SELECT workflow_run_id INTO rid FROM public.render_runs WHERE tenant_id=public.context_tenant() AND id=renderid;
 IF rid IS NULL THEN RAISE EXCEPTION 'Render not found' USING ERRCODE='P0002'; END IF;
 PERFORM 1 FROM public.workflow_runs WHERE tenant_id=public.context_tenant() AND id=rid FOR UPDATE;
 SELECT * INTO r FROM public.render_runs WHERE tenant_id=public.context_tenant() AND id=renderid FOR UPDATE;
 IF r.status<>'RENDERING' THEN RAISE EXCEPTION 'Render must be actively rendering' USING ERRCODE='23514'; END IF;
 PERFORM private.check_render_lineage(r,false);
 outcome:=private.validate_render_manifest(r,manifest,manifesthash);
 UPDATE public.render_runs SET manifest=complete_render.manifest,manifest_hash=manifesthash,status=outcome,ended_at=clock_timestamp()
 WHERE id=r.id;
 PERFORM private.audit_render(r,'RENDER_COMPLETED','RENDERING',outcome,jsonb_build_object('manifest_hash',manifesthash));
 END $$;

CREATE FUNCTION public.fail_render(renderid uuid, category text) RETURNS void
 LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,public AS $$
 DECLARE r public.render_runs; rid uuid;
 BEGIN
 IF NOT public.has_role('OPERATOR') THEN RAISE EXCEPTION 'Operator required' USING ERRCODE='42501'; END IF;
 IF category IS NULL OR category !~ '^[A-Z][A-Z0-9_]{0,79}$' THEN
 RAISE EXCEPTION 'Bounded failure category required' USING ERRCODE='23514'; END IF;
 SELECT workflow_run_id INTO rid FROM public.render_runs WHERE tenant_id=public.context_tenant() AND id=renderid;
 IF rid IS NULL THEN RAISE EXCEPTION 'Render not found' USING ERRCODE='P0002'; END IF;
 PERFORM 1 FROM public.workflow_runs WHERE tenant_id=public.context_tenant() AND id=rid FOR UPDATE;
 SELECT * INTO r FROM public.render_runs WHERE tenant_id=public.context_tenant() AND id=renderid FOR UPDATE;
 IF r.status NOT IN ('CREATED','RENDERING') THEN RAISE EXCEPTION 'Render is already terminal' USING ERRCODE='23514'; END IF;
 UPDATE public.render_runs SET status='FAILED',error_category=category,ended_at=clock_timestamp() WHERE id=r.id;
 PERFORM private.audit_render(r,'RENDER_FAILED',r.status,'FAILED',jsonb_build_object('error_category',category));
 END $$;

CREATE FUNCTION public.decide_render(renderid uuid, manifesthash text, decision text, comment text DEFAULT NULL)
 RETURNS uuid LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,public AS $$
 DECLARE r public.render_runs; rid uuid; content_approval uuid; result uuid;
 BEGIN
 IF NOT public.has_role('APPROVER') THEN RAISE EXCEPTION 'Approver required' USING ERRCODE='42501'; END IF;
 IF decision IS NULL OR decision NOT IN ('APPROVE','REJECT') OR length(comment)>2000 THEN
 RAISE EXCEPTION 'Invalid visual approval decision' USING ERRCODE='23514'; END IF;
 SELECT workflow_run_id INTO rid FROM public.render_runs WHERE tenant_id=public.context_tenant() AND id=renderid;
 IF rid IS NULL THEN RAISE EXCEPTION 'Render not found' USING ERRCODE='P0002'; END IF;
 PERFORM 1 FROM public.workflow_runs WHERE tenant_id=public.context_tenant() AND id=rid FOR UPDATE;
 SELECT * INTO r FROM public.render_runs WHERE tenant_id=public.context_tenant() AND id=renderid FOR UPDATE;
 IF r.status<>'PASS' OR r.manifest_hash IS DISTINCT FROM manifesthash
 OR EXISTS(SELECT 1 FROM public.render_runs WHERE tenant_id=r.tenant_id AND workflow_run_id=rid AND sequence>r.sequence)
 OR EXISTS(SELECT 1 FROM public.visual_approval_records WHERE tenant_id=r.tenant_id AND render_run_id=r.id) THEN
 RAISE EXCEPTION 'Visual approval requires the latest undecided passing render and exact hash' USING ERRCODE='23514'; END IF;
 content_approval:=private.check_render_lineage(r,true);
 IF private.validate_render_manifest(r,r.manifest,r.manifest_hash)<>'PASS' THEN
 RAISE EXCEPTION 'Visual manifest is not passing' USING ERRCODE='23514'; END IF;
 INSERT INTO public.visual_approval_records(tenant_id,render_run_id,workflow_run_id,asset_version_id,research_version_id,
 qa_report_id,content_approval_record_id,manifest_hash,decision,approver_id,comment)
 VALUES(r.tenant_id,r.id,r.workflow_run_id,r.asset_version_id,r.research_version_id,r.qa_report_id,
 content_approval,r.manifest_hash,decision,public.context_principal(),comment) RETURNING id INTO result;
 PERFORM private.audit_render(r,'VISUAL_'||CASE WHEN decision='APPROVE' THEN 'APPROVED' ELSE 'REJECTED' END,
 r.status,r.status,jsonb_build_object('visual_approval_record_id',result,'content_approval_record_id',content_approval,
 'manifest_hash',r.manifest_hash,'decision',decision));
 RETURN result;
 END $$;

CREATE FUNCTION public.check_render_export(renderid uuid) RETURNS void
 LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,public AS $$
 DECLARE r public.render_runs; rid uuid; content_approval uuid;
 BEGIN
 IF public.context_principal() IS NULL THEN RAISE EXCEPTION 'Authentication required' USING ERRCODE='42501'; END IF;
 SELECT workflow_run_id INTO rid FROM public.render_runs WHERE tenant_id=public.context_tenant() AND id=renderid;
 IF rid IS NULL THEN RAISE EXCEPTION 'Render not found' USING ERRCODE='P0002'; END IF;
 PERFORM 1 FROM public.workflow_runs WHERE tenant_id=public.context_tenant() AND id=rid FOR UPDATE;
 SELECT * INTO r FROM public.render_runs WHERE tenant_id=public.context_tenant() AND id=renderid FOR UPDATE;
 IF r.status<>'PASS' OR EXISTS(SELECT 1 FROM public.render_runs WHERE tenant_id=r.tenant_id AND workflow_run_id=rid AND sequence>r.sequence) THEN
 RAISE EXCEPTION 'Only the latest passing render can be exported' USING ERRCODE='23514'; END IF;
 content_approval:=private.check_render_lineage(r,true);
 IF private.validate_render_manifest(r,r.manifest,r.manifest_hash)<>'PASS'
 OR NOT EXISTS(SELECT 1 FROM public.visual_approval_records WHERE tenant_id=r.tenant_id AND render_run_id=r.id
 AND manifest_hash=r.manifest_hash AND decision='APPROVE' AND content_approval_record_id=content_approval) THEN
 RAISE EXCEPTION 'Export requires exact current visual and content approvals' USING ERRCODE='23514'; END IF;
 PERFORM private.audit_render(r,'RENDER_EXPORT_AUTHORIZED',r.status,r.status,jsonb_build_object('manifest_hash',r.manifest_hash));
 END $$;

REVOKE ALL ON FUNCTION private.visual_canonical(jsonb),private.visual_hash(jsonb),
 private.guard_visual_config_version(),
 private.audit_render(public.render_runs,text,text,text,jsonb),private.check_render_lineage(public.render_runs,boolean),
 private.check_text_coverage(jsonb,text,text,jsonb,text,int,jsonb,boolean),
 private.validate_render_manifest(public.render_runs,jsonb,text) FROM PUBLIC,mediaos_runtime;
REVOKE ALL ON FUNCTION public.start_render(uuid,uuid,uuid,text,text),public.claim_render(uuid),
 public.complete_render(uuid,jsonb,text),public.fail_render(uuid,text),
 public.decide_render(uuid,text,text,text),public.check_render_export(uuid) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION public.start_render(uuid,uuid,uuid,text,text),public.claim_render(uuid),
 public.complete_render(uuid,jsonb,text),public.fail_render(uuid,text),
 public.decide_render(uuid,text,text,text),public.check_render_export(uuid) TO mediaos_runtime;
