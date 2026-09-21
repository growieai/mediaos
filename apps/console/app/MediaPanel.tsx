"use client";

import { useCallback, useEffect, useMemo, useRef, useState } from "react";

type Workflow = { id: string; state: string; asset_version_id: string | null; research_version_id: string | null; qa_report_id: string | null };
type TextChoice = { path: string; text: string; kind: string };
type Profile = { id: string; version: number; content_hash: string; payload: { voice_id: string; tts_model: string; presenter_provider: string; tts_usd_per_1000_characters: string; avatar_usd_per_second: string; price_reference: string; price_checked_at: string } };
type Policy = { id: string; version: number; enabled: boolean; per_run_usd: string | number; per_day_usd: string | number; expires_at: string };
type Job = { id: string; stage: string; status: string; attempt: number; provider: string; expected_max_cost: string | number; actual_cost: string | number | null; retry_at: string | null; error_category: string | null };
type MediaRun = { id: string; sequence: number; status: string; profile_id: string; asset_version_id: string; research_version_id: string; qa_report_id: string; script_hash: string; script: { text: string; disclosure: string }; manifest_hash: string | null; manifest: Record<string, unknown> | null; qa_result: { status: string; findings: unknown[] } | null; next_poll_at: string | null; error_category: string | null; jobs: Job[]; approvals: { id: string; decision: string; created_at: string; comment: string | null }[] };
type Collection = { profiles: Profile[]; spend_policies: Policy[]; dependencies: Record<string, unknown>; runs: MediaRun[] };
type Checks = { identity: boolean; voice: boolean; lip_sync: boolean; captions: boolean; disclosure: boolean };
type Props = { tenant: string; token: string; run: Workflow; operator: boolean; approver: boolean; choices: TextChoice[] };
const emptyChecks = (): Checks => ({ identity: false, voice: false, lip_sync: false, captions: false, disclosure: false });
const empty: Collection = { profiles: [], spend_policies: [], dependencies: {}, runs: [] };
const maximumVideoBytes = 200 * 1024 * 1024;
const terminal = new Set(["AWAITING_APPROVAL", "APPROVED", "REJECTED", "BLOCKED", "FAILED", "UNKNOWN_OUTCOME"]);

async function failure(response: Response) {
  if (response.headers.get("content-type")?.split(";")[0] === "application/json") {
    const result = await response.json();
    if (typeof result.detail === "string") return new Error(result.detail);
  }
  return new Error(`Media request failed (${response.status}). Refresh saved state before retrying.`);
}

