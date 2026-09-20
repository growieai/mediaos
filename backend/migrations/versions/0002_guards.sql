CREATE FUNCTION public.source_backoff(host text) RETURNS timestamptz LANGUAGE plpgsql SECURITY DEFINER
 SET search_path=pg_catalog,public AS $$ BEGIN
 IF NOT public.has_role('INGESTOR') OR NOT EXISTS(SELECT 1 FROM public.source_definitions WHERE tenant_id=public.context_tenant() AND connector_configuration->'allowed_hosts' ? host) THEN
 RAISE EXCEPTION 'Trusted source host required' USING ERRCODE='42501'; END IF;
 RETURN (SELECT retry_at FROM private.source_host_cooldowns WHERE hostname=host);
 END $$;
CREATE FUNCTION public.record_source_backoff(host text, until_time timestamptz) RETURNS void LANGUAGE plpgsql SECURITY DEFINER
 SET search_path=pg_catalog,public AS $$ BEGIN
 PERFORM public.source_backoff(host);
 IF until_time IS NULL THEN RAISE EXCEPTION 'Backoff timestamp required' USING ERRCODE='23514'; END IF;
 INSERT INTO private.source_host_cooldowns AS existing(hostname,retry_at) VALUES(host,until_time)
 ON CONFLICT(hostname) DO UPDATE SET retry_at=greatest(existing.retry_at,EXCLUDED.retry_at),updated_at=clock_timestamp();
 END $$;
REVOKE ALL ON FUNCTION source_backoff(text),record_source_backoff(text,timestamptz) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION source_backoff(text),record_source_backoff(text,timestamptz) TO mediaos_runtime;

CREATE FUNCTION public.guard_intelligence_write() RETURNS trigger LANGUAGE plpgsql SECURITY DEFINER
 SET search_path=pg_catalog,public AS $$
 BEGIN
 IF NEW.tenant_id IS DISTINCT FROM public.context_tenant() OR NOT public.has_role('INGESTOR') THEN
 RAISE EXCEPTION 'Trusted ingestion principal required' USING ERRCODE='42501'; END IF;
 IF TG_TABLE_NAME='raw_source_documents' THEN
 IF NEW.checksum<>encode(public.digest(NEW.body,'sha256'),'hex') THEN
 RAISE EXCEPTION 'Raw response checksum mismatch' USING ERRCODE='23514'; END IF;
 END IF;
 IF TG_TABLE_NAME IN ('source_links','verification_conflicts') THEN
 PERFORM 1 FROM public.opportunities WHERE tenant_id=NEW.tenant_id AND id=NEW.opportunity_id FOR UPDATE;
 END IF;
 RETURN NEW; END $$;

DO $$ DECLARE t text; BEGIN
 FOREACH t IN ARRAY ARRAY['raw_source_documents','source_observations','ingestion_attempts','opportunities','opportunity_versions','source_links','opportunity_facts','verification_conflicts','change_events','duplicate_candidates'] LOOP
 EXECUTE format('CREATE TRIGGER trusted_writer BEFORE INSERT ON %I FOR EACH ROW EXECUTE FUNCTION guard_intelligence_write()',t);
 EXECUTE format('GRANT INSERT ON %I TO mediaos_runtime',t);
 END LOOP;
END $$;

CREATE FUNCTION public.guard_ingestion_run() RETURNS trigger LANGUAGE plpgsql SECURITY DEFINER
 SET search_path=pg_catalog,public AS $$
 BEGIN
 IF NEW.tenant_id IS DISTINCT FROM public.context_tenant() THEN RAISE EXCEPTION 'Tenant mismatch' USING ERRCODE='42501'; END IF;
 IF TG_OP='INSERT' THEN
 IF NOT public.has_role('OPERATOR') OR NEW.created_by IS DISTINCT FROM public.context_principal() OR NEW.status<>'CREATED'
 OR NEW.cursor<>'' OR NEW.pending_page IS NOT NULL OR NEW.document_offset<>0 THEN
 RAISE EXCEPTION 'Operator required to request ingestion' USING ERRCODE='42501'; END IF;
 IF NOT EXISTS(SELECT 1 FROM public.source_definitions WHERE tenant_id=NEW.tenant_id AND id=NEW.source_definition_id AND enabled AND access_policy='PUBLIC_API') THEN
 RAISE EXCEPTION 'Source disabled by access policy' USING ERRCODE='23514'; END IF;
 ELSE
 IF NOT public.has_role('INGESTOR') OR OLD.status='SUCCEEDED' OR NEW.status NOT IN ('RUNNING','SUCCEEDED','FAILED') THEN
 RAISE EXCEPTION 'Invalid ingestion checkpoint' USING ERRCODE='23514'; END IF;
 END IF;
 RETURN NEW; END $$;
