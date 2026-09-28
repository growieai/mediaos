"use client";
import { useEffect, useRef, useState } from "react";
import Modal from "./Modal";
import Icon from "./Icons";
import { request, type Influencer, type Session, type Workflow } from "./types";

export type ManualClaimType = "" | "GENERAL_EVERGREEN" | "GRANT_OR_TIME_SENSITIVE";
type SourceRelationship = "" | "PRIMARY" | "SECONDARY" | "INTERNAL";
type SourceDraft = { schema_version: 1; provider: "mock"; mode: "MOCK"; cost: 0; notice: string; influencer_id: string; mission_id: string; language: string; title: string; publisher: string; raw_content: string; source_type: "GENERATED"; classification: "INTERNAL"; is_fixture: true; origin: string; metadata: { source_draft_policy: "studio-source-draft-v1" } };
export function checkedSourceDraft(value: unknown): SourceDraft {
  const object = (item: unknown): item is Record<string, unknown> => !!item && typeof item === "object" && !Array.isArray(item);
  const plain = (item: unknown, max: number): item is string => typeof item === "string" && !!item.trim() && item === item.trim() && Array.from(item).length <= max && !/[\u0000-\u001f\u007f]/.test(item);
  const uuid = (item: unknown) => typeof item === "string" && /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i.test(item);
  if (!object(value) || value.schema_version !== 1 || value.provider !== "mock" || value.mode !== "MOCK" || value.cost !== 0 || !plain(value.notice, 500) || !uuid(value.influencer_id) || !uuid(value.mission_id) || !["en", "es"].includes(String(value.language)) || !plain(value.title, 200) || value.publisher !== "Media OS draft assistant (mock)" || typeof value.raw_content !== "string" || !value.raw_content.trim() || Array.from(value.raw_content).length > 100000 || /[\u0000-\u0008\u000b\u000c\u000e-\u001f\u007f]/.test(value.raw_content) || value.source_type !== "GENERATED" || value.classification !== "INTERNAL" || value.is_fixture !== true || typeof value.origin !== "string" || !/^generated:studio-source-draft-v1:[0-9a-f]{64}$/.test(value.origin) || !object(value.metadata) || value.metadata.source_draft_policy !== "studio-source-draft-v1" || Object.keys(value.metadata).length !== 1) throw new Error("The source draft could not be validated. Your writing is unchanged.");
  return value as SourceDraft;
}
const STRUCTURED_EVIDENCE_REQUIRED = "Funding, eligibility and time-sensitive claims need structured verification. Use Discover opportunities for supported official sources, or the typed evidence workflow for required dates and eligibility metadata. This form cannot save those claims as general facts.";

// Conservative friction for this small evergreen form, not a semantic classifier or QA replacement.
// Normalization is used only for detection; evidence always retains the exact original text.
export function manualEvidenceRisk(excerpts: string[]) {
  const value = excerpts.join(" ").normalize("NFKD").replace(/[\u0300-\u036f]/g, "").toLowerCase();
  const funding = /\b(?:grants?|subsid(?:y|ies)|fund(?:ing|ed)|loans?|financing|financial aid|reimburse\w*|eligib\w*|applicants?|applications?|apply|subvencion(?:es)?|ayudas?|financi\w*|prestamos?|creditos?|solicitud(?:es)?|solicitantes?|elegib\w*|beneficiari\w*|de minimis)\b/;
  const time = /\b(?:deadline|expir\w*|opening date|closing date|valid until|available until|today|tomorrow|yesterday|next week|last week|this week|currently|as of|vigente\w*|actualmente|hoy|manana|ayer|vence|vencimiento|caduca\w*|plazos?|fecha limite|fecha de inicio|fecha de cierre|hasta el|desde el)\b/;
  const dates = /\b(?:19|20)\d{2}\b|\b\d{1,2}[\/-]\d{1,2}(?:[\/-]\d{2,4})?\b|\b(?:january|february|march|april|june|july|august|september|october|november|december|enero|febrero|marzo|abril|mayo|junio|julio|agosto|septiembre|setiembre|octubre|noviembre|diciembre)\b|\b(?:by|until|before|after|from|through|in) may\b|\bmay \d{1,2}\b/;
  const amount = /[€$£]\s*\d|\d[\d.,]*\s*(?:[€$£%]|\b(?:eur|usd|gbp|euros?|dollars?|pounds?)\b)|\b(?:eur|usd|gbp)\s*\d/;
  if (funding.test(value) || amount.test(value)) return "FUNDING_OR_ELIGIBILITY";
  if (time.test(value) || dates.test(value)) return "DATES_OR_TIME_SENSITIVE";
  return null;
}