export default function MediaPanel({ tenant, token, run, operator, approver, choices }: Props) {
  const [collection, setCollection] = useState<Collection>(empty);
  const [profileId, setProfileId] = useState("");
  const [selectedPaths, setSelectedPaths] = useState<string[]>([]);
  const [mediaId, setMediaId] = useState("");
  const [busy, setBusy] = useState(false);
  const [message, setMessage] = useState("");
  const [spendAcknowledgement, setSpendAcknowledgement] = useState<string | null>(null);
  const [checks, setChecks] = useState<Checks>(emptyChecks);
  const [comment, setComment] = useState("");
  const [preview, setPreview] = useState<{ identity: string; url: string } | null>(null);
  const scope = useMemo(() => ({}), [tenant, token, run.id, run.asset_version_id, run.research_version_id, run.qa_report_id, run.state]);
  const active = useRef(scope); active.current = scope;
  const controller = useRef<AbortController | null>(null);
  const urls = useRef(new Set<string>());
  const keys = useRef(new Map<string, string>());

  const request = useCallback(async (path: string, method = "GET", body?: unknown) => fetch(`/api/internal/${path}`, {
    method, cache: "no-store", signal: controller.current?.signal,
    headers: { Authorization: `Bearer ${token}`, "X-Tenant-ID": tenant, "Content-Type": "application/json" },
    body: body === undefined ? undefined : JSON.stringify(body),
  }), [tenant, token]);
  const json = useCallback(async <T,>(path: string, method = "GET", body?: unknown): Promise<T> => {
    const response = await request(path, method, body);
    if (!response.ok) throw await failure(response);
    if (response.headers.get("content-type")?.split(";")[0] !== "application/json") throw new Error("Expected a typed media response.");
    return response.json() as Promise<T>;
  }, [request]);
  const load = useCallback(async (preferred?: string) => {
    const data = await json<Collection>(`workflow-runs/${run.id}/media-runs`);
    if (active.current !== scope || controller.current?.signal.aborted) return;
    data.profiles.sort((a, b) => b.version - a.version); data.runs.sort((a, b) => b.sequence - a.sequence);
    setCollection(data);
    setProfileId(old => data.profiles.some(p => p.id === old) ? old : data.profiles[0]?.id ?? "");
    setMediaId(old => data.runs.some(r => r.id === preferred) ? preferred! : data.runs.some(r => r.id === old) ? old : data.runs[0]?.id ?? "");
  }, [json, run.id, scope]);

  useEffect(() => {
    const abort = new AbortController(); controller.current = abort;
    setCollection(empty); setProfileId(""); setSelectedPaths([]); setMediaId(""); setBusy(false); setMessage("");
    setSpendAcknowledgement(null); setChecks(emptyChecks()); setComment(""); setPreview(null); keys.current.clear();
    load().catch(error => { if (active.current === scope && !abort.signal.aborted) setMessage(error instanceof Error ? error.message : "Could not load media runs."); });
    return () => { abort.abort(); for (const url of urls.current) URL.revokeObjectURL(url); urls.current.clear(); };
  }, [load, scope]);

  const current = collection.runs.find(row => row.id === mediaId);
  const currentIdentity = current ? `${current.id}:${current.manifest_hash}` : "";
  const identityRef = useRef(currentIdentity); identityRef.current = currentIdentity;
  const policy = [...collection.spend_policies].sort((a, b) => b.version - a.version)[0];
  const spendIdentity = current && policy ? `${current.id}:${current.profile_id}:${policy.id}` : "";
  const exact = !!current && current.asset_version_id === run.asset_version_id && current.research_version_id === run.research_version_id && current.qa_report_id === run.qa_report_id;
  const latest = !!current && !collection.runs.some(row => row.sequence > current.sequence);
  const liveReady = ["live_enabled", "elevenlabs_configured", "heygen_configured", "ffmpeg_available", "ffprobe_available"].every(key => collection.dependencies[key] === true);
  const budgetReady = !!policy?.enabled && Date.parse(policy.expires_at) > Date.now();
  const canExecute = operator && !!current && !terminal.has(current.status) && exact && latest && run.state === "APPROVED" && liveReady && budgetReady;
  const canReview = approver && current?.status === "AWAITING_APPROVAL" && current.qa_result?.status === "PASS" && exact && latest && run.state === "APPROVED";

  useEffect(() => {
    setChecks(emptyChecks()); setComment(""); setPreview(null); setSpendAcknowledgement(null);
    for (const url of urls.current) URL.revokeObjectURL(url); urls.current.clear();
  }, [currentIdentity, spendIdentity]);

  async function action(work: () => Promise<void>) {
    setBusy(true); setMessage("");
    try { await work(); }
    catch (error) { if (active.current === scope && !controller.current?.signal.aborted) setMessage(error instanceof Error ? error.message : "Request failed; refresh saved state."); }
    finally { if (active.current === scope) setBusy(false); }
  }

  async function video(exportApproved: boolean) {
    if (!current?.manifest_hash) return;
    const captured = currentIdentity;
    const response = await request(`media-runs/${current.id}/video${exportApproved ? "?export=true" : ""}`);
    if (!response.ok) throw await failure(response);
    if (response.headers.get("content-type")?.split(";")[0] !== "video/mp4") throw new Error("Expected an authenticated MP4.");
    const blob = await response.blob();
    if (!blob.size || blob.size > maximumVideoBytes) throw new Error("Video exceeds the supported 200 MiB preview limit.");
    if (active.current !== scope || identityRef.current !== captured || controller.current?.signal.aborted) return;
    const url = URL.createObjectURL(blob); urls.current.add(url);
    if (exportApproved) {
      const anchor = document.createElement("a"); anchor.href = url; anchor.download = `media-${current.id}.mp4`; anchor.click();
      setMessage("Downloaded the current human-approved MP4. Nothing was published.");
    } else {
      if (preview) { URL.revokeObjectURL(preview.url); urls.current.delete(preview.url); }
      setPreview({ identity: captured, url });
    }
  }

  return <section aria-label="Speaking video review" style={{ borderTop: "1px solid #ccc", marginTop: 20 }}>
    <h2>Speaking video</h2>
    <p>Generate speech and a talking presenter from approved source text, then review the exact MP4. No video generation starts automatically; nothing is published.</p>
    <button disabled={busy} onClick={() => action(() => load())}>Refresh videos and dependencies</button>
    <p role="status">{busy ? "Working… Saved jobs may continue if a request times out; refresh before resuming." : message}</p>
    <ul>{[["live_enabled", "Paid media execution"], ["elevenlabs_configured", "ElevenLabs credential"], ["heygen_configured", "HeyGen credential"], ["ffmpeg_available", "FFmpeg"], ["ffprobe_available", "FFprobe"]].map(([key, label]) => <li key={key}>{label}: {collection.dependencies[key] === true ? "Ready" : "Not configured / disabled"}</li>)}</ul>
    {policy ? <p>Configured budget v{policy.version}: US${policy.per_run_usd} per video, US${policy.per_day_usd} per day. {policy.enabled ? "Enabled" : "Disabled"}; expires {policy.expires_at}. These are enforced reservation limits using the configured rate card, not a provider billing quote.</p> : <p>An administrator must configure a voice profile with current prices and a spend policy before paid execution. See the media setup documentation; credentials never belong in this console.</p>}
    {operator && <details><summary>Prepare a speaking-video request</summary><fieldset disabled={busy || run.state !== "APPROVED"}>
      <p>Preparation saves the script and exact parent revisions; it does not make paid calls. Select the approved excerpts in their desired speaking order. AI disclosure is added by policy.</p>
      <label>Voice / presenter profile <select value={profileId} onChange={event => setProfileId(event.target.value)}><option value="">Choose a configured profile</option>{collection.profiles.map(profile => <option key={profile.id} value={profile.id}>Version {profile.version} · {profile.payload.voice_id} · {profile.payload.presenter_provider}</option>)}</select></label>
      {choices.map(choice => <p key={choice.path}><label><input type="checkbox" checked={selectedPaths.includes(choice.path)} disabled={!selectedPaths.includes(choice.path) && selectedPaths.length >= 10} onChange={event => setSelectedPaths(old => event.target.checked ? [...old, choice.path] : old.filter(path => path !== choice.path))} /> {choice.path} · {choice.text} <small>({choice.kind})</small></label></p>)}
      <p>Speaking order: {selectedPaths.join(" → ") || "Choose one or more excerpts."}</p>
      <button disabled={!profileId || !selectedPaths.length} onClick={() => action(async () => {
        const body = { profile_id: profileId, selected_paths: selectedPaths }; const signature = JSON.stringify(body);
        if (!keys.current.has(signature)) keys.current.set(signature, crypto.randomUUID());
        const saved = await json<MediaRun>(`workflow-runs/${run.id}/media-runs`, "POST", { ...body, idempotency_key: keys.current.get(signature) });
        await load(saved.id);
      })}>Save / recover video request (no paid calls)</button>{" "}
      <button onClick={() => { keys.current.clear(); setMessage("Next save will create a new video request requiring fresh review."); }}>Prepare a new request key</button>
    </fieldset></details>}
    {run.state !== "APPROVED" && <p>Approve the current source-backed content before preparing or executing a video.</p>}
    <p><label>Saved video <select disabled={busy} value={mediaId} onChange={event => setMediaId(event.target.value)}><option value="">No video selected</option>{collection.runs.map(row => <option key={row.id} value={row.id}>{row.status} · {row.id}</option>)}</select></label></p>
    {current && <article>
      <h3>{current.status}</h3><p>{current.id}{(!exact || !latest) && " · Historical revision; execution/approval disabled"}</p>
      <p style={{ whiteSpace: "pre-wrap", overflowWrap: "anywhere" }}>{current.script.text}</p>
      {current.error_category && <p>Saved error: {current.error_category}</p>}{current.next_poll_at && <p>Next safe poll: {current.next_poll_at}</p>}
      {current.status === "UNKNOWN_OUTCOME" && <p>A provider submission has an uncertain outcome. Administrative reconciliation is required; no blind resubmission is offered.</p>}
      {canExecute && <fieldset disabled={busy}><label><input type="checkbox" checked={spendAcknowledgement === spendIdentity} onChange={event => setSpendAcknowledgement(event.target.checked ? spendIdentity : null)} /> I authorize this exact saved video to use the configured paid providers within the current US${policy?.per_run_usd} per-run and US${policy?.per_day_usd} daily reservation limits.</label>
        <p><button disabled={spendAcknowledgement !== spendIdentity} onClick={() => action(async () => { setSpendAcknowledgement(null); await json(`media-runs/${current.id}/execute`, "POST"); await load(current.id); })}>Execute / resume paid video</button></p>
      </fieldset>}
      {current.manifest_hash && <><button disabled={busy} onClick={() => action(() => video(false))}>Load exact video preview</button>{" "}<button disabled={busy || current.status !== "APPROVED" || !exact || !latest} onClick={() => action(() => video(true))}>Download human-approved MP4</button></>}
      {preview?.identity === currentIdentity && <video controls playsInline preload="metadata" src={preview.url} style={{ display: "block", maxWidth: 420, width: "100%", marginTop: 12 }} />}
      {current.qa_result && <p>Media QA: {current.qa_result.status}. Human appearance and audiovisual checks remain required.</p>}
      {canReview && <fieldset disabled={busy}><legend>Review the exact preview</legend>
        {preview?.identity !== currentIdentity && <p>Load the exact video before checking quality or approving. You can reject an unavailable or corrupt preview with a review comment.</p>}
        {([["identity", "Character identity and appearance"], ["voice", "Voice and pronunciation"], ["lip_sync", "Lip sync and expression"], ["captions", "Caption accuracy and timing"], ["disclosure", "Visible and correct AI disclosure"]] as const).map(([key, label]) => <p key={key}><label><input type="checkbox" disabled={preview?.identity !== currentIdentity} checked={checks[key]} onChange={event => setChecks(old => ({ ...old, [key]: event.target.checked }))} /> {label}</label></p>)}
        <label>Review comment <input maxLength={2000} value={comment} onChange={event => setComment(event.target.value)} /></label>{" "}
        {(["APPROVE", "REJECT"] as const).map(decision => <button key={decision} disabled={decision === "APPROVE" && (preview?.identity !== currentIdentity || !Object.values(checks).every(Boolean))} onClick={() => action(async () => { await json(`media-runs/${current.id}/review`, "POST", { manifest_hash: current.manifest_hash, decision, checks, comment: comment || null }); await load(current.id); setChecks(emptyChecks()); })}>{decision === "APPROVE" ? "Approve exact video" : "Reject video"}</button>)}
      </fieldset>}
      {current.approvals.map(approval => <p key={approval.id}>Human decision: {approval.decision} · {approval.created_at} · {approval.comment}</p>)}
      <details><summary>Saved attempts, costs and exact manifest</summary><pre style={{ whiteSpace: "pre-wrap", overflowWrap: "anywhere" }}>{JSON.stringify({ jobs: current.jobs, manifest: current.manifest, manifest_hash: current.manifest_hash, qa: current.qa_result }, null, 2)}</pre></details>
    </article>}
  </section>;
}
