-- Versioned social layouts preserve all existing manifest and approval checks.
CREATE OR REPLACE FUNCTION private.validate_render_manifest(r public.render_runs, m jsonb, supplied_hash text)
 RETURNS text LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,public AS $$
 DECLARE a public.content_asset_versions; cfg public.visual_config_versions; w public.workflow_runs;
 outcome text; slide jsonb; original jsonb; coverage jsonb; finding jsonb; i int:=0; margin int; right_edge int;
 visual_payload jsonb; template text; regions jsonb; expected_keys text[]:=ARRAY['schema_version','renderer_version','pillow_version','status',
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
 template:=cfg.payload->>'template_version';
 IF template IS NULL OR template NOT IN ('editorial-v1','social-editorial-v2') THEN
 RAISE EXCEPTION 'Unknown visual template version' USING ERRCODE='23514'; END IF;
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
 IF template='editorial-v1' THEN
 regions:=jsonb_build_array(
   jsonb_build_array(margin,72,right_edge-176,168),
   jsonb_build_array(margin,238,right_edge,426),
   jsonb_build_array(margin,458,right_edge,1000),
   jsonb_build_array(margin,1042,right_edge,1142),
   jsonb_build_array(margin,1198,right_edge,1296));
 ELSE
 -- These exact regions mirror social_template.social_regions. No caller can
 -- move verified text outside the visible canvas or beneath another field.
 regions:=jsonb_build_array(
   jsonb_build_array(margin,76,right_edge-176,152),
   CASE WHEN i=1 THEN jsonb_build_array(margin,208,688,524)
     ELSE jsonb_build_array(margin,228,right_edge,432) END,
   CASE WHEN i=1 THEN jsonb_build_array(margin,584,right_edge,1008)
     WHEN i=jsonb_array_length(m->'slides') THEN jsonb_build_array(margin+32,494,right_edge-32,982)
     ELSE jsonb_build_array(margin+32,494,right_edge-32,1016) END,
   jsonb_build_array(margin+CASE WHEN i>1 AND i=jsonb_array_length(m->'slides') THEN 32 ELSE 0 END,
     1070,right_edge-88,1174),
   jsonb_build_array(margin,1222,right_edge,1304));
 END IF;
 PERFORM private.check_text_coverage(coverage->0,'display_name',cfg.payload->>'display_name','[]'::jsonb,
 'IMAGE',(cfg.payload->>'identity_font_size')::int,regions->0,outcome='PASS');
 PERFORM private.check_text_coverage(coverage->1,'slides['||(i-1)::text||'].headline',original->'headline'->>'text',original->'headline'->'fact_ids',
 'IMAGE',(cfg.payload->>'headline_font_size')::int,regions->1,outcome='PASS');
 PERFORM private.check_text_coverage(coverage->2,'slides['||(i-1)::text||'].body',original->'body'->>'text',original->'body'->'fact_ids',
 'IMAGE',(cfg.payload->>'body_font_size')::int,regions->2,outcome='PASS');
 PERFORM private.check_text_coverage(coverage->3,'cta',a.payload->'cta'->>'text',a.payload->'cta'->'fact_ids',
 'IMAGE',(cfg.payload->>'cta_font_size')::int,regions->3,outcome='PASS');
 PERFORM private.check_text_coverage(coverage->4,'disclosure',a.payload->>'disclosure','[]'::jsonb,
 'IMAGE',(cfg.payload->>'disclosure_font_size')::int,regions->4,outcome='PASS');
 END LOOP;
 RETURN outcome;
 END $$;
