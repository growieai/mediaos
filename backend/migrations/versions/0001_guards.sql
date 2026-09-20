
ALTER TABLE audit_events ADD COLUMN sequence bigint GENERATED ALWAYS AS IDENTITY;
ALTER TABLE facts ADD CONSTRAINT grant_classification CHECK(fact_type='GRANT' OR structured_data IS NULL OR structured_data='null'::jsonb);

CREATE FUNCTION public.audit_workflow() RETURNS trigger LANGUAGE plpgsql SECURITY DEFINER
 SET search_path=pg_catalog,public AS $$
 BEGIN
 INSERT INTO public.audit_events(tenant_id,workflow_run_id,actor_id,event_type,from_state,to_state,correlation_id)
 VALUES(NEW.tenant_id,NEW.id,public.context_principal(),'STATE_TRANSITION',
 CASE WHEN TG_OP='INSERT' THEN NULL ELSE OLD.state END,NEW.state,NEW.correlation_id);
 RETURN NEW;
 END $$;
CREATE TRIGGER audit_workflow AFTER INSERT OR UPDATE OF state ON workflow_runs FOR EACH ROW EXECUTE FUNCTION audit_workflow();

CREATE FUNCTION public.guard_new_run() RETURNS trigger LANGUAGE plpgsql AS $$
 BEGIN
 IF NOT public.has_role('OPERATOR') OR NEW.tenant_id IS DISTINCT FROM public.context_tenant()
 OR NEW.created_by IS DISTINCT FROM public.context_principal() OR NEW.state <> 'CREATED'
 OR NEW.source_snapshot_id IS NOT NULL OR NEW.research_version_id IS NOT NULL OR NEW.brief_id IS NOT NULL
 OR NEW.asset_version_id IS NOT NULL OR NEW.qa_report_id IS NOT NULL THEN
 RAISE EXCEPTION 'Invalid workflow creation' USING ERRCODE='42501'; END IF;
 RETURN NEW; END $$;
CREATE TRIGGER guard_new_run BEFORE INSERT ON workflow_runs FOR EACH ROW EXECUTE FUNCTION guard_new_run();

CREATE FUNCTION public.immutable_record() RETURNS trigger LANGUAGE plpgsql AS $$
 BEGIN RAISE EXCEPTION 'Immutable record: create a new revision' USING ERRCODE='23514'; END $$;
CREATE TRIGGER immutable BEFORE UPDATE OR DELETE ON influencer_versions FOR EACH ROW EXECUTE FUNCTION immutable_record();
CREATE TRIGGER immutable BEFORE UPDATE OR DELETE ON character_config_versions FOR EACH ROW EXECUTE FUNCTION immutable_record();
CREATE TRIGGER immutable BEFORE UPDATE OR DELETE ON research_packs FOR EACH ROW EXECUTE FUNCTION immutable_record();
CREATE TRIGGER immutable BEFORE UPDATE OR DELETE ON research_pack_versions FOR EACH ROW EXECUTE FUNCTION immutable_record();
CREATE TRIGGER immutable BEFORE UPDATE OR DELETE ON research_pack_sources FOR EACH ROW EXECUTE FUNCTION immutable_record();
CREATE TRIGGER immutable BEFORE UPDATE OR DELETE ON facts FOR EACH ROW EXECUTE FUNCTION immutable_record();
CREATE TRIGGER immutable BEFORE UPDATE OR DELETE ON research_pack_facts FOR EACH ROW EXECUTE FUNCTION immutable_record();
CREATE TRIGGER immutable BEFORE UPDATE OR DELETE ON content_briefs FOR EACH ROW EXECUTE FUNCTION immutable_record();
CREATE TRIGGER immutable BEFORE UPDATE OR DELETE ON brief_facts FOR EACH ROW EXECUTE FUNCTION immutable_record();
CREATE TRIGGER immutable BEFORE UPDATE OR DELETE ON content_assets FOR EACH ROW EXECUTE FUNCTION immutable_record();
CREATE TRIGGER immutable BEFORE UPDATE OR DELETE ON content_asset_versions FOR EACH ROW EXECUTE FUNCTION immutable_record();
CREATE TRIGGER immutable BEFORE UPDATE OR DELETE ON content_claims FOR EACH ROW EXECUTE FUNCTION immutable_record();
CREATE TRIGGER immutable BEFORE UPDATE OR DELETE ON qa_reports FOR EACH ROW EXECUTE FUNCTION immutable_record();
CREATE TRIGGER immutable BEFORE UPDATE OR DELETE ON approval_records FOR EACH ROW EXECUTE FUNCTION immutable_record();
CREATE TRIGGER immutable BEFORE UPDATE OR DELETE ON audit_events FOR EACH ROW EXECUTE FUNCTION immutable_record();
CREATE TRIGGER immutable BEFORE UPDATE OR DELETE ON cost_events FOR EACH ROW EXECUTE FUNCTION immutable_record();