CREATE TRIGGER guard_ingestion_run BEFORE INSERT OR UPDATE ON ingestion_runs FOR EACH ROW EXECUTE FUNCTION guard_ingestion_run();
GRANT INSERT ON ingestion_runs TO mediaos_runtime;
GRANT UPDATE(status,cursor,pending_page,document_offset,started_at,ended_at,error_category,retry_at,counters) ON ingestion_runs TO mediaos_runtime;

CREATE FUNCTION public.guard_source_capture() RETURNS trigger LANGUAGE plpgsql SECURITY DEFINER
 SET search_path=pg_catalog,public AS $$
 DECLARE r public.workflow_runs; d public.raw_source_documents; s public.source_definitions; parent public.source_snapshots;
 BEGIN
 IF NEW.tenant_id IS DISTINCT FROM public.context_tenant() OR NEW.created_by IS DISTINCT FROM public.context_principal()
 OR NEW.verification_status<>'UNVERIFIED' OR NEW.verified_by IS NOT NULL OR NEW.verified_at IS NOT NULL
 OR NEW.checksum<>encode(public.digest(NEW.raw_content,'sha256'),'hex') THEN
 RAISE EXCEPTION 'Invalid source capture' USING ERRCODE='23514'; END IF;
 IF NEW.workflow_run_id IS NOT NULL THEN
 SELECT * INTO r FROM public.workflow_runs WHERE tenant_id=NEW.tenant_id AND id=NEW.workflow_run_id FOR UPDATE;
 IF NOT public.has_role('OPERATOR') OR r.id IS NULL OR r.state<>'CREATED' OR NEW.raw_document_id IS NOT NULL OR NEW.source_definition_id IS NOT NULL OR NEW.parent_snapshot_id IS NOT NULL OR NEW.representation<>'MANUAL' THEN
 RAISE EXCEPTION 'Invalid workflow source stage' USING ERRCODE='23514'; END IF;
 ELSE
 IF NOT public.has_role('INGESTOR') THEN RAISE EXCEPTION 'Ingestor required' USING ERRCODE='42501'; END IF;
 SELECT * INTO d FROM public.raw_source_documents WHERE tenant_id=NEW.tenant_id AND id=NEW.raw_document_id;
 SELECT * INTO s FROM public.source_definitions WHERE tenant_id=NEW.tenant_id AND id=NEW.source_definition_id;
 IF d.id IS NULL OR d.source_definition_id<>s.id OR NOT s.enabled OR s.trust_level<>'OFFICIAL' OR s.authority_level<>'PRIMARY'
 OR NEW.source_type<>'OFFICIAL' OR NEW.classification<>'PRIMARY' OR NEW.is_fixture IS DISTINCT FROM d.is_fixture
 OR NEW.canonical_url IS DISTINCT FROM d.url OR NEW.captured_at IS DISTINCT FROM d.captured_at THEN
 RAISE EXCEPTION 'Official source provenance mismatch' USING ERRCODE='23514'; END IF;
 IF NEW.representation='RAW' THEN
 IF NEW.raw_content IS DISTINCT FROM d.body OR NEW.parent_snapshot_id IS NOT NULL THEN RAISE EXCEPTION 'Raw snapshot must preserve exact response' USING ERRCODE='23514'; END IF;
 ELSIF NEW.representation='NORMALIZED' THEN
 SELECT * INTO parent FROM public.source_snapshots WHERE tenant_id=NEW.tenant_id AND id=NEW.parent_snapshot_id;
 IF parent.id IS NULL OR parent.representation<>'RAW' OR parent.raw_document_id<>NEW.raw_document_id THEN RAISE EXCEPTION 'Normalization requires original raw snapshot' USING ERRCODE='23514'; END IF;
 ELSE RAISE EXCEPTION 'Ingestion representation required' USING ERRCODE='23514'; END IF;
 END IF;
 RETURN NEW; END $$;
