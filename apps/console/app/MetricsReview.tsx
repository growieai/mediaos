"use client";

import { useCallback, useEffect, useMemo, useRef, useState } from "react";

const metrics = ["reach", "saves", "shares", "comments", "follows"] as const;
type Counts = Record<typeof metrics[number], number | null>;
type Subject = { id: string; render_run_id: string; mode: "MANUAL" | "FIXTURE"; verification_status: string; payload: { platform_label: string; external_reference: string | null; provenance_note: string } };
type Snapshot = { id: string; observed_at: string; payload: Counts & { observed_at: string; evidence_text: string; definition_notes: string } };
type Learning = { id: string; payload: Record<string, unknown> };
type Detail = Subject & { snapshots: Snapshot[]; learning_reports: Learning[] };
type Props = { token: string; tenant: string; workflowId: string; renderId: string; operator: boolean; hasHistoricalApproval: boolean };

export default function MetricsReview({ token, tenant, workflowId, renderId, operator, hasHistoricalApproval }: Props) {
  const [subjects, setSubjects] = useState<Subject[]>([]);
  const [selected, setSelected] = useState("");
  const [detail, setDetail] = useState<Detail | null>(null);
  const [mode, setMode] = useState<"FIXTURE" | "MANUAL">("FIXTURE");
  const [platform, setPlatform] = useState("");
  const [reference, setReference] = useState("");
  const [provenance, setProvenance] = useState("");
  const [observed, setObserved] = useState("");
  const [values, setValues] = useState<Record<string, string>>({});
  const [evidence, setEvidence] = useState("");
  const [definitions, setDefinitions] = useState("");
  const [baseline, setBaseline] = useState("");
  const [current, setCurrent] = useState("");
  const [busy, setBusy] = useState(false);
  const [message, setMessage] = useState("");
  const currentSelection = useRef(selected);
  currentSelection.current = selected;
  const scope = useMemo(() => ({ tenant, token, workflowId, renderId }), [tenant, token, workflowId, renderId]);
  const active = useRef(scope);
  active.current = scope;
  const abort = useRef<AbortController | null>(null);
  const pending = useRef<{ signature: string; key: string } | null>(null);

  const request = useCallback(async <T,>(path: string, method = "GET", body?: unknown): Promise<T> => {
    const response = await fetch(`/api/internal/${path}`, {
      method, cache: "no-store", signal: abort.current?.signal,
      headers: { Authorization: `Bearer ${token}`, "X-Tenant-ID": tenant, "Content-Type": "application/json" },
      body: body === undefined ? undefined : JSON.stringify(body),
    });
    if (response.headers.get("content-type")?.split(";")[0] !== "application/json") throw new Error(`Metrics request failed (${response.status}).`);
    const result = await response.json();
    if (!response.ok) throw new Error(typeof result.detail === "string" ? result.detail : `Metrics request failed (${response.status}).`);
    return result as T;
  }, [tenant, token]);

  const load = useCallback(async (preferred?: string) => {
    const list = await request<Subject[]>(`workflow-runs/${workflowId}/metric-subjects`);
    if (active.current !== scope || abort.current?.signal.aborted) return;
    setSubjects(list);
    setSelected(previous => list.some(row => row.id === preferred) ? preferred! : list.some(row => row.id === previous) ? previous : list[0]?.id ?? "");
  }, [request, scope, workflowId]);

  useEffect(() => {
    abort.current = new AbortController();
    pending.current = null;
    setSubjects([]); setSelected(""); setDetail(null); setMessage(""); setBusy(false);
    setMode("FIXTURE"); setPlatform(""); setReference(""); setProvenance("");
    setObserved(new Date().toISOString()); setValues({}); setEvidence(""); setDefinitions("");
    load().catch(error => { if (active.current === scope && !abort.current?.signal.aborted) setMessage(error instanceof Error ? error.message : "Could not load observations."); });
    return () => abort.current?.abort();
  }, [load, scope]);

  const refreshDetail = useCallback(async () => {
    if (!selected) { setDetail(null); return; }
    const data = await request<Detail>(`metric-subjects/${selected}`);
    if (active.current !== scope || currentSelection.current !== selected || abort.current?.signal.aborted) return;
    setDetail(data);
    const ordered = [...data.snapshots].sort((a, b) => Date.parse(a.observed_at) - Date.parse(b.observed_at));
    setBaseline(previous => ordered.some(row => row.id === previous) ? previous : ordered[0]?.id ?? "");
    setCurrent(previous => ordered.some(row => row.id === previous) ? previous : ordered[ordered.length - 1]?.id ?? "");
  }, [request, scope, selected]);

  useEffect(() => {
    let cancelled = false;
    setDetail(null); setBaseline(""); setCurrent("");
    refreshDetail().catch(error => { if (!cancelled && active.current === scope && !abort.current?.signal.aborted) setMessage(error instanceof Error ? error.message : "Could not load saved metrics."); });
    return () => { cancelled = true; };
  }, [refreshDetail, scope]);

  async function action(work: () => Promise<void>) {
    const captured = scope;
    setBusy(true); setMessage("");
    try { await work(); }
    catch (error) { if (active.current === captured && !abort.current?.signal.aborted) setMessage(error instanceof Error ? error.message : "Metrics request failed."); }
    finally { if (active.current === captured) setBusy(false); }
  }

  async function save(path: string, body: Record<string, unknown>) {
    // Recover an uncertain response with the same key and payload; edits create a new request.
    const signature = JSON.stringify({ path, body });
    if (pending.current?.signature !== signature) pending.current = { signature, key: crypto.randomUUID() };
    return request<{ id: string }>(path, "POST", { ...body, idempotency_key: pending.current.key });
  }

  async function register() {
    const row = await save(`workflow-runs/${workflowId}/metric-subjects`, {
      schema_version: 1, render_run_id: renderId, mode, platform_label: platform,
      external_reference: mode === "FIXTURE" ? null : reference, provenance_note: provenance,
    });
    await load(row.id);
  }

  async function addSnapshot() {
    const counts: Record<string, number | null> = {};
    for (const name of metrics) {
      const raw = (values[name] ?? "").trim();
      if (raw !== "" && (!/^\d+$/.test(raw) || !Number.isSafeInteger(Number(raw)))) throw new Error(`${name} must be a nonnegative integer or blank for Unknown.`);
      counts[name] = raw === "" ? null : Number(raw);
    }
    await save(`metric-subjects/${selected}/snapshots`, {
      schema_version: 1, observed_at: observed, scope: "LIFETIME_CUMULATIVE", ...counts,
      evidence_text: evidence, definition_notes: definitions,
    });
    await refreshDetail();
  }

  return <section aria-label="Reported metrics" style={{ borderTop: "1px solid #ccc", marginTop: 20 }}>
    <h3>Reported metrics and descriptive comparisons</h3>
    <p>No social account is connected. Manual data is self-reported; fixture data is synthetic. Neither is verified platform performance. Blank values remain <strong>Unknown</strong>, not zero.</p>
    <button disabled={busy} onClick={() => action(async () => { await load(); await refreshDetail(); })}>Refresh metrics</button>
    <p role="status">{busy ? "Saving or loading observations…" : message}</p>
    {operator && hasHistoricalApproval && <details><summary>Register observations for this approved render</summary>
      <fieldset disabled={busy}>
        <label>Provenance <select value={mode} onChange={e => setMode(e.target.value as "MANUAL" | "FIXTURE")}><option value="FIXTURE">FIXTURE — synthetic testing only</option><option value="MANUAL">MANUAL — self-reported, unverified</option></select></label>
        <p><label>Platform label <input value={platform} maxLength={64} onChange={e => setPlatform(e.target.value)} /></label></p>
        {mode === "MANUAL" && <p><label>Reported post reference <input value={reference} maxLength={512} onChange={e => setReference(e.target.value)} /></label></p>}
        <label>Provenance note <textarea value={provenance} maxLength={2000} onChange={e => setProvenance(e.target.value)} /></label>
        <p style={{ overflowWrap: "anywhere" }}>Exact render: {renderId}. This records an association; it does not verify that a post exists.</p>
        <button disabled={!platform.trim() || !provenance.trim() || (mode === "MANUAL" && !reference.trim())} onClick={() => action(register)}>Add this carousel to metrics</button>
      </fieldset>
    </details>}
    {!hasHistoricalApproval && <p>Registration requires an exact historical content and visual approval. Metrics never replace approval.</p>}
    {subjects.length === 0 ? <p>No observations registered for this workflow.</p> : <p><label>Observation set <select disabled={busy} value={selected} onChange={e => setSelected(e.target.value)}>{subjects.map(row => <option key={row.id} value={row.id}>{row.mode} · {row.payload.platform_label} · {row.id}</option>)}</select></label></p>}
    {detail && detail.id === selected && <>
      <p><strong>{detail.mode === "FIXTURE" ? "SYNTHETIC FIXTURE — not real performance" : "SELF-REPORTED — not independently verified"}</strong></p>
      <p style={{ overflowWrap: "anywhere" }}>Render: {detail.render_run_id}<br />Reference: {detail.payload.external_reference ?? "None (fixture)"}</p>
      <p>{detail.payload.provenance_note}</p>
      {operator && <details><summary>Add an immutable observation</summary><fieldset disabled={busy}>
        <p><label>Observation time (UTC ISO format) <input value={observed} onChange={e => setObserved(e.target.value)} /></label>{" "}<button onClick={() => setObserved(new Date().toISOString())}>Use current UTC time</button></p>
        <p>Enter cumulative reported counts. Leave unavailable counts blank.</p>
        {metrics.map(name => <label key={name} style={{ marginRight: 12 }}>{name} <input inputMode="numeric" aria-label={name} value={values[name] ?? ""} onChange={e => setValues(previous => ({ ...previous, [name]: e.target.value }))} style={{ width: 90 }} /></label>)}
        <p><label>Evidence text <textarea value={evidence} maxLength={20000} onChange={e => setEvidence(e.target.value)} /></label></p>
        <p><label>Metric definitions / collection notes <textarea value={definitions} maxLength={2000} onChange={e => setDefinitions(e.target.value)} /></label></p>
        <button disabled={!observed || !evidence.trim() || !definitions.trim()} onClick={() => action(addSnapshot)}>Save observation</button>
      </fieldset></details>}
      {detail.snapshots.map(snapshot => <details key={snapshot.id}><summary>{snapshot.observed_at} · {snapshot.id}</summary>
        <table><thead><tr><th>Metric</th><th>Reported count</th></tr></thead><tbody>{metrics.map(name => <tr key={name}><td>{name}</td><td>{snapshot.payload[name] === null ? "Unknown" : snapshot.payload[name]}</td></tr>)}</tbody></table>
        <p>{snapshot.payload.definition_notes}</p><pre style={{ whiteSpace: "pre-wrap", overflowWrap: "anywhere" }}>{snapshot.payload.evidence_text}</pre>
      </details>)}
      {operator && detail.snapshots.length >= 2 && <fieldset disabled={busy}><legend>Compare two observations of this subject</legend>
        <label>Baseline <select value={baseline} onChange={e => setBaseline(e.target.value)}>{detail.snapshots.map(s => <option key={s.id} value={s.id}>{s.observed_at} · {s.id}</option>)}</select></label>{" "}
        <label>Current <select value={current} onChange={e => setCurrent(e.target.value)}>{detail.snapshots.map(s => <option key={s.id} value={s.id}>{s.observed_at} · {s.id}</option>)}</select></label>
        <p><button disabled={!baseline || !current || baseline === current} onClick={() => action(async () => { await save(`metric-subjects/${selected}/learning-reports`, { baseline_snapshot_id: baseline, current_snapshot_id: current }); await refreshDetail(); })}>Save descriptive comparison</button></p>
      </fieldset>}
      <p>Comparisons require matching definitions and increasing observation times. Decreases can reflect corrections. Reports do not infer causes, rank posts or change editorial policy.</p>
      {detail.learning_reports.map(row => <details key={row.id}><summary>Saved comparison · {row.id}</summary><pre style={{ whiteSpace: "pre-wrap", overflowWrap: "anywhere" }}>{JSON.stringify(row.payload, null, 2)}</pre></details>)}
    </>}
  </section>;
}