CREATE FUNCTION public.guard_artifact() RETURNS trigger LANGUAGE plpgsql SECURITY DEFINER
 SET search_path=pg_catalog,public AS $$
 DECLARE r public.workflow_runs; rid uuid; expected text; raw text;
 BEGIN
 IF NOT public.has_role('OPERATOR') OR NEW.tenant_id IS DISTINCT FROM public.context_tenant() THEN
 RAISE EXCEPTION 'Operator required' USING ERRCODE='42501'; END IF;
 IF TG_TABLE_NAME='facts' THEN
 SELECT workflow_run_id, raw_content INTO rid,raw FROM public.source_snapshots WHERE tenant_id=NEW.tenant_id AND id=NEW.source_snapshot_id;
 IF substring(raw FROM NEW.span_start+1 FOR NEW.span_end-NEW.span_start) IS NULL
 OR btrim(substring(raw FROM NEW.span_start+1 FOR NEW.span_end-NEW.span_start)) <> NEW.statement
 OR NEW.span_end>length(raw) THEN RAISE EXCEPTION 'Evidence span mismatch' USING ERRCODE='23514'; END IF;
 expected:='RESEARCHING';
 ELSE
 rid:=NEW.workflow_run_id;
 expected:=CASE TG_TABLE_NAME WHEN 'source_snapshots' THEN 'CREATED' WHEN 'research_packs' THEN 'RESEARCHING'
 WHEN 'research_pack_versions' THEN 'RESEARCHING' WHEN 'content_briefs' THEN 'BRIEFING'
 ELSE 'CONTENT_GENERATING' END;
 END IF;
 SELECT * INTO r FROM public.workflow_runs WHERE tenant_id=NEW.tenant_id AND id=rid FOR UPDATE;
 IF r.id IS NULL OR r.state<>expected THEN RAISE EXCEPTION 'Artifact stage mismatch' USING ERRCODE='23514'; END IF;
 IF TG_TABLE_NAME='source_snapshots' THEN
 IF NEW.verification_status<>'UNVERIFIED' OR NEW.verified_by IS NOT NULL OR NEW.verified_at IS NOT NULL
 OR NEW.created_by IS DISTINCT FROM public.context_principal()
 OR NEW.checksum<>encode(public.digest(NEW.raw_content,'sha256'),'hex') THEN
 RAISE EXCEPTION 'Invalid source submission' USING ERRCODE='23514'; END IF;
 END IF;
 IF TG_TABLE_NAME='content_briefs' THEN
 IF NEW.influencer_version_id<>r.influencer_version_id OR NEW.mission_id<>r.mission_id
 OR NEW.research_version_id<>r.research_version_id THEN RAISE EXCEPTION 'Brief lineage mismatch' USING ERRCODE='23514'; END IF;
 END IF;
 RETURN NEW; END $$;
CREATE TRIGGER guard_artifact BEFORE INSERT ON source_snapshots FOR EACH ROW EXECUTE FUNCTION guard_artifact();
CREATE TRIGGER guard_artifact BEFORE INSERT ON facts FOR EACH ROW EXECUTE FUNCTION guard_artifact();
CREATE TRIGGER guard_artifact BEFORE INSERT ON research_packs FOR EACH ROW EXECUTE FUNCTION guard_artifact();
CREATE TRIGGER guard_artifact BEFORE INSERT ON research_pack_versions FOR EACH ROW EXECUTE FUNCTION guard_artifact();
CREATE TRIGGER guard_artifact BEFORE INSERT ON content_briefs FOR EACH ROW EXECUTE FUNCTION guard_artifact();
CREATE TRIGGER guard_artifact BEFORE INSERT ON content_assets FOR EACH ROW EXECUTE FUNCTION guard_artifact();
CREATE TRIGGER guard_artifact BEFORE INSERT ON content_asset_versions FOR EACH ROW EXECUTE FUNCTION guard_artifact();