DROP TRIGGER guard_artifact ON source_snapshots;
CREATE TRIGGER guard_source_capture BEFORE INSERT ON source_snapshots FOR EACH ROW EXECUTE FUNCTION guard_source_capture();

CREATE FUNCTION public.attest_official_snapshot(sid uuid) RETURNS void LANGUAGE plpgsql SECURITY DEFINER
 SET search_path=pg_catalog,public AS $$
 DECLARE s public.source_snapshots; d public.raw_source_documents; definition public.source_definitions;
 BEGIN
 IF NOT public.has_role('INGESTOR') THEN RAISE EXCEPTION 'Ingestor required' USING ERRCODE='42501'; END IF;
 SELECT * INTO s FROM public.source_snapshots WHERE tenant_id=public.context_tenant() AND id=sid FOR UPDATE;
 SELECT * INTO d FROM public.raw_source_documents WHERE tenant_id=s.tenant_id AND id=s.raw_document_id;
 SELECT * INTO definition FROM public.source_definitions WHERE tenant_id=s.tenant_id AND id=s.source_definition_id;
 IF s.id IS NULL OR s.workflow_run_id IS NOT NULL OR s.verification_status<>'UNVERIFIED' OR s.is_fixture OR d.is_fixture
 OR d.id IS NULL OR NOT definition.enabled OR definition.access_policy<>'PUBLIC_API'
 OR (definition.connector='BDNS' AND d.url !~ '^https://www\.infosubvenciones\.es/bdnstrans/api/convocatorias\?numConv=[0-9]+$')
 OR (definition.connector='BOE' AND d.url !~ '^https://www\.boe\.es/diario_boe/xml\.php\?id=BOE-[AB]-[0-9]{4}-[0-9]+$')
 OR definition.connector='CAMARA' THEN
 RAISE EXCEPTION 'Snapshot is not eligible for official attestation' USING ERRCODE='23514'; END IF;
 UPDATE public.source_snapshots SET verification_status='VERIFIED',verified_by=public.context_principal(),verified_at=clock_timestamp(),verification_comment='Official HTTPS capture; deterministic extraction; '||definition.parser_version WHERE id=sid;
 END $$;

CREATE FUNCTION public.guard_opportunity_version() RETURNS trigger LANGUAGE plpgsql SECURITY DEFINER
 SET search_path=pg_catalog,public AS $$
 DECLARE o public.opportunities; expected int;
 BEGIN
 SELECT * INTO o FROM public.opportunities WHERE tenant_id=NEW.tenant_id AND id=NEW.opportunity_id FOR UPDATE;
 SELECT coalesce(max(version),0)+1 INTO expected FROM public.opportunity_versions WHERE tenant_id=NEW.tenant_id AND opportunity_id=NEW.opportunity_id;
 IF NEW.version<>expected OR NOT EXISTS(SELECT 1 FROM public.source_links WHERE tenant_id=NEW.tenant_id AND opportunity_id=NEW.opportunity_id AND source_snapshot_id=NEW.source_snapshot_id) THEN
 RAISE EXCEPTION 'Opportunity version lineage mismatch' USING ERRCODE='23514'; END IF;
 RETURN NEW; END $$;
