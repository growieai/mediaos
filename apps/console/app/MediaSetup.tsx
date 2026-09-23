"use client";

import { useEffect, useMemo, useRef, useState } from "react";

type JsonRequest = <T>(path: string, method?: string, body?: unknown, signal?: AbortSignal) => Promise<T>;
type Configuration = { id: string; version: number; payload: { display_name: string } };
type Props = { workflowId: string; influencerId: string; json: JsonRequest; onSaved: () => Promise<void> };
const emptyProfile = { voice_id: "", tts_model: "eleven_multilingual_v2", tts_usd_per_1000_characters: "", avatar_usd_per_second: "", price_reference: "", price_checked_at: "" };
const emptyBudget = { per_run_usd: "", per_day_usd: "", expires_at: "", enabled: false };

export default function MediaSetup({ workflowId, influencerId, json, onSaved }: Props) {
  const [configuration, setConfiguration] = useState<Configuration | null>(null);
  const [profile, setProfile] = useState(emptyProfile);
  const [budget, setBudget] = useState(emptyBudget);
  const [priceAcknowledgement, setPriceAcknowledgement] = useState<string | null>(null);
  const [budgetAcknowledgement, setBudgetAcknowledgement] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [message, setMessage] = useState("");
  const scope = useMemo(() => ({}), [workflowId, influencerId, json]);
  const active = useRef<object | null>(scope); active.current = scope;
  const saving = useRef<object | null>(null);
  const controller = useRef<AbortController | null>(null);
  const [readyScope, setReadyScope] = useState<object | null>(null);
  const priceIdentity = JSON.stringify([configuration?.id, profile]);
  const budgetIdentity = JSON.stringify(budget);

  useEffect(() => {
    const abort = new AbortController(); controller.current = abort;
    active.current = scope;
    let disposed = false;
    setConfiguration(null); setProfile(emptyProfile); setBudget(emptyBudget);
    setPriceAcknowledgement(null); setBudgetAcknowledgement(null); setMessage(""); setBusy(false);
    setReadyScope(scope);
    json<{ configurations: Configuration[] }>(`workflow-runs/${workflowId}/renders`, "GET", undefined, abort.signal).then(data => {
      if (!disposed && active.current === scope) setConfiguration([...data.configurations].sort((a, b) => b.version - a.version)[0] ?? null);
    }).catch(() => { if (!disposed && active.current === scope) setMessage("Could not load the character configuration. Refresh before configuring video."); });
    return () => { disposed = true; abort.abort(); if (active.current === scope) active.current = null; };
  }, [json, workflowId, scope]);

  function editProfile(change: Partial<typeof emptyProfile>) {
    if (active.current !== scope || readyScope !== scope) return;
    setProfile(previous => ({ ...previous, ...change })); setPriceAcknowledgement(null);
  }
  function editBudget(change: Partial<typeof emptyBudget>) {
    if (active.current !== scope || readyScope !== scope) return;
    setBudget(previous => ({ ...previous, ...change })); setBudgetAcknowledgement(null);
  }

  async function save(path: string, payload: unknown, kind: "profile" | "budget") {
    if (busy || active.current !== scope || readyScope !== scope || saving.current === scope) return;
    saving.current = scope;
    setBusy(true); setMessage(""); setPriceAcknowledgement(null); setBudgetAcknowledgement(null);
    try {
      await json(path, "POST", payload, controller.current?.signal);
      if (active.current !== scope) return;
      await onSaved();
      if (active.current === scope) setMessage(`Saved a new ${kind} version. Video generation has not started.`);
    } catch (error) {
      if (active.current === scope) setMessage(error instanceof Error ? error.message : "Could not save. Refresh saved profiles and budgets before retrying.");
    } finally {
      if (saving.current === scope) saving.current = null;
      if (active.current === scope) setBusy(false);
    }
  }

  const priceFields = [
    ["tts_usd_per_1000_characters", "Speech upper-bound price, USD / 1,000 characters"],
    ["avatar_usd_per_second", "HeyGen upper-bound price, USD / second"],
  ] as const;
  return <details aria-label="Administrator video setup"><summary>Configure voice, verified prices and budget</summary>
    <p>Keep provider keys in the server environment. Save the voice and prices checked in your accounts, then set an explicit budget. Each save creates a version. Generation requires a separate execution action.</p>
    <p role="status">{busy ? "Saving configuration…" : message}</p>
    <form onSubmit={event => {
      event.preventDefault();
      if (priceAcknowledgement !== priceIdentity || !configuration || !Number.isFinite(Date.parse(profile.price_checked_at))) return;
      void save(`influencers/${influencerId}/media-profiles`, { visual_config_version_id: configuration.id,
        payload: { schema_version: 1, ...profile, presenter_provider: "heygen", price_checked_at: new Date(profile.price_checked_at).toISOString() } }, "profile");
    }}><fieldset disabled={busy || readyScope !== scope}><legend>Voice and provider prices</legend>
      <p>Character: {configuration ? `${configuration.payload.display_name} · version ${configuration.version}` : "No current configuration available"}</p>
      <p><label>ElevenLabs voice ID <input required maxLength={128} pattern="[A-Za-z0-9_-]+" value={profile.voice_id} onChange={event => editProfile({ voice_id: event.target.value })} /></label></p>
      <p><label>Speech model <select value={profile.tts_model} onChange={event => editProfile({ tts_model: event.target.value })}><option value="eleven_multilingual_v2">Eleven Multilingual v2</option><option value="eleven_v3">Eleven v3</option></select></label></p>
      {priceFields.map(([key, label]) => <p key={key}><label>{label} <input type="number" required min="0.000001" max="1000" step="0.000001" value={profile[key]} onChange={event => editProfile({ [key]: event.target.value })} /></label></p>)}
      <p><label>Price reference (no credentials) <input required maxLength={1000} value={profile.price_reference} onChange={event => editProfile({ price_reference: event.target.value })} /></label></p>
      <p><label>Prices checked at (your local time) <input type="datetime-local" required value={profile.price_checked_at} onChange={event => editProfile({ price_checked_at: event.target.value })} /></label></p>
      <p><label><input type="checkbox" checked={priceAcknowledgement === priceIdentity} onChange={event => setPriceAcknowledgement(event.target.checked ? priceIdentity : null)} /> I verified voice access and these account prices. A new profile invalidates earlier video eligibility.</label></p>
      <button disabled={!configuration || priceAcknowledgement !== priceIdentity}>Save voice and price profile</button>
    </fieldset></form>
    <form onSubmit={event => {
      event.preventDefault();
      if (budgetAcknowledgement !== budgetIdentity || !Number.isFinite(Date.parse(budget.expires_at))) return;
      void save("media-spend-policy", { schema_version: 1, ...budget, expires_at: new Date(budget.expires_at).toISOString() }, "budget");
    }}><fieldset disabled={busy || readyScope !== scope}><legend>Tenant spending policy</legend>
      <p>Limits reserve costs using configured prices; they are not a provider billing guarantee. Set account-side limits too. The server execution flag and provider keys remain separate.</p>
      {([['per_run_usd', 'Maximum USD per video', '1000'], ['per_day_usd', 'Maximum USD per day', '10000']] as const).map(([key, label, maximum]) => <p key={key}><label>{label} <input type="number" required min="0.000001" max={maximum} step="0.000001" value={budget[key]} onChange={event => editBudget({ [key]: event.target.value })} /></label></p>)}
      <p><label>Expires at (your local time) <input type="datetime-local" required value={budget.expires_at} onChange={event => editBudget({ expires_at: event.target.value })} /></label></p>
      <p><label><input type="checkbox" checked={budget.enabled} onChange={event => editBudget({ enabled: event.target.checked })} /> Enable this spending policy</label></p>
      <p><label><input type="checkbox" checked={budgetAcknowledgement === budgetIdentity} onChange={event => setBudgetAcknowledgement(event.target.checked ? budgetIdentity : null)} /> I authorize these exact limits, expiry and enabled setting for this tenant.</label></p>
      <button disabled={budgetAcknowledgement !== budgetIdentity}>Save spending policy</button>
    </fieldset></form>
  </details>;
}