CREATE FUNCTION public.checkpoint(rid uuid, target text, artifact uuid DEFAULT NULL) RETURNS void LANGUAGE plpgsql SECURITY DEFINER
 SET search_path=pg_catalog,public AS $$
 DECLARE r public.workflow_runs; ok boolean:=false;
 BEGIN
 IF NOT public.has_role('OPERATOR') THEN RAISE EXCEPTION 'Operator required' USING ERRCODE='42501'; END IF;
 SELECT * INTO r FROM public.workflow_runs WHERE tenant_id=public.context_tenant() AND id=rid FOR UPDATE;
 IF r.id IS NULL THEN RAISE EXCEPTION 'Run not found' USING ERRCODE='P0002'; END IF;
 ok:=(r.state||'>'||target)=ANY(ARRAY[
 'CREATED>SOURCE_CAPTURED','SOURCE_CAPTURED>RESEARCHING','RESEARCHING>RESEARCH_COMPLETE',
 'RESEARCH_COMPLETE>BRIEFING','BRIEFING>BRIEF_COMPLETE','BRIEF_COMPLETE>CONTENT_GENERATING',
 'CONTENT_GENERATING>CONTENT_COMPLETE','CONTENT_COMPLETE>QA_RUNNING']);
 IF target='FAILED' AND r.state=ANY(ARRAY['CREATED','SOURCE_CAPTURED','RESEARCHING','RESEARCH_COMPLETE','BRIEFING','BRIEF_COMPLETE','CONTENT_GENERATING','CONTENT_COMPLETE','QA_RUNNING']) THEN ok:=true; END IF;
 IF NOT ok THEN RAISE EXCEPTION 'Invalid transition % to %',r.state,target USING ERRCODE='23514'; END IF;
 IF target=ANY(ARRAY['SOURCE_CAPTURED','RESEARCH_COMPLETE','BRIEF_COMPLETE','CONTENT_COMPLETE']) AND artifact IS NULL THEN
 RAISE EXCEPTION 'Checkpoint requires artifact' USING ERRCODE='23514'; END IF;
 UPDATE public.workflow_runs SET state=target,updated_at=clock_timestamp(),
 source_snapshot_id=CASE WHEN target='SOURCE_CAPTURED' THEN artifact ELSE source_snapshot_id END,
 research_version_id=CASE WHEN target='RESEARCH_COMPLETE' THEN artifact ELSE research_version_id END,
 brief_id=CASE WHEN target='BRIEF_COMPLETE' THEN artifact ELSE brief_id END,
 asset_version_id=CASE WHEN target='CONTENT_COMPLETE' THEN artifact ELSE asset_version_id END WHERE id=rid;
 END $$;

CREATE FUNCTION public.verify_source(sid uuid, decision text, comment text) RETURNS void LANGUAGE plpgsql SECURITY DEFINER
 SET search_path=pg_catalog,public AS $$
 DECLARE s public.source_snapshots; r public.workflow_runs;
 BEGIN
 IF NOT public.has_role('APPROVER') THEN RAISE EXCEPTION 'Approver required' USING ERRCODE='42501'; END IF;
 SELECT * INTO s FROM public.source_snapshots WHERE tenant_id=public.context_tenant() AND id=sid;
 SELECT * INTO r FROM public.workflow_runs WHERE tenant_id=public.context_tenant() AND id=s.workflow_run_id FOR UPDATE;
 SELECT * INTO s FROM public.source_snapshots WHERE tenant_id=public.context_tenant() AND id=sid FOR UPDATE;
 IF s.id IS NULL THEN RAISE EXCEPTION 'Source not found' USING ERRCODE='P0002'; END IF;
 IF r.state<>'SOURCE_CAPTURED' OR s.verification_status<>'UNVERIFIED'
 OR decision NOT IN ('VERIFIED','REJECTED') OR length(btrim(comment))=0 THEN
 RAISE EXCEPTION 'Source attestation is immutable or run has started' USING ERRCODE='23514'; END IF;
 IF s.is_fixture AND decision='VERIFIED' THEN RAISE EXCEPTION 'Fixture cannot be verified' USING ERRCODE='23514'; END IF;
 UPDATE public.source_snapshots SET verification_status=decision,verified_by=public.context_principal(),
 verified_at=clock_timestamp(),verification_comment=comment WHERE id=sid;
 INSERT INTO public.audit_events(tenant_id,workflow_run_id,actor_id,event_type,correlation_id,details)
 VALUES(r.tenant_id,r.id,public.context_principal(),'SOURCE_ATTESTED',r.correlation_id,jsonb_build_object('source_id',sid,'decision',decision));
 END $$;