CREATE TRIGGER version_guard BEFORE INSERT ON opportunity_versions FOR EACH ROW EXECUTE FUNCTION guard_opportunity_version();
CREATE FUNCTION public.advance_opportunity() RETURNS trigger LANGUAGE plpgsql SECURITY DEFINER
 SET search_path=pg_catalog,public AS $$ BEGIN
 UPDATE public.opportunities SET current_version_id=NEW.id,last_changed_at=clock_timestamp(),last_seen_at=clock_timestamp() WHERE tenant_id=NEW.tenant_id AND id=NEW.opportunity_id;
 RETURN NEW; END $$;
CREATE TRIGGER advance_opportunity AFTER INSERT ON opportunity_versions FOR EACH ROW EXECUTE FUNCTION advance_opportunity();
CREATE FUNCTION public.touch_opportunity(oid uuid) RETURNS void LANGUAGE plpgsql SECURITY DEFINER
 SET search_path=pg_catalog,public AS $$ BEGIN
 IF NOT public.has_role('INGESTOR') THEN RAISE EXCEPTION 'Ingestor required' USING ERRCODE='42501'; END IF;
 UPDATE public.opportunities SET last_seen_at=clock_timestamp() WHERE tenant_id=public.context_tenant() AND id=oid;
 END $$;
CREATE FUNCTION public.guard_opportunity_fact() RETURNS trigger LANGUAGE plpgsql SECURITY DEFINER
 SET search_path=pg_catalog,public AS $$ DECLARE s public.source_snapshots; BEGIN
 SELECT * INTO s FROM public.source_snapshots WHERE tenant_id=NEW.tenant_id AND id=NEW.source_snapshot_id;
 IF s.id IS NULL OR NEW.span_end>length(s.raw_content) OR substring(s.raw_content FROM NEW.span_start+1 FOR NEW.span_end-NEW.span_start)<>NEW.statement
 OR (NEW.verification_status='VERIFIED' AND (s.verification_status<>'VERIFIED' OR s.is_fixture)) THEN
 RAISE EXCEPTION 'Fact evidence mismatch' USING ERRCODE='23514'; END IF;
 RETURN NEW; END $$;
CREATE TRIGGER evidence_guard BEFORE INSERT ON opportunity_facts FOR EACH ROW EXECUTE FUNCTION guard_opportunity_fact();

CREATE FUNCTION public.guard_editorial_write() RETURNS trigger LANGUAGE plpgsql AS $$ BEGIN
 IF NOT public.has_role('OPERATOR') OR NEW.tenant_id IS DISTINCT FROM public.context_tenant() THEN RAISE EXCEPTION 'Operator required' USING ERRCODE='42501'; END IF;
 RETURN NEW; END $$;
CREATE TRIGGER editorial_writer BEFORE INSERT ON relevance_scores FOR EACH ROW EXECUTE FUNCTION guard_editorial_write();
CREATE TRIGGER editorial_writer BEFORE INSERT ON editorial_decisions FOR EACH ROW EXECUTE FUNCTION guard_editorial_write();
GRANT INSERT ON relevance_scores,editorial_decisions TO mediaos_runtime;

