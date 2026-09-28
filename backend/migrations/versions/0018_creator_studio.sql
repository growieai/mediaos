-- Studio creates generic identities; it cannot change source trust or content approval.
CREATE TABLE studio_categories (
 id text PRIMARY KEY, name text NOT NULL, description text NOT NULL,
 accent text NOT NULL CHECK(accent ~ '^#[0-9A-Fa-f]{6}$'),
 position integer NOT NULL UNIQUE, enabled boolean NOT NULL DEFAULT true
);
INSERT INTO studio_categories(id,name,description,accent,position) VALUES
 ('business','Business & money','Explain sourced opportunities, business ideas and practical decisions.','#285850',1),
 ('beauty','Beauty & style','Explore beauty, personal style and the businesses behind them.','#80324F',2),
 ('food','Food & hospitality','Tell stories about food, restaurants and hospitality.','#9A3B22',3),
 ('fitness','Fitness & wellbeing','Create evidence-led fitness and wellbeing content.','#275947',4),
 ('technology','Technology','Make technology understandable and useful.','#313D8A',5),
 ('travel','Travel & places','Explore destinations, local places and travel information.','#24566D',6),
 ('education','Education','Teach clearly with attributable sources.','#65418D',7),
 ('lifestyle','Lifestyle','Build a focused lifestyle publication for your audience.','#755024',8);
ALTER TABLE studio_categories ENABLE ROW LEVEL SECURITY;
ALTER TABLE studio_categories FORCE ROW LEVEL SECURITY;
CREATE POLICY authenticated_catalog ON studio_categories
 USING(context_tenant() IS NOT NULL);
GRANT SELECT ON studio_categories TO mediaos_runtime;

CREATE TABLE studio_creations (
 id uuid PRIMARY KEY DEFAULT gen_random_uuid(), tenant_id uuid NOT NULL REFERENCES tenants,
 influencer_id uuid NOT NULL, mission_id uuid NOT NULL, influencer_version_id uuid NOT NULL,
 character_config_version_id uuid NOT NULL, visual_config_version_id uuid NOT NULL,
 category_id text NOT NULL REFERENCES studio_categories,
 idempotency_key text NOT NULL CHECK(length(idempotency_key) BETWEEN 1 AND 128),
 schema_version int NOT NULL CHECK(schema_version=1), payload jsonb NOT NULL,
 input_hash text NOT NULL CHECK(input_hash=private.visual_hash(payload)),
 created_by uuid NOT NULL, correlation_id uuid NOT NULL,
 created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
 UNIQUE(tenant_id,id), UNIQUE(tenant_id,idempotency_key), UNIQUE(tenant_id,influencer_id),
 FOREIGN KEY(tenant_id,influencer_id) REFERENCES influencers(tenant_id,id),
 FOREIGN KEY(tenant_id,mission_id,influencer_id) REFERENCES missions(tenant_id,id,influencer_id),
 FOREIGN KEY(tenant_id,influencer_version_id,influencer_id) REFERENCES influencer_versions(tenant_id,id,influencer_id),
 FOREIGN KEY(tenant_id,character_config_version_id,influencer_id) REFERENCES character_config_versions(tenant_id,id,influencer_id),
 FOREIGN KEY(tenant_id,visual_config_version_id) REFERENCES visual_config_versions(tenant_id,id),
 FOREIGN KEY(tenant_id,created_by) REFERENCES tenant_memberships(tenant_id,principal_id)
);
ALTER TABLE studio_creations ENABLE ROW LEVEL SECURITY;
ALTER TABLE studio_creations FORCE ROW LEVEL SECURITY;
CREATE POLICY tenant_boundary ON studio_creations
 USING(tenant_id=context_tenant()) WITH CHECK(tenant_id=context_tenant());
CREATE TRIGGER immutable BEFORE UPDATE OR DELETE ON studio_creations
 FOR EACH ROW EXECUTE FUNCTION immutable_record();
GRANT SELECT ON studio_creations TO mediaos_runtime;