-- QA reads relational provenance and the exact stored payload; callers cannot submit a PASS.
CREATE FUNCTION public.qa_findings(rid uuid) RETURNS jsonb LANGUAGE plpgsql SECURITY DEFINER
 SET search_path=pg_catalog,public AS $$
 DECLARE r public.workflow_runs; a public.content_asset_versions; cfg jsonb; findings jsonb:='[]';
 b jsonb; f public.facts; s public.source_snapshots; fid uuid; allowed boolean; path text; expected text;
 slide jsonb; idx int:=0; item record; g jsonb; val jsonb; k text; v text;
 BEGIN
 SELECT * INTO r FROM public.workflow_runs WHERE tenant_id=public.context_tenant() AND id=rid;
 IF r.id IS NULL THEN RAISE EXCEPTION 'Run not found' USING ERRCODE='P0002'; END IF;
 SELECT * INTO a FROM public.content_asset_versions WHERE tenant_id=r.tenant_id AND id=r.asset_version_id;
 SELECT c.payload INTO cfg FROM public.influencer_versions iv JOIN public.character_config_versions c
 ON c.tenant_id=iv.tenant_id AND c.id=iv.character_config_version_id WHERE iv.tenant_id=r.tenant_id AND iv.id=r.influencer_version_id;
 IF a.id IS NULL OR r.research_version_id IS NULL OR r.brief_id IS NULL OR a.research_version_id<>r.research_version_id
 OR EXISTS(SELECT 1 FROM public.content_asset_versions x WHERE x.tenant_id=r.tenant_id AND x.content_asset_id=a.content_asset_id AND x.version>a.version) THEN
 findings:=findings||jsonb_build_array(jsonb_build_object('code','UPSTREAM','category','WORKFLOW','severity','BLOCKED','message','Missing or stale upstream revision'));
 END IF;
 IF a.payload->>'type' IS DISTINCT FROM 'CAROUSEL' OR a.payload->>'schema_version' IS DISTINCT FROM '1'
 OR jsonb_typeof(a.payload->'slides') IS DISTINCT FROM 'array' THEN
 RETURN findings||jsonb_build_array(jsonb_build_object('code','INVALID_SCHEMA','category','CONTENT','severity','BLOCKED','message','Invalid carousel payload schema')); END IF;
 IF EXISTS(SELECT 1 FROM public.research_pack_versions rp JOIN public.research_pack_versions newer
 ON newer.tenant_id=rp.tenant_id AND newer.research_pack_id=rp.research_pack_id AND newer.version>rp.version
 WHERE rp.tenant_id=r.tenant_id AND rp.id=r.research_version_id) THEN
 findings:=findings||jsonb_build_array(jsonb_build_object('code','STALE_RESEARCH','category','WORKFLOW','severity','BLOCKED','message','Newer research revision exists')); END IF;
 IF NOT EXISTS(SELECT 1 FROM public.research_pack_versions rp WHERE rp.tenant_id=r.tenant_id AND rp.id=r.research_version_id AND rp.verification_status='VERIFIED') THEN
 findings:=findings||jsonb_build_array(jsonb_build_object('code','UNVERIFIED_RESEARCH','category','EVIDENCE','severity','BLOCKED','message','Research revision is not verified')); END IF;
 IF coalesce(a.payload->>'disclosure','')='' OR a.payload->>'disclosure' IS DISTINCT FROM cfg->>'disclosure' THEN
 findings:=findings||jsonb_build_array(jsonb_build_object('code','DISCLOSURE','category','CONTENT','severity','BLOCKED','message','Mandatory AI disclosure mismatch')); END IF;
 IF a.payload->>'influencer_version_id' IS DISTINCT FROM r.influencer_version_id::text
 OR a.payload->>'language' IS DISTINCT FROM cfg->>'language'
 OR (a.payload->>'brand_association_level')::int IS DISTINCT FROM (cfg->>'brand_association_level')::int
 OR (a.payload->>'brand_association_level')::int>(cfg->>'max_brand_association_level')::int
 OR a.payload->'cta'->>'text' IS DISTINCT FROM cfg->>'cta' THEN
 findings:=findings||jsonb_build_array(jsonb_build_object('code','CHARACTER_POLICY','category','CONTENT','severity','BLOCKED','message','Character, brand, language or CTA policy mismatch')); END IF;
 IF jsonb_array_length(a.payload->'slides') NOT BETWEEN (cfg->>'min_slides')::int AND (cfg->>'max_slides')::int THEN
 findings:=findings||jsonb_build_array(jsonb_build_object('code','SLIDE_COUNT','category','CONTENT','severity','REVISION_REQUIRED','message','Slide count outside configured range')); END IF;
 FOR slide IN SELECT value FROM jsonb_array_elements(a.payload->'slides') LOOP
 idx:=idx+1;
 IF (slide->>'index')::int<>idx THEN
 findings:=findings||jsonb_build_array(jsonb_build_object('code','SLIDE_ORDER','category','CONTENT','severity','REVISION_REQUIRED','message','Slide indices must be consecutive')); END IF;
 END LOOP;
 IF NOT EXISTS(SELECT 1 FROM public.content_claims WHERE tenant_id=r.tenant_id AND asset_version_id=a.id) THEN
 findings:=findings||jsonb_build_array(jsonb_build_object('code','NO_EVIDENCE','category','EVIDENCE','severity','BLOCKED','message','No supported factual content')); END IF;
 FOR item IN
 SELECT 'caption' AS p,a.payload->'caption' AS block UNION ALL
 SELECT 'cta',a.payload->'cta' UNION ALL
 SELECT 'slides.'||(ordinality-1)::text||'.headline',value->'headline' FROM jsonb_array_elements(a.payload->'slides') WITH ORDINALITY UNION ALL
 SELECT 'slides.'||(ordinality-1)::text||'.body',value->'body' FROM jsonb_array_elements(a.payload->'slides') WITH ORDINALITY
 LOOP
 b:=item.block; path:=item.p;
 IF jsonb_typeof(b->'fact_ids') IS DISTINCT FROM 'array' OR jsonb_typeof(b->'text') IS DISTINCT FROM 'string' THEN
 findings:=findings||jsonb_build_array(jsonb_build_object('code','INVALID_BLOCK','category','CONTENT','severity','BLOCKED','message','Malformed text block'));
 CONTINUE; END IF;
 IF b->>'kind'='CREATIVE' THEN
 IF jsonb_array_length(b->'fact_ids')<>0 OR NOT (cfg->'creative_allowlist' ? (b->>'text')) THEN
 findings:=findings||jsonb_build_array(jsonb_build_object('code','UNSUPPORTED_CREATIVE','category','CONTENT','severity','BLOCKED','message','Creative text must be an approved non-factual template: '||path)); END IF;
 ELSIF b->>'kind'='FACT' THEN
 expected:='';
 IF jsonb_array_length(b->'fact_ids')=0 THEN
 findings:=findings||jsonb_build_array(jsonb_build_object('code','MISSING_FACT','category','EVIDENCE','severity','BLOCKED','message','Fact reference required: '||path)); END IF;
 FOR fid IN SELECT value::uuid FROM jsonb_array_elements_text(b->'fact_ids') LOOP
 SELECT * INTO f FROM public.facts WHERE tenant_id=r.tenant_id AND id=fid;
 SELECT EXISTS(SELECT 1 FROM public.brief_facts bf JOIN public.content_claims cc
 ON cc.tenant_id=bf.tenant_id AND cc.brief_id=bf.brief_id AND cc.research_version_id=bf.research_version_id AND cc.fact_id=bf.fact_id
 WHERE bf.tenant_id=r.tenant_id AND bf.brief_id=r.brief_id AND bf.research_version_id=r.research_version_id AND bf.fact_id=fid
 AND cc.asset_version_id=a.id AND cc.field_path=path) INTO allowed;
 IF f.id IS NULL OR NOT allowed THEN
 findings:=findings||jsonb_build_array(jsonb_build_object('code','FACT_NOT_ALLOWED','category','EVIDENCE','severity','BLOCKED','message','Fact is outside allowed evidence: '||path));
 ELSE
 SELECT * INTO s FROM public.source_snapshots WHERE tenant_id=r.tenant_id AND id=f.source_snapshot_id;
 IF f.verification_status<>'VERIFIED' OR s.verification_status<>'VERIFIED' OR s.is_fixture
 OR btrim(substring(s.raw_content FROM f.span_start+1 FOR f.span_end-f.span_start))<>f.statement
 OR s.checksum<>encode(public.digest(s.raw_content,'sha256'),'hex') THEN
 findings:=findings||jsonb_build_array(jsonb_build_object('code','INELIGIBLE_EVIDENCE','category','EVIDENCE','severity','BLOCKED','message','Evidence is unverified, fixture-only or invalid')); END IF;
 IF f.fact_type='GRANT' THEN
 g:=f.structured_data;
 IF g IS NULL OR coalesce(g->>'deadline','')='' OR coalesce(g->>'opening_date','')=''
 OR g->>'eligibility_status' IS DISTINCT FROM 'VERIFIED'
 OR jsonb_array_length(coalesce(g->'eligible_geography','[]'))=0
 OR jsonb_array_length(coalesce(g->'eligible_business_type','[]'))=0
 OR jsonb_array_length(coalesce(g->'required_evidence','[]'))=0 THEN
 findings:=findings||jsonb_build_array(jsonb_build_object('code','GRANT_REQUIRED','category','EVIDENCE','severity','BLOCKED','message','Mandatory grant dates or eligibility evidence missing'));
 ELSE
 IF (g->>'deadline')::date < (current_timestamp AT TIME ZONE 'UTC')::date
 OR (g->>'opening_date')::date > (g->>'deadline')::date THEN
 findings:=findings||jsonb_build_array(jsonb_build_object('code','GRANT_EXPIRED','category','EVIDENCE','severity','BLOCKED','message','Grant deadline expired or date interval invalid')); END IF;
 END IF;
 FOR k,val IN SELECT key,value FROM jsonb_each(coalesce(g,'{}')) WHERE key NOT IN ('field_evidence','eligibility_status') AND value<>'null'::jsonb LOOP
 FOR v IN SELECT value FROM jsonb_array_elements_text(CASE WHEN jsonb_typeof(val)='array' THEN val ELSE jsonb_build_array(val) END) LOOP
 IF coalesce(g->'field_evidence'->>k,'')='' OR position(v IN (g->'field_evidence'->>k))=0
 OR position((g->'field_evidence'->>k) IN f.statement)=0 THEN
 findings:=findings||jsonb_build_array(jsonb_build_object('code','GRANT_FIELD_EVIDENCE','category','EVIDENCE','severity','BLOCKED','message','Structured grant field lacks exact evidence: '||k)); END IF;
 END LOOP; END LOOP;
 END IF;
 expected:=expected||CASE WHEN expected='' THEN '' ELSE ' ' END||f.statement;
 END IF;
 END LOOP;
 IF b->>'text' IS DISTINCT FROM expected THEN
 findings:=findings||jsonb_build_array(jsonb_build_object('code','UNSUPPORTED_CLAIM','category','EVIDENCE','severity','BLOCKED','message','Factual text differs from verified statements: '||path)); END IF;
 ELSE
 findings:=findings||jsonb_build_array(jsonb_build_object('code','INVALID_BLOCK','category','CONTENT','severity','BLOCKED','message','Unknown content block')); END IF;
 END LOOP;
 RETURN findings;
 END $$;