CREATE FUNCTION public.bind_opportunity_run(rid uuid, vid uuid, sid uuid, eid uuid, audience uuid, context jsonb) RETURNS void LANGUAGE plpgsql SECURITY DEFINER
 SET search_path=pg_catalog,public AS $$
 DECLARE r public.workflow_runs; v public.opportunity_versions; o public.opportunities;
 original public.source_snapshots; copy public.source_snapshots; e public.editorial_decisions; score public.relevance_scores;
 policy public.mission_editorial_policies; fresh_until timestamptz;
 BEGIN
 IF NOT public.has_role('OPERATOR') THEN RAISE EXCEPTION 'Operator required' USING ERRCODE='42501'; END IF;
 SELECT * INTO r FROM public.workflow_runs WHERE tenant_id=public.context_tenant() AND id=rid FOR UPDATE;
 SELECT * INTO v FROM public.opportunity_versions WHERE tenant_id=r.tenant_id AND id=vid;
 SELECT * INTO o FROM public.opportunities WHERE tenant_id=r.tenant_id AND id=v.opportunity_id FOR UPDATE;
 SELECT * INTO original FROM public.source_snapshots WHERE tenant_id=r.tenant_id AND id=sid;
 SELECT * INTO copy FROM public.source_snapshots WHERE tenant_id=r.tenant_id AND id=r.source_snapshot_id FOR UPDATE;
 SELECT * INTO e FROM public.editorial_decisions WHERE tenant_id=r.tenant_id AND id=eid;
 SELECT * INTO score FROM public.relevance_scores WHERE tenant_id=r.tenant_id AND id=e.relevance_score_id;
 SELECT * INTO policy FROM public.mission_editorial_policies WHERE tenant_id=r.tenant_id AND mission_id=r.mission_id ORDER BY version DESC LIMIT 1;
 SELECT min(coalesce((SELECT max(ob.fetched_at) FROM public.source_observations ob WHERE ob.tenant_id=s.tenant_id AND ob.raw_document_id=s.raw_document_id AND NOT ob.is_fixture),s.captured_at))
 + make_interval(hours=>(policy.payload->>'freshness_hours')::int) INTO fresh_until
 FROM public.source_snapshots s WHERE s.tenant_id=r.tenant_id AND v.payload->'source_hashes' ? s.id::text;
 IF r.id IS NULL OR r.state<>'SOURCE_CAPTURED' OR v.id IS NULL OR o.current_version_id<>vid OR original.id IS NULL OR original.workflow_run_id IS NOT NULL
 OR copy.checksum IS DISTINCT FROM original.checksum OR copy.canonical_url IS DISTINCT FROM original.canonical_url
 OR copy.is_fixture IS DISTINCT FROM original.is_fixture OR copy.evidence_input IS DISTINCT FROM original.evidence_input
 OR copy.verification_status<>'UNVERIFIED' OR e.id IS NULL OR e.decision<>'CREATE_CONTENT'
 OR score.opportunity_version_id<>vid OR score.mission_id<>r.mission_id OR score.audience_segment_id<>audience OR score.expires_at<=now()
 OR policy.id IS NULL OR score.policy_id<>policy.id OR fresh_until IS NULL OR fresh_until<=now()
 OR NOT EXISTS(SELECT 1 FROM public.source_links WHERE tenant_id=r.tenant_id AND opportunity_id=o.id AND source_snapshot_id=sid) THEN
 RAISE EXCEPTION 'Opportunity workflow lineage rejected' USING ERRCODE='23514'; END IF;
 INSERT INTO public.workflow_opportunities(tenant_id,workflow_run_id,opportunity_id,opportunity_version_id,source_snapshot_id,editorial_decision_id,audience_segment_id,expires_at,research_context)
 VALUES(r.tenant_id,rid,o.id,vid,sid,eid,audience,least(score.expires_at,fresh_until),context);
 UPDATE public.source_snapshots SET raw_document_id=original.raw_document_id,source_definition_id=original.source_definition_id,parent_snapshot_id=original.id,representation='NORMALIZED',
 verification_status=original.verification_status,verified_by=original.verified_by,verified_at=original.verified_at,
 verification_comment='Linked immutable official snapshot '||original.id WHERE id=copy.id;
 INSERT INTO public.audit_events(tenant_id,workflow_run_id,actor_id,event_type,correlation_id,details)
 VALUES(r.tenant_id,rid,public.context_principal(),'OPPORTUNITY_LINKED',r.correlation_id,jsonb_build_object('opportunity_version_id',vid,'source_snapshot_id',sid,'editorial_decision_id',eid));
 END $$;

