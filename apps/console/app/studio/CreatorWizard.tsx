"use client";
import { useEffect, useRef, useState } from "react";
import Icon, { categoryIcon } from "./Icons";
import Modal from "./Modal";
import { initials, request, type Category, type Influencer, type Session } from "./types";

export type CreatorFields = { name: string; category_id: string; language: string; tone: string; audience: string; objective: string };
export type OnboardingSuggestion = { id: string; label: string; name: string; audience: string[]; objective: string };
type OnboardingDrafts = { schema_version: 1; provider: "mock"; mode: "MOCK"; cost: 0; notice: string; suggestions: OnboardingSuggestion[] };
export function checkedOnboardingDrafts(value: unknown): OnboardingDrafts {
  const object = (item: unknown): item is Record<string, unknown> => !!item && typeof item === "object" && !Array.isArray(item);
  const plain = (item: unknown, min: number, max: number): item is string => typeof item === "string" && item === item.trim() && Array.from(item).length >= min && Array.from(item).length <= max && !/[\u0000-\u001f\u007f]/.test(item);
  if (!object(value) || value.schema_version !== 1 || value.provider !== "mock" || value.mode !== "MOCK" || value.cost !== 0 || !plain(value.notice, 1, 500) || !Array.isArray(value.suggestions) || value.suggestions.length !== 3) throw new Error("The draft suggestions could not be validated. Your entries are unchanged.");
  const ids = new Set<string>();
  for (const item of value.suggestions) {
    if (!object(item) || typeof item.id !== "string" || !["practical", "explainer", "community"].includes(item.id) || ids.has(item.id) || !plain(item.label, 1, 100) || !plain(item.name, 1, 100) || !plain(item.objective, 10, 500) || !Array.isArray(item.audience) || item.audience.length < 1 || item.audience.length > 8 || item.audience.some(group => !plain(group, 1, 100) || group.includes(",")) || new Set(item.audience).size !== item.audience.length) throw new Error("The draft suggestions could not be validated. Your entries are unchanged.");
    ids.add(item.id);
  }
  return value as OnboardingDrafts;
}
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
export default function CreatorWizard({ session, categories, initialCategory = "", mockMode = true, onClose, onCreated }: { session: Session; categories: Category[]; initialCategory?: string; mockMode?: boolean; onClose: () => void; onCreated: (creator: Influencer) => Promise<void> }) {
  const [step, setStep] = useState(0);
  const [fields, setFields] = useState<CreatorFields>({ name: "", category_id: initialCategory, language: "en", tone: "CLEAR", audience: "", objective: "" });
  const [busy, setBusy] = useState(false); const [error, setError] = useState("");
  const [drafting, setDrafting] = useState(false); const [draftError, setDraftError] = useState("");
  const [drafts, setDrafts] = useState<{ context: string; controller: AbortController; data: OnboardingDrafts } | null>(null);
  const [draftNotice, setDraftNotice] = useState("");
  const draftRequest = useRef<AbortController | null>(null);
  const lock = useRef(false); const abort = useRef<AbortController | null>(null); const keys = useRef(new Map<string, string>());
  const scope = `${session.tenant}:${session.token}:${initialCategory}:${mockMode}`;
  const currentScope = useRef(scope); currentScope.current = scope;
  const draftContext = JSON.stringify({ scope, step, fields });
  const currentDraftContext = useRef(draftContext); currentDraftContext.current = draftContext;
  useEffect(() => {
    const controller = new AbortController(); abort.current = controller; lock.current = false; keys.current.clear();
    setStep(0); setFields({ name: "", category_id: initialCategory, language: "en", tone: "CLEAR", audience: "", objective: "" }); setBusy(false); setError("");
    draftRequest.current?.abort(); draftRequest.current = null; setDrafting(false); setDrafts(null); setDraftError(""); setDraftNotice("");
    return () => { controller.abort(); draftRequest.current?.abort(); };
  }, [scope, initialCategory]);
  const category = categories.find(row => row.id === fields.category_id);
  function clearDrafts() { draftRequest.current?.abort(); draftRequest.current = null; setDrafts(null); setDrafting(false); setDraftError(""); setDraftNotice(""); }
  function edit(change: Partial<CreatorFields>) { clearDrafts(); setFields(old => ({ ...old, ...change })); setError(""); }
  function moveStep(next: number) { clearDrafts(); setStep(next); }
  const validationError = step === 0 ? category ? "" : "Choose a content category." : creatorFieldError(fields, step);
  const valid = !validationError;
  async function suggest() {
    const lifetime = abort.current;
    if (!mockMode || !category || lock.current || drafting || !lifetime || lifetime.signal.aborted || currentScope.current !== scope || currentDraftContext.current !== draftContext || draftRequest.current && !draftRequest.current.signal.aborted && !drafts) return;
    const payload = creatorPayload(fields);
    const problem = creatorFieldError({ ...fields, name: payload.name || "New creator", audience: payload.audience.length ? fields.audience : "New audience" }, 1);
    if (problem) { setDraftError(problem); return; }
    clearDrafts(); const controller = new AbortController(); draftRequest.current = controller; setDrafting(true);
    const active = () => !lifetime.signal.aborted && !controller.signal.aborted && abort.current === lifetime && draftRequest.current === controller && currentScope.current === scope && currentDraftContext.current === draftContext;
    try {
      const response = await request<unknown>(session, "studio/onboarding-drafts", "POST", { category_id: fields.category_id, language: fields.language, tone: fields.tone, ...(payload.name ? { name: payload.name } : {}), audience: payload.audience }, controller.signal);
      if (!active()) return;
      const data = checkedOnboardingDrafts(response);
      setDrafts({ context: draftContext, controller, data });
    } catch (cause) { if (active()) { setDraftError(cause instanceof Error ? cause.message : "Could not suggest a draft. Your entries are unchanged."); draftRequest.current = null; } }
    finally { if (active() || draftRequest.current === null && currentDraftContext.current === draftContext && !lifetime.signal.aborted) setDrafting(false); }
  }
  function useDraft(item: OnboardingSuggestion) {
    if (!drafts || drafts.context !== currentDraftContext.current || currentScope.current !== scope || drafts.controller !== draftRequest.current || drafts.controller.signal.aborted || abort.current?.signal.aborted || lock.current || !drafts.data.suggestions.includes(item)) return;
    edit(step === 2 ? { objective: item.objective } : { name: fields.name.trim() || item.name, audience: item.audience.join(", ") });
    setDraftNotice(step === 2 ? "Mission draft added. Make it yours before creating." : "Starting point added. Edit the name and audience to make them yours.");
  }
  function draftAssistant() {
    return <section className="onboarding-assistant" aria-label={step === 2 ? "Editorial mission assistant" : "Creator starting point assistant"}>
      <div className="assistant-heading"><div><span className="eyebrow">A LITTLE HELP GETTING STARTED</span><strong>{step === 2 ? "Turn your direction into a mission." : "Find a name and an audience."}</strong></div><button type="button" className="button secondary" disabled={busy || drafting || !mockMode} onClick={() => void suggest()}><Icon name="spark" size={15}/>{drafting ? "Drafting ideas…" : step === 2 ? "Draft mission with AI" : "Suggest with AI"}</button></div>
      <p className="small muted">{mockMode ? "Mock preview · category-based starter drafts. No AI calls or charges. Choose a draft to add it; your writing stays yours." : "Live AI drafting is not configured for onboarding. You can write your own details to continue."}</p>
      {drafting && <p role="status" className="small muted">Preparing three directions for {category?.name}…</p>}
      {draftError && <p role="alert" className="notice error">{draftError}</p>}
      {draftNotice && <p role="status" className="small">{draftNotice}</p>}
      {drafts && drafts.context === draftContext && <div className="onboarding-drafts"><p className="small muted">{drafts.data.notice}</p>{drafts.data.suggestions.map(item => <article className="onboarding-draft" key={item.id}><span className="pill neutral">{item.label}</span>{step === 2 ? <p>{item.objective}</p> : <><h3>{item.name}</h3><p>{item.audience.join(" · ")}</p></>}<button type="button" className="text-button" onClick={() => useDraft(item)}>{step === 2 ? "Use this mission" : "Use this starting point"}<Icon name="arrow" size={15}/></button></article>)}</div>}
    </section>;
  }
  async function submit() {
    const controller = abort.current;
    const active = () => currentScope.current === scope && abort.current === controller && !!controller && !controller.signal.aborted;
    if (!valid || lock.current || !active()) return;
    const problem = creatorFieldError(fields, 1) || creatorFieldError(fields, 2);
    if (!category || problem) { setError(problem || "Choose a content category."); return; }
    clearDrafts(); lock.current = true; setBusy(true); setError("");
    const payload = creatorPayload(fields); const signature = JSON.stringify(payload);
    if (!keys.current.has(signature)) keys.current.set(signature, crypto.randomUUID());
    try {
      const saved = await request<Influencer>(session, "studio/influencers", "POST", { ...payload, idempotency_key: keys.current.get(signature) }, controller?.signal);
      if (active()) await onCreated(saved);
    } catch (cause) { if (active()) setError(cause instanceof Error ? cause.message : "Could not confirm creation. Retry this same request to recover it."); }
    finally { if (active()) { lock.current = false; setBusy(false); } }
  }
  return <Modal title="Create new" onClose={onClose} wide>
    <div className="wizard-layout"><div className="wizard-main"><div className="step-label">STEP {step + 1} OF 3 <span>{["Choose a world", "Build a personality", "Give it a purpose"][step]}</span></div>
      <h2>{["What’s their thing?", "Meet your next creator.", "Make every post matter."][step]}</h2>
      <p className="muted">{["Pick a content category. It becomes the foundation for their audience and editorial direction.", "A memorable name, a clear audience, and a voice that feels like them.", "Set a clear mission. Sources, quality checks and your approval stay part of every workflow."][step]}</p>
      {error && <p className="notice error" role="alert">{error}</p>}
      {step > 0 && validationError && <p className="small muted" role="status">{validationError}</p>}
      <form onSubmit={event => { event.preventDefault(); if (step < 2 && valid) moveStep(step + 1); else if (step === 2) void submit(); }}>
        <fieldset disabled={busy} className="plain-fieldset">
          {step === 0 && <div className="category-grid">{categories.map(row => <button type="button" key={row.id} className={`category-option category-${row.id} ${fields.category_id === row.id ? "selected" : ""}`} aria-pressed={fields.category_id === row.id} onClick={() => edit({ category_id: row.id })}><span className="category-symbol"><Icon name={categoryIcon[row.id]}/></span><strong>{row.name}</strong><small>{row.description}</small>{fields.category_id === row.id && <span className="selected-tick"><Icon name="check" size={14}/></span>}</button>)}</div>}
          {step === 1 && <div className="form-stack"><label>Creator name<input autoFocus required minLength={1} maxLength={100} placeholder="Give your influencer a name" value={fields.name} onChange={event => edit({ name: event.target.value })}/></label><label>Who is their content for?<input required maxLength={815} placeholder="e.g. Independent cafés, first-time founders" value={fields.audience} onChange={event => edit({ audience: event.target.value })}/><small>Separate audience groups with commas.</small></label><div className="form-two"><label>Content language<select value={fields.language} onChange={event => edit({ language: event.target.value })}><option value="en">English</option><option value="es">Spanish</option></select></label><label>Voice & tone<select value={fields.tone} onChange={event => edit({ tone: event.target.value })}><option value="CLEAR">Clear & knowledgeable</option><option value="WARM">Warm & approachable</option><option value="BOLD">Bold & direct</option></select></label></div></div>}
          {step === 2 && <div className="form-stack"><label>Editorial mission<textarea autoFocus required minLength={10} maxLength={500} rows={5} placeholder="Help independent business owners discover practical opportunities and understand what to do next." value={fields.objective} onChange={event => edit({ objective: event.target.value })}/></label>{draftAssistant()}<div className="notice"><Icon name="shield"/><div><strong>Built with clear boundaries</strong><p>Your influencer is disclosed as AI. Factual content needs source evidence; nothing is posted without approval.</p></div></div><p className="muted small">This creates a saved identity and editorial configuration. Portrait and voice generation are separate setup steps.</p></div>}
          {step === 1 && draftAssistant()}
          <div className="wizard-actions">{step > 0 && <button type="button" className="button secondary" onClick={() => moveStep(step - 1)}>Back</button>}<span/>
            <button className="button primary" disabled={!valid || busy}>{busy ? "Creating…" : step === 2 ? "Create new" : "Continue"}<Icon name={step === 2 ? "spark" : "arrow"} size={17}/></button>
          </div>
        </fieldset>
      </form>
    </div><aside className={`creator-live-preview category-${fields.category_id || "business"}`}><span className="eyebrow">YOUR CREATOR</span><div className="preview-monogram">{fields.name.trim() ? initials(fields.name) : <Icon name="spark" size={52}/>}</div><span className="pill light">{category?.name ?? "Choose a category"}</span><h3>{fields.name.trim() || "A new perspective."}</h3><p>{fields.objective.trim() || "Your voice. Your audience. A world of possibilities."}</p><div className="preview-meta"><span>{fields.language === "es" ? "ES" : "EN"}</span><span>{fields.tone.toLowerCase()} voice</span><span>AI creator</span></div><small>Identity preview · No generated portrait yet</small></aside></div>
  </Modal>;
}