CREATE FUNCTION public.finish_qa(rid uuid) RETURNS uuid LANGUAGE plpgsql SECURITY DEFINER
 SET search_path=pg_catalog,public AS $$
 DECLARE r public.workflow_runs; findings jsonb; result text; qid uuid; payload jsonb;
 BEGIN
 IF NOT public.has_role('OPERATOR') THEN RAISE EXCEPTION 'Operator required' USING ERRCODE='42501'; END IF;
 SELECT * INTO r FROM public.workflow_runs WHERE tenant_id=public.context_tenant() AND id=rid FOR UPDATE;
 IF r.id IS NULL OR r.state<>'QA_RUNNING' THEN RAISE EXCEPTION 'QA stage required' USING ERRCODE='23514'; END IF;
 findings:=public.qa_findings(rid);
 result:=CASE WHEN EXISTS(SELECT 1 FROM jsonb_array_elements(findings) x WHERE x->>'severity'='BLOCKED') THEN 'BLOCKED'
 WHEN jsonb_array_length(findings)>0 THEN 'REVISION_REQUIRED' ELSE 'PASS' END;
 payload:=jsonb_build_object('schema_version',1,'status',result,'findings',findings);
 INSERT INTO public.qa_reports(tenant_id,workflow_run_id,asset_version_id,research_version_id,version,schema_version,policy_version,status,payload,content_hash)
 VALUES(r.tenant_id,r.id,r.asset_version_id,r.research_version_id,
 (SELECT coalesce(max(version),0)+1 FROM public.qa_reports WHERE tenant_id=r.tenant_id AND asset_version_id=r.asset_version_id),
 1,'deterministic-v1',result,payload,encode(public.digest(payload::text,'sha256'),'hex')) RETURNING id INTO qid;
 UPDATE public.workflow_runs SET qa_report_id=qid,state=CASE WHEN result='PASS' THEN 'AWAITING_APPROVAL' ELSE result END,updated_at=clock_timestamp() WHERE id=rid;
 RETURN qid;
 END $$;

