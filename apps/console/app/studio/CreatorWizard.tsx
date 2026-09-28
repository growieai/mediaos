"use client";
import { useEffect, useRef, useState } from "react";
import Icon, { categoryIcon } from "./Icons";
import Modal from "./Modal";
import { initials, request, type Category, type Influencer, type Session } from "./types";

export type CreatorFields = { name: string; category_id: string; language: string; tone: string; audience: string; objective: string };
export function creatorPayload(fields: CreatorFields) {
  return { name: fields.name.trim(), category_id: fields.category_id, language: fields.language, tone: fields.tone,
    audience: fields.audience.split(",").map(value => value.trim()).filter(Boolean), objective: fields.objective.trim() };
}
export function creatorFieldError(fields: CreatorFields, step: number) {
  const value = creatorPayload(fields);
  const length = (text: string) => Array.from(text).length;
  const control = (text: string) => /[\u0000-\u001f\u007f]/.test(text);
  if (step === 1) {
    if (!value.name || length(value.name) > 100 || control(value.name)) return "Use a creator name of 1–100 characters on one line.";
    if (!value.audience.length || value.audience.length > 8) return "Add 1–8 audience groups, separated by commas.";
    if (value.audience.some(group => length(group) > 100 || control(group))) return "Keep each audience group to 100 characters on one line.";
    if (new Set(value.audience).size !== value.audience.length) return "Use each audience group only once.";
  }
  if (step === 2 && (length(value.objective) < 10 || length(value.objective) > 500 || control(value.objective))) return "Write a mission of 10–500 characters on one line, without line breaks.";
  return "";
}
export default function CreatorWizard({ session, categories, initialCategory = "", onClose, onCreated }: { session: Session; categories: Category[]; initialCategory?: string; onClose: () => void; onCreated: (creator: Influencer) => Promise<void> }) {
  const [step, setStep] = useState(0);
  const [fields, setFields] = useState<CreatorFields>({ name: "", category_id: initialCategory, language: "en", tone: "CLEAR", audience: "", objective: "" });
  const [busy, setBusy] = useState(false); const [error, setError] = useState("");
  const lock = useRef(false); const abort = useRef<AbortController | null>(null); const keys = useRef(new Map<string, string>());
  const scope = `${session.tenant}:${session.token}:${initialCategory}`;
  const currentScope = useRef(scope); currentScope.current = scope;
  useEffect(() => {
    const controller = new AbortController(); abort.current = controller; lock.current = false; keys.current.clear();
    setStep(0); setFields({ name: "", category_id: initialCategory, language: "en", tone: "CLEAR", audience: "", objective: "" }); setBusy(false); setError("");
    return () => controller.abort();
  }, [scope, initialCategory]);
  const category = categories.find(row => row.id === fields.category_id);
  function edit(change: Partial<CreatorFields>) { setFields(old => ({ ...old, ...change })); setError(""); }
  const validationError = step === 0 ? category ? "" : "Choose a content category." : creatorFieldError(fields, step);
  const valid = !validationError;
  async function submit() {
    const controller = abort.current;
    const active = () => currentScope.current === scope && abort.current === controller && !!controller && !controller.signal.aborted;
    if (!valid || lock.current || !active()) return;
    const problem = creatorFieldError(fields, 1) || creatorFieldError(fields, 2);
    if (!category || problem) { setError(problem || "Choose a content category."); return; }
    lock.current = true; setBusy(true); setError("");
    const payload = creatorPayload(fields); const signature = JSON.stringify(payload);
    if (!keys.current.has(signature)) keys.current.set(signature, crypto.randomUUID());
    try {
      const saved = await request<Influencer>(session, "studio/influencers", "POST", { ...payload, idempotency_key: keys.current.get(signature) }, controller?.signal);
      if (active()) await onCreated(saved);
    } catch (cause) { if (active()) setError(cause instanceof Error ? cause.message : "Could not confirm creation. Retry this same request to recover it."); }
    finally { if (active()) { lock.current = false; setBusy(false); } }
  }
  return <Modal title="Create an influencer" onClose={onClose} wide>
    <div className="wizard-layout"><div className="wizard-main"><div className="step-label">STEP {step + 1} OF 3 <span>{["Choose a world", "Build a personality", "Give it a purpose"][step]}</span></div>
      <h2>{["What’s their thing?", "Meet your next creator.", "Make every post matter."][step]}</h2>
      <p className="muted">{["Pick a content category. It becomes the foundation for their audience and editorial direction.", "A memorable name, a clear audience, and a voice that feels like them.", "Set a clear mission. Sources, quality checks and your approval stay part of every workflow."][step]}</p>
      {error && <p className="notice error" role="alert">{error}</p>}
      {step > 0 && validationError && <p className="small muted" role="status">{validationError}</p>}
      <form onSubmit={event => { event.preventDefault(); if (step < 2 && valid) setStep(step + 1); else if (step === 2) void submit(); }}>
        <fieldset disabled={busy} className="plain-fieldset">
          {step === 0 && <div className="category-grid">{categories.map(row => <button type="button" key={row.id} className={`category-option category-${row.id} ${fields.category_id === row.id ? "selected" : ""}`} aria-pressed={fields.category_id === row.id} onClick={() => edit({ category_id: row.id })}><span className="category-symbol"><Icon name={categoryIcon[row.id]}/></span><strong>{row.name}</strong><small>{row.description}</small>{fields.category_id === row.id && <span className="selected-tick"><Icon name="check" size={14}/></span>}</button>)}</div>}
          {step === 1 && <div className="form-stack"><label>Creator name<input autoFocus required minLength={1} maxLength={100} placeholder="Give your influencer a name" value={fields.name} onChange={event => edit({ name: event.target.value })}/></label><label>Who is their content for?<input required maxLength={815} placeholder="e.g. Independent cafés, first-time founders" value={fields.audience} onChange={event => edit({ audience: event.target.value })}/><small>Separate audience groups with commas.</small></label><div className="form-two"><label>Content language<select value={fields.language} onChange={event => edit({ language: event.target.value })}><option value="en">English</option><option value="es">Spanish</option></select></label><label>Voice & tone<select value={fields.tone} onChange={event => edit({ tone: event.target.value })}><option value="CLEAR">Clear & knowledgeable</option><option value="WARM">Warm & approachable</option><option value="BOLD">Bold & direct</option></select></label></div></div>}
          {step === 2 && <div className="form-stack"><label>Editorial mission<textarea autoFocus required minLength={10} maxLength={500} rows={5} placeholder="Help independent business owners discover practical opportunities and understand what to do next." value={fields.objective} onChange={event => edit({ objective: event.target.value })}/></label><div className="notice"><Icon name="shield"/><div><strong>Built with clear boundaries</strong><p>Your influencer is disclosed as AI. Factual content needs source evidence; nothing is posted without approval.</p></div></div><p className="muted small">This creates a saved identity and editorial configuration. Portrait and voice generation are separate setup steps.</p></div>}
          <div className="wizard-actions">{step > 0 && <button type="button" className="button secondary" onClick={() => setStep(step - 1)}>Back</button>}<span/>
            <button className="button primary" disabled={!valid || busy}>{busy ? "Creating…" : step === 2 ? "Create influencer" : "Continue"}<Icon name={step === 2 ? "spark" : "arrow"} size={17}/></button>
          </div>
        </fieldset>
      </form>
    </div><aside className={`creator-live-preview category-${fields.category_id || "business"}`}><span className="eyebrow">YOUR CREATOR</span><div className="preview-monogram">{fields.name.trim() ? initials(fields.name) : <Icon name="spark" size={52}/>}</div><span className="pill light">{category?.name ?? "Choose a category"}</span><h3>{fields.name.trim() || "A new perspective."}</h3><p>{fields.objective.trim() || "Your voice. Your audience. A world of possibilities."}</p><div className="preview-meta"><span>{fields.language === "es" ? "ES" : "EN"}</span><span>{fields.tone.toLowerCase()} voice</span><span>AI creator</span></div><small>Identity preview · No generated portrait yet</small></aside></div>
  </Modal>;
}
