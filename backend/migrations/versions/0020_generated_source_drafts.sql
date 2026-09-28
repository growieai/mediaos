-- Writing assistance never establishes factual truth. Existing immutable-source,
-- fixture verification, QA and guarded approval rules remain authoritative.
ALTER TABLE public.source_snapshots DROP CONSTRAINT source_snapshots_source_type_check;
ALTER TABLE public.source_snapshots ADD CONSTRAINT source_snapshots_source_type_check
 CHECK (source_type IN ('MANUAL','OFFICIAL','SECONDARY','GENERATED'));
ALTER TABLE public.source_snapshots ADD CONSTRAINT generated_source_is_nonpublishable
 CHECK (
   NOT (source_type='GENERATED' OR lower(btrim(origin)) LIKE 'generated:%'
        OR metadata ? 'source_draft_policy')
   OR (is_fixture AND classification='INTERNAL')
 );