CREATE FUNCTION public.decide_approval(rid uuid, aid uuid, rpid uuid, qid uuid, decision text, comment text DEFAULT NULL) RETURNS uuid LANGUAGE plpgsql SECURITY DEFINER
 SET search_path=pg_catalog,public AS $$
 DECLARE r public.workflow_runs; q public.qa_reports; result uuid;
 BEGIN
 IF NOT public.has_role('APPROVER') THEN RAISE EXCEPTION 'Approver required' USING ERRCODE='42501'; END IF;
 SELECT * INTO r FROM public.workflow_runs WHERE tenant_id=public.context_tenant() AND id=rid FOR UPDATE;
 IF r.id IS NULL THEN RAISE EXCEPTION 'Run not found' USING ERRCODE='P0002'; END IF;
 IF r.state<>'AWAITING_APPROVAL' OR r.asset_version_id IS DISTINCT FROM aid OR r.research_version_id IS DISTINCT FROM rpid
 OR r.qa_report_id IS DISTINCT FROM qid OR decision NOT IN ('APPROVE','REJECT') THEN
 RAISE EXCEPTION 'Approval requires current passing revisions awaiting approval' USING ERRCODE='23514'; END IF;
 SELECT * INTO q FROM public.qa_reports WHERE tenant_id=r.tenant_id AND id=qid;
 IF q.status IS DISTINCT FROM 'PASS' OR (decision='APPROVE' AND (jsonb_array_length(public.qa_findings(rid))>0
 OR EXISTS(SELECT 1 FROM public.qa_reports WHERE tenant_id=r.tenant_id AND asset_version_id=aid AND status='BLOCKED'))) THEN
 RAISE EXCEPTION 'Approval denied by current QA/evidence policy' USING ERRCODE='23514'; END IF;
 INSERT INTO public.approval_records(tenant_id,workflow_run_id,asset_version_id,research_version_id,qa_report_id,approver_id,decision,comment)
 VALUES(r.tenant_id,rid,aid,rpid,qid,public.context_principal(),decision,comment) RETURNING id INTO result;
 UPDATE public.workflow_runs SET state=CASE WHEN decision='APPROVE' THEN 'APPROVED' ELSE 'REVISION_REQUIRED' END,updated_at=clock_timestamp() WHERE id=rid;
 RETURN result;
 END $$;