export function evidenceFrom(raw: string, excerpts: string[]) {
  const seen = new Set<string>();
  return excerpts.map(value => value.trim()).filter(Boolean).map(statement => {
    const utf16Start = raw.indexOf(statement);
    if (utf16Start < 0) throw new Error("Each factual excerpt must appear exactly in the source text. Copy the original wording.");
    if (seen.has(statement)) throw new Error("Use each source excerpt only once.");
    seen.add(statement);
    const length = Array.from(statement).length;
    if (length > 2000) throw new Error("Keep each excerpt under 2,000 characters.");
    // Python/PostgreSQL spans use Unicode code points, not JavaScript UTF-16 units.
    const start = Array.from(raw.slice(0, utf16Start)).length;
    return { start, end: start + length, statement };
  });
}
export function manualGeneralEvidence(raw: string, excerpts: string[], claimType: ManualClaimType, confirmed: boolean) {
  if (!claimType) throw new Error("Choose the kind of claims before saving a source.");
  if (claimType !== "GENERAL_EVERGREEN") throw new Error(STRUCTURED_EVIDENCE_REQUIRED);
  const evidence = evidenceFrom(raw, excerpts);
  if (!evidence.length) throw new Error("Add at least one exact source excerpt.");
  if (manualEvidenceRisk(evidence.map(item => item.statement))) throw new Error(STRUCTURED_EVIDENCE_REQUIRED);
  if (!confirmed) throw new Error("Confirm that the selected excerpts are evergreen general facts without funding, eligibility or time-sensitive conditions.");
  return evidence.map(item => ({ ...item, fact_type: "GENERAL" as const }));
}
export default function SourceComposer({ session, creators, initialCreatorId, mockMode = true, onClose, onCreated }: { session: Session; creators: Influencer[]; initialCreatorId: string; mockMode?: boolean; onClose: () => void; onCreated: (run: Workflow) => Promise<void> }) {
  const [creatorId, setCreatorId] = useState(initialCreatorId || creators[0]?.id || "");
  const [title, setTitle] = useState(""); const [origin, setOrigin] = useState(""); const [publisher, setPublisher] = useState("");
  const [raw, setRaw] = useState(""); const [excerpts, setExcerpts] = useState([""]); const [fixture, setFixture] = useState(false);
  const [claimType, setClaimType] = useState<ManualClaimType>(""); const [classification, setClassification] = useState<SourceRelationship>(""); const [generalConfirmed, setGeneralConfirmed] = useState(false);
  const [busy, setBusy] = useState(false); const [message, setMessage] = useState("");
  const [drafting, setDrafting] = useState(false); const [draftError, setDraftError] = useState("");
  const [draft, setDraft] = useState<{ context: string; controller: AbortController; data: SourceDraft } | null>(null);
  const [generated, setGenerated] = useState<SourceDraft | null>(null);
  const draftRequest = useRef<AbortController | null>(null);
  const lock = useRef(false); const abort = useRef<AbortController | null>(null); const saved = useRef(new Map<string, { key: string; capturedAt: string }>());
  const scope = `${session.tenant}:${session.token}:${initialCreatorId}:${mockMode}`;
  const currentScope = useRef(scope); currentScope.current = scope;
  const creator = creators.find(row => row.id === creatorId);
  const draftContext = JSON.stringify({ scope, creator, title, raw, origin, publisher, classification, claimType, excerpts, fixture, generalConfirmed });
  const currentDraftContext = useRef(draftContext); currentDraftContext.current = draftContext;
  useEffect(() => {
    const controller = new AbortController(); abort.current = controller; lock.current = false; saved.current.clear();
    setCreatorId(creators.some(row => row.id === initialCreatorId) ? initialCreatorId : creators[0]?.id ?? "");
    setTitle(""); setOrigin(""); setPublisher(""); setRaw(""); setExcerpts([""]); setFixture(false); setBusy(false); setMessage("");
    setClaimType(""); setClassification(""); setGeneralConfirmed(false);
    draftRequest.current?.abort(); draftRequest.current = null; setDrafting(false); setDraft(null); setGenerated(null); setDraftError("");
    return () => { controller.abort(); draftRequest.current?.abort(); };
  }, [scope, initialCreatorId]);
  function clearDraft() { draftRequest.current?.abort(); draftRequest.current = null; setDrafting(false); setDraft(null); setDraftError(""); }
  useEffect(() => { clearDraft(); }, [draftContext]);
  function startRealSource() {
    if (lock.current || currentScope.current !== scope || abort.current?.signal.aborted || currentDraftContext.current !== draftContext) return;
    clearDraft(); setGenerated(null); setRaw(""); setExcerpts([""]); setOrigin(""); setPublisher(""); setClassification(""); setClaimType(""); setFixture(false); setGeneralConfirmed(false); setMessage("");
  }
  async function suggest() {
    const lifetime = abort.current;
    if (!mockMode || !creator || raw.trim() || !title.trim() || Array.from(title.trim()).length > 200 || /[\u0000-\u001f\u007f]/.test(title) || lock.current || draftRequest.current && !draftRequest.current.signal.aborted && !draft || !lifetime || lifetime.signal.aborted || currentScope.current !== scope || currentDraftContext.current !== draftContext) return;
    clearDraft(); const controller = new AbortController(); draftRequest.current = controller; setDrafting(true);
    const active = () => !lifetime.signal.aborted && !controller.signal.aborted && abort.current === lifetime && draftRequest.current === controller && currentScope.current === scope && currentDraftContext.current === draftContext;
    try {
      const response = await request<unknown>(session, "studio/source-drafts", "POST", { influencer_id: creator.id, title: title.trim() }, controller.signal);
      if (!active()) return;
      const data = checkedSourceDraft(response);
      if (data.influencer_id !== creator.id || data.mission_id !== creator.mission_id || data.title !== title.trim() || data.language !== creator.language) throw new Error("The source draft no longer matches this story. Generate a fresh draft.");
      setDraft({ context: draftContext, controller, data });
    } catch (cause) { if (active()) { setDraftError(cause instanceof Error ? cause.message : "Could not prepare a source draft. Your writing is unchanged."); draftRequest.current = null; } }
    finally { if (active() || draftRequest.current === null && currentDraftContext.current === draftContext && !lifetime.signal.aborted) setDrafting(false); }
  }
  function useDraft() {
    if (!draft || raw.trim() || lock.current || draft.context !== currentDraftContext.current || currentScope.current !== scope || draft.controller !== draftRequest.current || draft.controller.signal.aborted || abort.current?.signal.aborted) return;
    const data = draft.data; clearDraft(); setGenerated(data); setRaw(data.raw_content); setOrigin(data.origin); setPublisher(data.publisher); setClassification("INTERNAL"); setFixture(true); setExcerpts([""]); setClaimType(""); setGeneralConfirmed(false); setMessage("");
  }
  async function submit() {
    const controller = abort.current;
    const active = () => currentScope.current === scope && abort.current === controller && !!controller && !controller.signal.aborted;
    if (lock.current || !active()) return;
    setMessage(""); const creator = creators.find(row => row.id === creatorId); if (!creator) return;
    try {
      if (!["PRIMARY", "SECONDARY", "INTERNAL"].includes(classification)) throw new Error("Choose how this source relates to the information.");
      const evidence = manualGeneralEvidence(raw, excerpts, claimType, generalConfirmed);
      const base = { influencer_id: creator.id, mission_id: creator.mission_id, source: { source_type: generated ? "GENERATED" : "MANUAL", title: title.trim(), publisher: generated?.publisher ?? publisher.trim(), origin: generated?.origin ?? origin.trim(), canonical_url: !generated && /^https?:\/\//.test(origin.trim()) ? origin.trim() : null, raw_content: raw, classification: generated ? "INTERNAL" : classification, is_fixture: !!generated || fixture, evidence, metadata: { manual_claim_classification: claimType, manual_classification_policy: "evergreen-review-v1", general_claim_declaration: "USER_CONFIRMED", ...(generated ? generated.metadata : {}) } } };
      const signature = JSON.stringify(base);
      let attempt = saved.current.get(signature);
      if (!attempt) { attempt = { key: crypto.randomUUID(), capturedAt: new Date().toISOString() }; saved.current.set(signature, attempt); }
      clearDraft(); lock.current = true; setBusy(true);
      const run = await request<Workflow>(session, "workflow-runs", "POST", { ...base, idempotency_key: attempt.key, source: { ...base.source, captured_at: attempt.capturedAt } }, controller?.signal);
      if (active()) await onCreated(run);
    } catch (error) { if (active()) setMessage(error instanceof Error ? error.message : "Could not save the source. Retry the same request to recover it."); }
    finally { if (active()) { lock.current = false; setBusy(false); } }
  }
  const needsStructuredEvidence = claimType === "GRANT_OR_TIME_SENSITIVE" || (claimType === "GENERAL_EVERGREEN" && !!manualEvidenceRisk(excerpts));
  return <Modal title="Create content" onClose={onClose} wide><div className="composer-heading"><span className="eyebrow">SOURCE → STORY</span><h2>Start with something worth sharing.</h2><p className="muted">Bring a source, or start with a topic and generate a draft. Factual content still needs real evidence and a reviewer’s approval.</p></div>
    <form onSubmit={event => { event.preventDefault(); void submit(); }}><fieldset className="plain-fieldset" disabled={busy}>
      {message && <p className="notice error" role="alert">{message}</p>}
      <div className="composer-grid"><div className="form-stack"><div className="form-two"><label>Influencer<select value={creatorId} onChange={event => { clearDraft(); setCreatorId(event.target.value); }}>{creators.map(row => <option key={row.id} value={row.id}>{row.name}</option>)}</select></label><label>Format<input value="Instagram carousel · 4:5" readOnly/></label></div>
      <label>Story / source title<input required maxLength={200} value={title} onChange={event => { clearDraft(); setTitle(event.target.value); }} placeholder="What is this story about?"/></label>
      <section className="onboarding-assistant source-draft-assistant" aria-label="Source draft assistant">
        <div className="assistant-heading"><div><span className="eyebrow">NO SOURCE TEXT YET?</span><strong>Turn your topic into a starting draft.</strong></div><button type="button" className="button secondary" disabled={busy || drafting || !!raw.trim() || !title.trim() || !creator || !mockMode} onClick={() => void suggest()}><Icon name="spark" size={15}/>{drafting ? "Drafting…" : "Generate draft"}</button></div>
        <p className="small muted">{mockMode ? "Mock preview · an editable starter from your topic and creator. No AI calls or charges. Generated text is not verified evidence." : "Live source drafting is not configured. Paste an original source to continue."}</p>
        {!title.trim() && <p className="small muted">Add a story title above to get started.</p>}
        {!!raw.trim() && !generated && <p className="small muted">Your source text is already here. Drafting is available when this field is empty.</p>}
        {drafting && <p role="status" className="small muted">Preparing a starting point for {creator?.name}…</p>}
        {draftError && <p role="alert" className="notice error">{draftError}</p>}
        {draft && draft.context === draftContext && <article className="onboarding-draft"><span className="pill warning">Generated draft · not source evidence</span><p className="source-draft-copy">{draft.data.raw_content}</p><p className="small muted">{draft.data.notice}</p><button type="button" className="text-button" onClick={useDraft}>Use draft<Icon name="arrow" size={15}/></button></article>}
        {generated && <div role="status"><p className="small"><strong>Generated draft added.</strong> Edit the text below. It stays test material and cannot be verified or approved for publication.</p><button type="button" className="text-button" onClick={startRealSource}>Start again with real source</button><p className="small muted">Starting again clears the draft, excerpts and source details so you can paste original evidence.</p></div>}
      </section>
      <div className="form-two"><label>Source URL or origin<input required maxLength={2048} value={origin} readOnly={!!generated} onChange={event => setOrigin(event.target.value)} placeholder="https://official-source…"/></label><label>Publisher<input required maxLength={200} value={publisher} readOnly={!!generated} onChange={event => setPublisher(event.target.value)} placeholder="Who published it?"/></label></div>
      <div className="form-two"><label>Claim type<select required value={claimType} onChange={event => { setClaimType(event.target.value as ManualClaimType); setGeneralConfirmed(false); setMessage(""); }}><option value="">Choose the kind of claims</option><option value="GENERAL_EVERGREEN">Evergreen general facts</option><option value="GRANT_OR_TIME_SENSITIVE">Funding, eligibility or time-sensitive facts</option></select></label><label>Source relationship<select required disabled={!!generated} value={classification} onChange={event => { setClassification(event.target.value as SourceRelationship); setGeneralConfirmed(false); }}><option value="">Choose source relationship</option><option value="PRIMARY">Primary / original issuer</option><option value="SECONDARY">Secondary / summary or republication</option><option value="INTERNAL">Internal / workspace documentation</option></select></label></div>{needsStructuredEvidence && <p className="notice error" role="alert">{STRUCTURED_EVIDENCE_REQUIRED}</p>}<label>Original source text<textarea required rows={10} maxLength={100000} value={raw} onChange={event => { clearDraft(); setRaw(event.target.value); setGeneralConfirmed(false); }} placeholder="Paste the original text you want your content to draw from."/></label></div>
      <aside className="evidence-composer"><div className="section-kicker"><Icon name="book"/><strong>Keep the facts connected.</strong></div><p className="muted small">{generated ? "Copy a draft excerpt only to explore the test workflow. Generated passages do not establish facts or satisfy publication evidence." : "Copy the exact passages that support your content. Each excerpt becomes a traceable fact."}</p>{excerpts.map((excerpt, index) => <label key={index}>Evidence {index + 1}<textarea required rows={3} maxLength={2000} value={excerpt} onChange={event => { setExcerpts(old => old.map((value, at) => at === index ? event.target.value : value)); setGeneralConfirmed(false); }} placeholder="An exact passage from the source text"/>{excerpts.length > 1 && <button type="button" className="text-button" onClick={() => { setExcerpts(old => old.filter((_, at) => at !== index)); setGeneralConfirmed(false); }}>Remove excerpt</button>}</label>)}
      <button className="button secondary compact" type="button" disabled={excerpts.length >= 20} onClick={() => { setExcerpts(old => [...old, ""]); setGeneralConfirmed(false); }}><Icon name="plus" size={15}/>Add excerpt</button>
      {claimType === "GENERAL_EVERGREEN" && <label className="checkbox-row"><input type="checkbox" checked={generalConfirmed} disabled={needsStructuredEvidence} onChange={event => setGeneralConfirmed(event.target.checked)}/>I confirm these excerpts are evergreen general facts, with no funding, eligibility or time-sensitive conditions.</label>}<p className="small muted">The form flags common funding and date wording. It cannot understand every claim; a reviewer must verify the classification and source.</p><label className="checkbox-row"><input type="checkbox" checked={!!generated || fixture} disabled={!!generated} onChange={event => setFixture(event.target.checked)}/>This is test or demonstration evidence.</label>{(generated || fixture) && <p className="small muted">Test sources can be explored, but will be blocked from publication.</p>}</aside></div>
      <footer className="wizard-actions"><p className="small muted">Saving creates a source review record. It does not publish content.</p><button className="button primary" disabled={!creatorId || busy || !classification || claimType !== "GENERAL_EVERGREEN" || !generalConfirmed || needsStructuredEvidence}>{busy ? "Saving source…" : "Save source for review"}<Icon name="arrow" size={17}/></button></footer>
    </fieldset></form>
  </Modal>;
}