-- Keep the entire M1 policy, then add live-source freshness/conflict checks.
ALTER FUNCTION public.qa_findings(uuid) RENAME TO qa_findings_m1;
REVOKE ALL ON FUNCTION qa_findings_m1(uuid) FROM mediaos_runtime;
CREATE FUNCTION public.intelligence_findings(rid uuid) RETURNS jsonb LANGUAGE plpgsql SECURITY DEFINER
 SET search_path=pg_catalog,public AS $$
 DECLARE b public.workflow_opportunities; o public.opportunities; v public.opportunity_versions;
 findings jsonb:='[]'; code text; reasons text[]:=ARRAY[]::text[];
 BEGIN
 SELECT * INTO b FROM public.workflow_opportunities WHERE tenant_id=public.context_tenant() AND workflow_run_id=rid;
 IF b.id IS NULL THEN RETURN findings; END IF;
 -- Ingestion updates and approval serialize on the same opportunity row.
 SELECT * INTO o FROM public.opportunities WHERE tenant_id=b.tenant_id AND id=b.opportunity_id FOR UPDATE;
 SELECT * INTO v FROM public.opportunity_versions WHERE tenant_id=b.tenant_id AND id=b.opportunity_version_id;
 IF o.current_version_id IS DISTINCT FROM b.opportunity_version_id THEN reasons:=array_append(reasons,'STALE_OPPORTUNITY'); END IF;
 IF b.expires_at<=clock_timestamp() THEN reasons:=array_append(reasons,'STALE_RESEARCH'); END IF;
 IF v.closing_date IS NULL THEN reasons:=array_append(reasons,'MISSING_DEADLINE');
 ELSIF v.closing_date<timezone('UTC',now())::date THEN reasons:=array_append(reasons,'EXPIRED_OPPORTUNITY'); END IF;
 IF v.opening_date IS NOT NULL AND v.closing_date<v.opening_date THEN reasons:=array_append(reasons,'INVALID_DATE_INTERVAL'); END IF;
 IF EXISTS(SELECT 1 FROM public.verification_conflicts WHERE tenant_id=b.tenant_id AND opportunity_id=o.id) THEN reasons:=array_append(reasons,'OFFICIAL_SOURCE_CONFLICT'); END IF;
 IF EXISTS(SELECT 1 FROM public.source_links l JOIN public.source_snapshots s ON s.tenant_id=l.tenant_id AND s.id=l.source_snapshot_id
 WHERE l.tenant_id=b.tenant_id AND l.opportunity_id=o.id AND v.payload->'source_hashes' ? s.id::text AND (s.is_fixture OR s.verification_status<>'VERIFIED')) THEN reasons:=array_append(reasons,'INELIGIBLE_OFFICIAL_EVIDENCE'); END IF;
 FOREACH code IN ARRAY reasons LOOP
 findings:=findings||jsonb_build_array(jsonb_build_object('code',code,'category','EVIDENCE','severity','BLOCKED','message',code));
 END LOOP;
 RETURN findings; END $$;
CREATE FUNCTION public.qa_findings(rid uuid) RETURNS jsonb LANGUAGE plpgsql SECURITY DEFINER
 SET search_path=pg_catalog,public AS $$ BEGIN
 RETURN public.qa_findings_m1(rid)||public.intelligence_findings(rid);
 END $$;
CREATE FUNCTION public.guard_live_approval() RETURNS trigger LANGUAGE plpgsql SECURITY DEFINER
 SET search_path=pg_catalog,public AS $$ BEGIN
 IF NEW.decision='APPROVE' AND jsonb_array_length(public.intelligence_findings(NEW.workflow_run_id))>0 THEN
 RAISE EXCEPTION 'Live evidence is stale or conflicted' USING ERRCODE='23514'; END IF;
 RETURN NEW; END $$;
CREATE TRIGGER live_approval_guard BEFORE INSERT ON approval_records FOR EACH ROW EXECUTE FUNCTION guard_live_approval();

REVOKE ALL ON FUNCTION guard_intelligence_write(),guard_ingestion_run(),guard_source_capture(),attest_official_snapshot(uuid),guard_opportunity_version(),advance_opportunity(),touch_opportunity(uuid),guard_opportunity_fact(),guard_editorial_write(),bind_opportunity_run(uuid,uuid,uuid,uuid,uuid,jsonb),intelligence_findings(uuid),qa_findings(uuid),guard_live_approval() FROM PUBLIC;
GRANT EXECUTE ON FUNCTION attest_official_snapshot(uuid),touch_opportunity(uuid),bind_opportunity_run(uuid,uuid,uuid,uuid,uuid,jsonb),qa_findings(uuid) TO mediaos_runtime;