-- Revisions lock the same run as approvals. Old approvals remain audit history only.
CREATE FUNCTION public.begin_revision(rid uuid) RETURNS void LANGUAGE plpgsql SECURITY DEFINER
 SET search_path=pg_catalog,public AS $$
 DECLARE r public.workflow_runs;
 BEGIN
 IF NOT public.has_role('OPERATOR') THEN RAISE EXCEPTION 'Operator required' USING ERRCODE='42501'; END IF;
 SELECT * INTO r FROM public.workflow_runs WHERE tenant_id=public.context_tenant() AND id=rid FOR UPDATE;
 IF r.id IS NULL OR r.state NOT IN ('AWAITING_APPROVAL','APPROVED','REVISION_REQUIRED','BLOCKED') THEN
 RAISE EXCEPTION 'Run cannot be revised in current state' USING ERRCODE='23514'; END IF;
 UPDATE public.workflow_runs SET state='CONTENT_GENERATING',asset_version_id=NULL,qa_report_id=NULL,updated_at=clock_timestamp() WHERE id=rid;
 END $$;

GRANT INSERT ON workflow_runs,source_snapshots,research_packs,research_pack_versions,research_pack_sources,facts,research_pack_facts,
content_briefs,brief_facts,content_assets,content_asset_versions,content_claims,skill_runs,cost_events TO mediaos_runtime;
GRANT UPDATE(ended_at,latency_ms,status,error_category,retryable,retry_at,output,research_version_id,brief_id,asset_version_id,qa_report_id) ON skill_runs TO mediaos_runtime;
REVOKE ALL ON ALL FUNCTIONS IN SCHEMA public FROM PUBLIC;
GRANT EXECUTE ON FUNCTION context_tenant(),context_principal(),has_role(text),authenticate(text,uuid),
checkpoint(uuid,text,uuid),verify_source(uuid,text,text),qa_findings(uuid),finish_qa(uuid),
decide_approval(uuid,uuid,uuid,uuid,text,text),begin_revision(uuid) TO mediaos_runtime;