CREATE FUNCTION public.studio_create_influencer(request_key text, request_payload jsonb, correlation uuid)
 RETURNS uuid LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,public AS $$
 DECLARE tenant uuid:=public.context_tenant(); actor uuid:=public.context_principal();
 existing public.studio_creations; category public.studio_categories;
 iid uuid:=gen_random_uuid(); mid uuid:=gen_random_uuid(); cid uuid:=gen_random_uuid();
 vid uuid:=gen_random_uuid(); visual_id uuid:=gen_random_uuid(); config jsonb; visual jsonb;
 disclosure text; headline text; cta text; item jsonb; label text; tone_label text;
 BEGIN
 IF NOT public.has_role('OPERATOR') OR tenant IS NULL OR actor IS NULL THEN
   RAISE EXCEPTION 'Operator required' USING ERRCODE='42501'; END IF;
 IF request_key IS NULL OR length(request_key) NOT BETWEEN 1 AND 128 OR correlation IS NULL
 OR request_payload IS NULL OR jsonb_typeof(request_payload)<>'object'
 OR NOT(request_payload ?& ARRAY['name','category_id','language','tone','audience','objective'])
 OR request_payload-ARRAY['name','category_id','language','tone','audience','objective']<>'{}'::jsonb THEN
   RAISE EXCEPTION 'Invalid studio input' USING ERRCODE='23514'; END IF;
 FOREACH label IN ARRAY ARRAY['name','category_id','language','tone','objective'] LOOP
   IF jsonb_typeof(request_payload->label)<>'string'
   OR length(request_payload->>label) NOT BETWEEN 1 AND (CASE WHEN label='objective' THEN 500 ELSE 100 END)
   OR request_payload->>label<>btrim(request_payload->>label)
   OR request_payload->>label ~ '[[:cntrl:]]' THEN
     RAISE EXCEPTION 'Invalid studio text' USING ERRCODE='23514'; END IF;
 END LOOP;
 IF request_payload->>'language' NOT IN ('en','es') OR request_payload->>'tone' NOT IN ('CLEAR','WARM','BOLD')
 OR jsonb_typeof(request_payload->'audience')<>'array' THEN
   RAISE EXCEPTION 'Invalid studio options' USING ERRCODE='23514'; END IF;
 IF jsonb_array_length(request_payload->'audience') NOT BETWEEN 1 AND 8 THEN
   RAISE EXCEPTION 'Invalid audience size' USING ERRCODE='23514'; END IF;
 FOR item IN SELECT value FROM jsonb_array_elements(request_payload->'audience') LOOP
   label:=item#>>'{}';
   IF jsonb_typeof(item)<>'string' OR length(label) NOT BETWEEN 1 AND 100
   OR label<>btrim(label) OR label ~ '[[:cntrl:]]' THEN
     RAISE EXCEPTION 'Invalid audience label' USING ERRCODE='23514'; END IF;
 END LOOP;
 IF (SELECT count(DISTINCT value) FROM jsonb_array_elements(request_payload->'audience'))
    <>jsonb_array_length(request_payload->'audience') THEN
   RAISE EXCEPTION 'Duplicate audience label' USING ERRCODE='23514'; END IF;
 -- One lock covers first writer and replay; independent tenants can reuse a key.
 PERFORM pg_advisory_xact_lock(hashtextextended('studio-create:'||tenant::text||':'||request_key,0));
 SELECT * INTO existing FROM public.studio_creations
 WHERE tenant_id=tenant AND idempotency_key=request_key;
 IF existing.id IS NOT NULL THEN
   IF existing.input_hash<>private.visual_hash(request_payload) THEN
     RAISE EXCEPTION 'Studio idempotency conflict' USING ERRCODE='23505'; END IF;
   RETURN existing.influencer_id;
 END IF;
 SELECT * INTO category FROM public.studio_categories
 WHERE id=request_payload->>'category_id' AND enabled;
 IF category.id IS NULL THEN RAISE EXCEPTION 'Category unavailable' USING ERRCODE='23514'; END IF;
 IF request_payload->>'language'='es' THEN
   disclosure:='Creador virtual generado con IA.';
   headline:='Mira los detalles'; cta:='Consulta la fuente y guarda esta publicación.';
   tone_label:=CASE request_payload->>'tone' WHEN 'CLEAR' THEN 'Claro y directo'
     WHEN 'WARM' THEN 'Cercano y atento' ELSE 'Enérgico y conciso' END;
 ELSE
   disclosure:='AI-generated virtual creator.';
   headline:='Take a closer look'; cta:='Read the source and save this post.';
   tone_label:=CASE request_payload->>'tone' WHEN 'CLEAR' THEN 'Clear and direct'
     WHEN 'WARM' THEN 'Warm and thoughtful' ELSE 'Bold and concise' END;
 END IF;
 config:=jsonb_build_object(
   'schema_version',1,'persona','An openly disclosed virtual creator. Never impersonate a real person.',
   'voice',tone_label,'visual_policy','Social editorial design with an abstract identity; no human likeness is configured.',
   'brand_policy','No implied brand affiliation. Use only the configured association level.',
   'language',request_payload->>'language','audience',request_payload->'audience',
   'franchise',category.name,'objective',request_payload->>'objective','tone',tone_label,
   'disclosure',disclosure,'headline',headline,'cta',cta,'cta_type','READ_SOURCE',
   'brand_association_level',0,'max_brand_association_level',0,'min_slides',1,'max_slides',10,
   'creative_allowlist',jsonb_build_array(headline,cta),'community_policy',NULL
 );
 visual:=jsonb_build_object(
   'schema_version',1,'template_version','social-editorial-v2','display_name',request_payload->>'name',
   'required_disclosure',disclosure,'regular_font_path','backend/assets/fonts/Inter-Regular.ttf',
   'bold_font_path','backend/assets/fonts/Inter-Bold.ttf',
   'palette',jsonb_build_object('background','#F7F5EF','text','#132A2A','accent',category.accent),
   'width',1080,'height',1350,'margin',72,'headline_font_size',72,'body_font_size',48,
   'cta_font_size',32,'disclosure_font_size',26,'identity_font_size',32,'minimum_contrast_ratio',4.5
 );
 INSERT INTO public.influencers(id,tenant_id,slug,name)
 VALUES(iid,tenant,'creator-'||replace(iid::text,'-',''),request_payload->>'name');
 INSERT INTO public.missions(id,tenant_id,influencer_id,name,objective)
 VALUES(mid,tenant,iid,request_payload->>'name'||' editorial mission',request_payload->>'objective');
 INSERT INTO public.character_config_versions(id,tenant_id,influencer_id,version,schema_version,payload,content_hash)
 VALUES(cid,tenant,iid,1,1,config,private.visual_hash(config));
 INSERT INTO public.influencer_versions(id,tenant_id,influencer_id,character_config_version_id,version)
 VALUES(vid,tenant,iid,cid,1);
 INSERT INTO public.visual_config_versions(id,tenant_id,influencer_id,version,schema_version,payload,content_hash,font_hashes)
 VALUES(visual_id,tenant,iid,1,1,visual,private.visual_hash(visual),jsonb_build_object(
   'regular','40d692fce188e4471e2b3cba937be967878f631ad3ebbbdcd587687c7ebe0c82',
   'bold','288316099b1e0a47a4716d159098005eef7c0066921f34e3200393dbdb01947f'));
 INSERT INTO public.studio_creations(tenant_id,influencer_id,mission_id,influencer_version_id,
 character_config_version_id,visual_config_version_id,category_id,idempotency_key,schema_version,
 payload,input_hash,created_by,correlation_id)
 VALUES(tenant,iid,mid,vid,cid,visual_id,category.id,request_key,1,request_payload,
 private.visual_hash(request_payload),actor,correlation);
 RETURN iid;
 END $$;
REVOKE ALL ON FUNCTION public.studio_create_influencer(text,jsonb,uuid) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION public.studio_create_influencer(text,jsonb,uuid) TO mediaos_runtime;
