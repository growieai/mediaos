"use client";
import { useEffect, useRef, useState } from "react";
import Modal from "./Modal";
import Icon from "./Icons";
import { request, type Influencer, type Session, type Workflow } from "./types";

export type ManualClaimType = "" | "GENERAL_EVERGREEN" | "GRANT_OR_TIME_SENSITIVE";
type SourceRelationship = "" | "PRIMARY" | "SECONDARY" | "INTERNAL";
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
export default function SourceComposer({ session, creators, initialCreatorId, onClose, onCreated }: { session: Session; creators: Influencer[]; initialCreatorId: string; onClose: () => void; onCreated: (run: Workflow) => Promise<void> }) {
  const [creatorId, setCreatorId] = useState(initialCreatorId || creators[0]?.id || "");
  const [title, setTitle] = useState(""); const [origin, setOrigin] = useState(""); const [publisher, setPublisher] = useState("");
  const [raw, setRaw] = useState(""); const [excerpts, setExcerpts] = useState([""]); const [fixture, setFixture] = useState(false);
  const [claimType, setClaimType] = useState<ManualClaimType>(""); const [classification, setClassification] = useState<SourceRelationship>(""); const [generalConfirmed, setGeneralConfirmed] = useState(false);
  const [busy, setBusy] = useState(false); const [message, setMessage] = useState("");
  const lock = useRef(false); const abort = useRef<AbortController | null>(null); const saved = useRef(new Map<string, { key: string; capturedAt: string }>());
  const scope = `${session.tenant}:${session.token}:${initialCreatorId}`;
  const currentScope = useRef(scope); currentScope.current = scope;
  useEffect(() => {
    const controller = new AbortController(); abort.current = controller; lock.current = false; saved.current.clear();
    setCreatorId(creators.some(row => row.id === initialCreatorId) ? initialCreatorId : creators[0]?.id ?? "");
    setTitle(""); setOrigin(""); setPublisher(""); setRaw(""); setExcerpts([""]); setFixture(false); setBusy(false); setMessage("");
    setClaimType(""); setClassification(""); setGeneralConfirmed(false);
    return () => controller.abort();
  }, [scope, initialCreatorId]);
  async function submit() {
    const controller = abort.current;
    const active = () => currentScope.current === scope && abort.current === controller && !!controller && !controller.signal.aborted;
    if (lock.current || !active()) return;
    setMessage(""); const creator = creators.find(row => row.id === creatorId); if (!creator) return;
    try {
      if (!["PRIMARY", "SECONDARY", "INTERNAL"].includes(classification)) throw new Error("Choose how this source relates to the information.");
      const evidence = manualGeneralEvidence(raw, excerpts, claimType, generalConfirmed);
      const base = { influencer_id: creator.id, mission_id: creator.mission_id, source: { source_type: "MANUAL", title: title.trim(), publisher: publisher.trim(), origin: origin.trim(), canonical_url: /^https?:\/\//.test(origin.trim()) ? origin.trim() : null, raw_content: raw, classification, is_fixture: fixture, evidence, metadata: { manual_claim_classification: claimType, manual_classification_policy: "evergreen-review-v1", general_claim_declaration: "USER_CONFIRMED" } } };
      const signature = JSON.stringify(base);
      let attempt = saved.current.get(signature);
      if (!attempt) { attempt = { key: crypto.randomUUID(), capturedAt: new Date().toISOString() }; saved.current.set(signature, attempt); }
      lock.current = true; setBusy(true);
      const run = await request<Workflow>(session, "workflow-runs", "POST", { ...base, idempotency_key: attempt.key, source: { ...base.source, captured_at: attempt.capturedAt } }, controller?.signal);
      if (active()) await onCreated(run);
    } catch (error) { if (active()) setMessage(error instanceof Error ? error.message : "Could not save the source. Retry the same request to recover it."); }
    finally { if (active()) { lock.current = false; setBusy(false); } }
  }
  const needsStructuredEvidence = claimType === "GRANT_OR_TIME_SENSITIVE" || (claimType === "GENERAL_EVERGREEN" && !!manualEvidenceRisk(excerpts));
  return <Modal title="Create content" onClose={onClose} wide><div className="composer-heading"><span className="eyebrow">SOURCE → STORY</span><h2>Start with something worth sharing.</h2><p className="muted">Bring the source. Your creator brings the voice. A reviewer verifies the evidence before a draft is generated.</p></div>
    <form onSubmit={event => { event.preventDefault(); void submit(); }}><fieldset className="plain-fieldset" disabled={busy}>
      {message && <p className="notice error" role="alert">{message}</p>}
      <div className="composer-grid"><div className="form-stack"><div className="form-two"><label>Influencer<select value={creatorId} onChange={event => setCreatorId(event.target.value)}>{creators.map(row => <option key={row.id} value={row.id}>{row.name}</option>)}</select></label><label>Format<input value="Instagram carousel · 4:5" readOnly/></label></div>
      <label>Story / source title<input required maxLength={200} value={title} onChange={event => setTitle(event.target.value)} placeholder="What is this story about?"/></label>
      <div className="form-two"><label>Source URL or origin<input required maxLength={2048} value={origin} onChange={event => setOrigin(event.target.value)} placeholder="https://official-source…"/></label><label>Publisher<input required maxLength={200} value={publisher} onChange={event => setPublisher(event.target.value)} placeholder="Who published it?"/></label></div>
      <div className="form-two"><label>Claim type<select required value={claimType} onChange={event => { setClaimType(event.target.value as ManualClaimType); setGeneralConfirmed(false); setMessage(""); }}><option value="">Choose the kind of claims</option><option value="GENERAL_EVERGREEN">Evergreen general facts</option><option value="GRANT_OR_TIME_SENSITIVE">Funding, eligibility or time-sensitive facts</option></select></label><label>Source relationship<select required value={classification} onChange={event => { setClassification(event.target.value as SourceRelationship); setGeneralConfirmed(false); }}><option value="">Choose source relationship</option><option value="PRIMARY">Primary / original issuer</option><option value="SECONDARY">Secondary / summary or republication</option><option value="INTERNAL">Internal / workspace documentation</option></select></label></div>{needsStructuredEvidence && <p className="notice error" role="alert">{STRUCTURED_EVIDENCE_REQUIRED}</p>}<label>Original source text<textarea required rows={10} maxLength={100000} value={raw} onChange={event => { setRaw(event.target.value); setGeneralConfirmed(false); }} placeholder="Paste the original text you want your content to draw from."/></label></div>
      <aside className="evidence-composer"><div className="section-kicker"><Icon name="book"/><strong>Keep the facts connected.</strong></div><p className="muted small">Copy the exact passages that support your content. Each excerpt becomes a traceable fact.</p>{excerpts.map((excerpt, index) => <label key={index}>Evidence {index + 1}<textarea required rows={3} maxLength={2000} value={excerpt} onChange={event => { setExcerpts(old => old.map((value, at) => at === index ? event.target.value : value)); setGeneralConfirmed(false); }} placeholder="An exact passage from the source text"/>{excerpts.length > 1 && <button type="button" className="text-button" onClick={() => { setExcerpts(old => old.filter((_, at) => at !== index)); setGeneralConfirmed(false); }}>Remove excerpt</button>}</label>)}
      <button className="button secondary compact" type="button" disabled={excerpts.length >= 20} onClick={() => { setExcerpts(old => [...old, ""]); setGeneralConfirmed(false); }}><Icon name="plus" size={15}/>Add excerpt</button>
      {claimType === "GENERAL_EVERGREEN" && <label className="checkbox-row"><input type="checkbox" checked={generalConfirmed} disabled={needsStructuredEvidence} onChange={event => setGeneralConfirmed(event.target.checked)}/>I confirm these excerpts are evergreen general facts, with no funding, eligibility or time-sensitive conditions.</label>}<p className="small muted">The form flags common funding and date wording. It cannot understand every claim; a reviewer must verify the classification and source.</p><label className="checkbox-row"><input type="checkbox" checked={fixture} onChange={event => setFixture(event.target.checked)}/>This is test or demonstration evidence.</label>{fixture && <p className="small muted">Test sources can be explored, but will be blocked from publication.</p>}</aside></div>
      <footer className="wizard-actions"><p className="small muted">Saving does not generate or publish content.</p><button className="button primary" disabled={!creatorId || busy || !classification || claimType !== "GENERAL_EVERGREEN" || !generalConfirmed || needsStructuredEvidence}>{busy ? "Saving source…" : "Save source for review"}<Icon name="arrow" size={17}/></button></footer>
    </fieldset></form>
  </Modal>;
}