-- Seal relational memberships at the same boundary as their immutable payloads.
CREATE FUNCTION public.guard_link() RETURNS trigger LANGUAGE plpgsql SECURITY DEFINER
 SET search_path=pg_catalog,public AS $$
 DECLARE rid uuid; expected text; actual text;
 BEGIN
 IF NOT public.has_role('OPERATOR') OR NEW.tenant_id IS DISTINCT FROM public.context_tenant() THEN
 RAISE EXCEPTION 'Operator required' USING ERRCODE='42501'; END IF;
 IF TG_TABLE_NAME IN ('research_pack_sources','research_pack_facts') THEN
 SELECT workflow_run_id INTO rid FROM public.research_pack_versions WHERE tenant_id=NEW.tenant_id AND id=NEW.research_version_id;
 expected:='RESEARCHING';
 ELSIF TG_TABLE_NAME='brief_facts' THEN
 SELECT workflow_run_id INTO rid FROM public.content_briefs WHERE tenant_id=NEW.tenant_id AND id=NEW.brief_id;
 expected:='BRIEFING';
 ELSE
 SELECT workflow_run_id INTO rid FROM public.content_asset_versions WHERE tenant_id=NEW.tenant_id AND id=NEW.asset_version_id;
 expected:='CONTENT_GENERATING';
 END IF;
 SELECT state INTO actual FROM public.workflow_runs WHERE tenant_id=NEW.tenant_id AND id=rid FOR UPDATE;
 IF actual IS DISTINCT FROM expected THEN RAISE EXCEPTION 'Evidence membership is sealed' USING ERRCODE='23514'; END IF;
 RETURN NEW;
 END $$;
CREATE TRIGGER guard_link BEFORE INSERT ON research_pack_sources FOR EACH ROW EXECUTE FUNCTION guard_link();
CREATE TRIGGER guard_link BEFORE INSERT ON research_pack_facts FOR EACH ROW EXECUTE FUNCTION guard_link();
CREATE TRIGGER guard_link BEFORE INSERT ON brief_facts FOR EACH ROW EXECUTE FUNCTION guard_link();
CREATE TRIGGER guard_link BEFORE INSERT ON content_claims FOR EACH ROW EXECUTE FUNCTION guard_link();

ALTER TABLE skill_runs ADD UNIQUE(tenant_id,id,workflow_run_id);
ALTER TABLE cost_events ADD FOREIGN KEY(tenant_id,skill_run_id,workflow_run_id) REFERENCES skill_runs(tenant_id,id,workflow_run_id);
CREATE FUNCTION public.guard_skill() RETURNS trigger LANGUAGE plpgsql AS $$
 BEGIN
 IF NOT public.has_role('OPERATOR') THEN RAISE EXCEPTION 'Operator required' USING ERRCODE='42501'; END IF;
 IF TG_OP='UPDATE' AND (OLD.status<>'RUNNING' OR NEW.status NOT IN ('SUCCEEDED','FAILED','INTERRUPTED')
 OR NEW.ended_at IS NULL OR NEW.latency_ms IS NULL OR NEW.latency_ms<0) THEN
 RAISE EXCEPTION 'Invalid skill attempt completion' USING ERRCODE='23514'; END IF;
 IF TG_OP='INSERT' AND NEW.status<>'RUNNING' THEN RAISE EXCEPTION 'Attempt must start RUNNING' USING ERRCODE='23514'; END IF;
 RETURN NEW;
 END $$;
CREATE TRIGGER guard_skill BEFORE INSERT OR UPDATE ON skill_runs FOR EACH ROW EXECUTE FUNCTION guard_skill();
REVOKE ALL ON FUNCTION guard_link(),guard_skill() FROM PUBLIC;
