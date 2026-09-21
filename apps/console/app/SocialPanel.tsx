"use client";

import { useCallback, useEffect, useMemo, useRef, useState } from "react";

type Workflow = { id: string; influencer_id: string; state: string; asset_version_id: string | null; research_version_id: string | null; qa_report_id: string | null };
type Connection = { id: string; influencer_id: string; account_id: string; username: string; version: number; api_version: string; scopes: string[] };
type Render = { id: string; sequence: number; status: string; asset_version_id: string; research_version_id: string; qa_report_id: string; visual_config_version_id: string; approvals: { decision: string }[] };
type RenderCollection = { renders: Render[]; configurations: { id: string; version: number }[] };
type Publish = { id: string; created_at: string; status: string; connection_id: string; account_id: string; render_run_id: string; plan_hash: string; plan: { caption: string; slides: { index: number; sha256: string }[] }; post_id: string | null; jobs: { id: string; stage: string; status: string; attempt: number; error_category: string | null; retry_at: string | null }[]; decisions: unknown[]; insights: { id: string; captured_at: string; payload: Record<string, unknown> }[] };
type Props = { tenant: string; token: string; run: Workflow; operator: boolean; approver: boolean; admin: boolean };
type Learning = { id: string; status: string; payload: { baseline_snapshot_id: string; current_snapshot_id: string; baseline_observed_at: string; current_observed_at: string; metrics: Record<string, { baseline: number | null; current: number | null; delta: number | null; direction: string }>; definitions: Record<string, string>; limitations: string[] } };
type Checks = { reviewed_images: boolean; reviewed_caption: boolean; confirmed_account: boolean };
type CommentRelink = { publish_run_id: string; processed: number; linked: number; has_more: boolean; network_performed: false };
const emptyChecks = (): Checks => ({ reviewed_images: false, reviewed_caption: false, confirmed_account: false });

async function errorFor(response: Response) {
  if (response.headers.get("content-type")?.split(";")[0] === "application/json") {
    const data = await response.json();
    if (typeof data.detail === "string") return new Error(data.detail);
  }
  return new Error(`Instagram request failed (${response.status}). Refresh saved state before retrying.`);
}

export default function SocialPanel({ tenant, token, run, operator, approver, admin }: Props) {
  const [dependencies, setDependencies] = useState<Record<string, boolean>>({});
  const [connections, setConnections] = useState<Connection[]>([]);
  const [revokedIds, setRevokedIds] = useState<string[]>([]);
  const [renders, setRenders] = useState<RenderCollection>({ renders: [], configurations: [] });
  const [publishes, setPublishes] = useState<Publish[]>([]);
  const [connectionId, setConnectionId] = useState("");
  const [publishId, setPublishId] = useState("");
  const [authorizationUrl, setAuthorizationUrl] = useState("");
  const [message, setMessage] = useState("");
  const [busy, setBusy] = useState(false);
  const [checks, setChecks] = useState<Checks>(emptyChecks);
  const [dispatchAck, setDispatchAck] = useState("");
  const [preview, setPreview] = useState<{ identity: string; images: { index: number; url: string }[] } | null>(null);
  const [loadedImages, setLoadedImages] = useState<number[]>([]);
  const [candidateId, setCandidateId] = useState("");
  const [matchComment, setMatchComment] = useState("");
  const [matched, setMatched] = useState(false);
  const [learning, setLearning] = useState<Learning[]>([]);
  const [baselineId, setBaselineId] = useState("");
  const [comparisonId, setComparisonId] = useState("");
  const scope = useMemo(() => ({}), [tenant, token, run.id, run.influencer_id, run.state, run.asset_version_id, run.research_version_id, run.qa_report_id]);
  const active = useRef(scope); active.current = scope;
  const controller = useRef<AbortController | null>(null);
  const urls = useRef(new Set<string>());
  const keys = useRef(new Map<string, string>());

  const request = useCallback((path: string, method = "GET", body?: unknown) => fetch(`/api/internal/${path}`, {
    method, cache: "no-store", signal: controller.current?.signal,
    headers: { Authorization: `Bearer ${token}`, "X-Tenant-ID": tenant, "Content-Type": "application/json" },
    body: body === undefined ? undefined : JSON.stringify(body),
  }), [tenant, token]);
  const json = useCallback(async <T,>(path: string, method = "GET", body?: unknown): Promise<T> => {
    const response = await request(path, method, body);
    if (!response.ok) throw await errorFor(response);
    if (response.headers.get("content-type")?.split(";")[0] !== "application/json") throw new Error("Expected an internal JSON response.");
    return response.json() as Promise<T>;
  }, [request]);
  const load = useCallback(async (preferred?: string) => {
    const [deps, accounts, visual, saved] = await Promise.all([
      json<Record<string, boolean>>("social/dependencies"),
      json<{ connections: Connection[]; revocations: { connection_id: string }[] }>("social/connections"),
      json<RenderCollection>(`workflow-runs/${run.id}/renders`),
      json<Publish[]>(`workflow-runs/${run.id}/social-publishes`),
    ]);
    if (active.current !== scope || controller.current?.signal.aborted) return;
    setDependencies(deps); setConnections(accounts.connections); setRevokedIds(accounts.revocations.map(row => row.connection_id));
    setRenders({ renders: [...visual.renders].sort((a, b) => b.sequence - a.sequence), configurations: [...visual.configurations].sort((a, b) => b.version - a.version) });
    const ordered = [...saved].sort((a, b) => b.created_at.localeCompare(a.created_at)); setPublishes(ordered);
    const eligible = accounts.connections.filter(c => c.influencer_id === run.influencer_id && !accounts.revocations.some(r => r.connection_id === c.id) && !accounts.connections.some(newer => newer.account_id === c.account_id && newer.version > c.version));
    setConnectionId(old => eligible.some(c => c.id === old) ? old : eligible[0]?.id ?? "");
    setPublishId(old => ordered.some(row => row.id === preferred) ? preferred! : ordered.some(row => row.id === old) ? old : ordered[0]?.id ?? "");
  }, [json, run.id, run.influencer_id, scope]);

  useEffect(() => {
    const abort = new AbortController(); controller.current = abort;
    setDependencies({}); setConnections([]); setRevokedIds([]); setRenders({ renders: [], configurations: [] }); setPublishes([]); setConnectionId(""); setPublishId("");
    setAuthorizationUrl(""); setMessage(""); setBusy(false); keys.current.clear();
    load().catch(error => { if (active.current === scope && !abort.signal.aborted) setMessage(error instanceof Error ? error.message : "Could not load Instagram state."); });
    return () => { abort.abort(); for (const url of urls.current) URL.revokeObjectURL(url); urls.current.clear(); };
  }, [load, scope]);

  const isCurrentConnection = (connection: Connection) => !revokedIds.includes(connection.id) && !connections.some(newer => newer.account_id === connection.account_id && newer.version > connection.version);
  const eligibleConnections = connections.filter(c => c.influencer_id === run.influencer_id && isCurrentConnection(c));
  const selectedConnection = connections.find(c => c.id === connectionId);
  const latestRender = renders.renders[0];
  const approvedRender = latestRender && latestRender.status === "PASS" && latestRender.approvals.some(a => a.decision === "APPROVE") && latestRender.visual_config_version_id === renders.configurations[0]?.id && latestRender.asset_version_id === run.asset_version_id && latestRender.research_version_id === run.research_version_id && latestRender.qa_report_id === run.qa_report_id && run.state === "APPROVED" ? latestRender : null;
  const current = publishes.find(row => row.id === publishId);
  const currentConnection = connections.find(c => c.id === current?.connection_id);
  const identity = current ? `${current.id}:${current.plan_hash}` : "";
  const dispatchIdentity = current ? `${identity}:${current.status}` : "";
  const recoveringPublication = current?.status === "PUBLISHING";
  const identityRef = useRef(identity); identityRef.current = identity;
  const exact = !!current && current.render_run_id === approvedRender?.id && !!currentConnection && isCurrentConnection(currentConnection);
  const previewReady = !!current && preview?.identity === identity && current.plan.slides.length === loadedImages.length && current.plan.slides.every(slide => loadedImages.includes(slide.index));
  const publishReady = ["manual_publish_enabled", "app_configured", "vault_configured", "public_media_configured"].every(key => dependencies[key] === true);
  const connectionReady = ["connect_enabled", "app_configured", "vault_configured"].every(key => dependencies[key] === true);
  const snapshots = [...(current?.insights ?? [])].sort((a, b) => a.captured_at.localeCompare(b.captured_at));
  const snapshotIdentity = snapshots.map(row => row.id).join(":");
  const baseline = snapshots.find(row => row.id === baselineId);
  const comparison = snapshots.find(row => row.id === comparisonId);
  const validPair = !!baseline && !!comparison && baseline.id !== comparison.id && Date.parse(baseline.captured_at) < Date.parse(comparison.captured_at);
  const loadLearning = useCallback(async () => {
    if (!publishId) return;
    const result = await json<Learning[]>(`social-publishes/${publishId}/learning`);
    if (active.current === scope && identityRef.current === identity && !controller.current?.signal.aborted) setLearning(result);
  }, [json, publishId, scope, identity]);

  useEffect(() => {
    setLearning([]);
    loadLearning().catch(error => { if (active.current === scope && identityRef.current === identity && !controller.current?.signal.aborted) setMessage(error instanceof Error ? error.message : "Could not load saved platform comparisons."); });
  }, [loadLearning, scope, identity]);

  useEffect(() => {
    setBaselineId(old => snapshots.some(row => row.id === old) ? old : snapshots[0]?.id ?? "");
    setComparisonId(old => snapshots.some(row => row.id === old) ? old : snapshots.at(-1)?.id ?? "");
  }, [identity, snapshotIdentity]);

  useEffect(() => {
    setChecks(emptyChecks()); setDispatchAck(""); setPreview(null); setLoadedImages([]); setCandidateId(""); setMatchComment(""); setMatched(false);
    for (const url of urls.current) URL.revokeObjectURL(url); urls.current.clear();
  }, [identity]);

  async function action(work: () => Promise<void>) {
    setBusy(true); setMessage("");
    try { await work(); }
    catch (error) { if (active.current === scope && !controller.current?.signal.aborted) setMessage(error instanceof Error ? error.message : "Request failed. Refresh persisted state."); }
    finally { if (active.current === scope) setBusy(false); }
  }
  function keyFor(signature: string) {
    if (!keys.current.has(signature)) keys.current.set(signature, crypto.randomUUID());
    return keys.current.get(signature)!;
  }
  async function loadPreview() {
    if (!current) return;
    const captured = identity; const created: string[] = []; const images: { index: number; url: string }[] = [];
    setChecks(emptyChecks()); setPreview(null); setLoadedImages([]);
    for (const url of urls.current) URL.revokeObjectURL(url); urls.current.clear();
    try {
      for (const slide of current.plan.slides) {
        const response = await request(`social-publishes/${current.id}/slides/${slide.index}`);
        if (!response.ok) throw await errorFor(response);
        if (response.headers.get("content-type")?.split(";")[0] !== "image/jpeg") throw new Error("Expected the exact approved JPEG.");
        const blob = await response.blob();
        if (!blob.size || blob.size > 8_000_000) throw new Error("JPEG exceeds the 8 MB preview limit.");
        const digest = Array.from(new Uint8Array(await crypto.subtle.digest("SHA-256", await blob.arrayBuffer()))).map(byte => byte.toString(16).padStart(2, "0")).join("");
        if (digest !== slide.sha256) throw new Error("JPEG differs from the saved publication plan.");
        if (active.current !== scope || identityRef.current !== captured || controller.current?.signal.aborted) return;
        const url = URL.createObjectURL(blob); created.push(url); urls.current.add(url); images.push({ index: slide.index, url });
      }
      setPreview({ identity: captured, images });
    } finally {
      if (images.length !== current.plan.slides.length || active.current !== scope || identityRef.current !== captured) {
        for (const url of created) { URL.revokeObjectURL(url); urls.current.delete(url); }
      }
    }
  }

  return <section aria-label="Instagram connection and publication" style={{ borderTop: "1px solid #ccc", marginTop: 20 }}>
    <h2>Instagram · controlled publishing</h2>
    <p>Connect a professional account, review the exact JPEGs and caption, then explicitly dispatch the authorized post. Connecting or approving never sends a post automatically.</p>
    <button disabled={busy} onClick={() => action(() => load())}>Refresh Instagram state</button>
    <p role="status">{busy ? "Working… If a request times out, refresh saved state before taking another action." : message}</p>
    <ul>{[["connect_enabled", "Account connection"], ["manual_publish_enabled", "Manual publishing"], ["app_configured", "Meta app and pinned API version"], ["vault_configured", "Credential vault"], ["public_media_configured", "Approved public media origin"], ["reply_dispatch_enabled", "Separate approved replies"]].map(([name, label]) => <li key={name}>{label}: {dependencies[name] === true ? "Configured / enabled" : "Disabled or missing"}</li>)}</ul>
    {admin && <p><button disabled={busy || !connectionReady || !run.influencer_id} onClick={() => action(async () => {
      const result = await json<{ authorization_url: string }>("social/connect", "POST", { influencer_id: run.influencer_id });
      const url = new URL(result.authorization_url);
      if (url.protocol !== "https:" || url.hostname !== "www.instagram.com" || url.pathname !== "/oauth/authorize" || url.username || url.password || url.port) throw new Error("Unexpected Instagram authorization URL.");
      if (active.current === scope) setAuthorizationUrl(url.href);
    })}>Prepare account connection</button>{" "}{authorizationUrl && <a href={authorizationUrl} target="_blank" rel="noopener noreferrer">Continue to Instagram authorization</a>}</p>}
    {!connections.length && <p>No Instagram account is connected. An administrator must configure the Meta app, private credentials, registered HTTPS callback and supported permissions. Mock mode remains usable without connecting.</p>}
    {connections.map(c => <p key={c.id}>@{c.username} · account {c.account_id} · v{c.version} · {c.api_version} · {isCurrentConnection(c) ? "Current saved connection" : "Revoked or superseded"}</p>)}
    <p><label>Account for this influencer <select disabled={busy} value={connectionId} onChange={event => setConnectionId(event.target.value)}><option value="">Choose an active account</option>{eligibleConnections.map(c => <option key={c.id} value={c.id}>@{c.username} · {c.account_id}</option>)}</select></label>{" "}
      {admin && selectedConnection && <><button disabled={busy || !connectionReady || !isCurrentConnection(selectedConnection)} onClick={() => action(async () => { await json(`social/connections/${selectedConnection.id}/refresh`, "POST"); await load(); })}>Refresh selected account token</button>{" "}<button disabled={busy} onClick={() => action(async () => { await json(`social/connections/${selectedConnection.id}/revoke`, "POST"); setAuthorizationUrl(""); await load(); })}>Revoke selected connection</button></>}
    </p>
    {operator && <p><button disabled={busy || !approvedRender || !connectionId} onClick={() => action(async () => {
      if (!approvedRender) return;
      const saved = await json<Publish>(`renders/${approvedRender.id}/social-publishes`, "POST", { connection_id: connectionId, idempotency_key: keyFor(`publish:${approvedRender.id}:${connectionId}`) });
      await load(saved.id);
    })}>Prepare / recover exact publication plan</button> {approvedRender ? `Uses approved render ${approvedRender.id}. No external post is sent.` : "Current content and latest visual render must both be human-approved."}</p>}
    <p><label>Publication plan <select disabled={busy} value={publishId} onChange={event => setPublishId(event.target.value)}><option value="">No saved plan selected</option>{publishes.map(row => <option key={row.id} value={row.id}>{row.status} · {row.id}</option>)}</select></label></p>
    {current && <article>
      <h3>{current.status}</h3><p>Account: @{currentConnection?.username ?? "Unknown"} · {current.account_id}. Plan hash: <code>{current.plan_hash}</code></p>
      {!exact && current.status !== "PUBLISHED" && <p>This plan is historical or its parent approvals/account changed. Fresh approval is required before publishing.</p>}
      <p style={{ whiteSpace: "pre-wrap", overflowWrap: "anywhere" }}>{current.plan.caption}</p>
      <button disabled={busy} onClick={() => action(loadPreview)}>Load exact JPEG previews</button>
      {preview?.identity === identity && <div style={{ display: "flex", flexWrap: "wrap", gap: 12 }}>{preview.images.map(image => <figure key={image.index} style={{ margin: 0, width: 250 }}><img src={image.url} alt={`Exact publication slide ${image.index}`} width={250} height={312} onLoad={() => { if (identityRef.current === preview.identity) setLoadedImages(old => old.includes(image.index) ? old : [...old, image.index]); }} onError={() => { setLoadedImages(old => old.filter(index => index !== image.index)); setChecks(emptyChecks()); setMessage("A JPEG could not be decoded; approval is unavailable."); }} /><figcaption>Slide {image.index}</figcaption></figure>)}</div>}
      {approver && current.status === "AWAITING_PUBLISH_APPROVAL" && <fieldset disabled={busy}><legend>Separate publication authorization</legend>
        {([["reviewed_images", "I reviewed every exact JPEG."], ["reviewed_caption", "I reviewed the exact caption and AI disclosure."], ["confirmed_account", `I confirmed the destination account ${current.account_id}.`]] as const).map(([name, label]) => <p key={name}><label><input type="checkbox" disabled={!previewReady || !exact} checked={checks[name]} onChange={event => setChecks(old => ({ ...old, [name]: event.target.checked }))} /> {label}</label></p>)}
        {(["AUTHORIZE_PUBLISH", "REJECT"] as const).map(decision => <button key={decision} disabled={decision === "AUTHORIZE_PUBLISH" && (!exact || !previewReady || !Object.values(checks).every(Boolean))} onClick={() => action(async () => { await json(`social-publishes/${current.id}/review`, "POST", { plan_hash: current.plan_hash, decision, ...checks, comment: null }); setChecks(emptyChecks()); await load(current.id); })}>{decision === "AUTHORIZE_PUBLISH" ? "Authorize exact post" : "Reject publication plan"}</button>)}
      </fieldset>}
      {approver && current.status === "AUTHORIZED" && current.jobs.length === 0 && <p><button disabled={busy} onClick={() => action(async () => { await json(`social-publishes/${current.id}/review`, "POST", { plan_hash: current.plan_hash, decision: "REJECT", ...emptyChecks(), comment: null }); setDispatchAck(""); await load(current.id); })}>Reject undispatched publication</button></p>}
      {operator && ["AUTHORIZED", "PREPARING", "READY", "PUBLISHING"].includes(current.status) && <fieldset disabled={busy || (!recoveringPublication && !exact) || !publishReady}><legend>{recoveringPublication ? "Recover interrupted publication" : "Public action"}</legend>
        {recoveringPublication && <p>Recovery checks the saved provider receipt without posting again. An unconfirmed outcome requires reconciliation.</p>}
        <label><input type="checkbox" checked={dispatchAck === dispatchIdentity} onChange={event => setDispatchAck(event.target.checked ? dispatchIdentity : "")} /> {recoveringPublication ? `I confirm recovery of this interrupted publication for account ${current.account_id}, without reposting.` : `I confirm this exact authorized plan may be posted publicly to account ${current.account_id}.`}</label><p><button disabled={dispatchAck !== dispatchIdentity} onClick={() => action(async () => { setDispatchAck(""); await json(`social-publishes/${current.id}/execute`, "POST", { confirm_public_post: true }); await load(current.id); })}>{recoveringPublication ? "Recover interrupted publication" : "Publish / resume authorized post"}</button></p>
      </fieldset>}
      {current.status === "UNKNOWN_OUTCOME" && <><p>The provider outcome is uncertain. Blind retry is disabled. Inspect Instagram and reconcile only an exact existing post.</p>{approver && <fieldset disabled={busy || !dependencies.connect_enabled}><legend>Confirm an existing external post</legend><label>Published media ID <input value={candidateId} onChange={event => { setCandidateId(event.target.value); setMatched(false); }} maxLength={64} /></label>{" "}<label>Match evidence <input value={matchComment} onChange={event => setMatchComment(event.target.value)} maxLength={2000} /></label><p><label><input type="checkbox" checked={matched} onChange={event => setMatched(event.target.checked)} /> I inspected the exact account, all images and caption on Instagram and confirmed this is the intended post.</label></p><button disabled={!matched || !/^[0-9]{1,64}$/.test(candidateId) || !matchComment.trim()} onClick={() => action(async () => { await json(`social-publishes/${current.id}/reconcile`, "POST", { candidate_media_id: candidateId, confirm_external_match: true, comment: matchComment }); await load(current.id); })}>Record confirmed external match</button></fieldset>}</>}
      {current.status === "PUBLISHED" && <p>Confirmed published media: {current.post_id}. {operator && <button disabled={busy || !dependencies.connect_enabled} onClick={() => action(async () => { await json(`social-publishes/${current.id}/insights`, "POST", { idempotency_key: keyFor(`insights:${current.id}:${current.insights.at(-1)?.id ?? "first"}`) }); await load(current.id); })}>Fetch / recover real post insights</button>}</p>}
      {operator && current.status === "PUBLISHED" && <p><button disabled={busy} onClick={() => action(async () => {
        const selectedIdentity = identity;
        const result = await json<CommentRelink>(`social-publishes/${current.id}/comments/relink`, "POST");
        if (result.publish_run_id !== current.id || result.network_performed !== false) throw new Error("Unexpected saved-comment recovery response.");
        if (active.current === scope && identityRef.current === selectedIdentity && !controller.current?.signal.aborted) setMessage(`Processed ${result.processed} saved comments; linked ${result.linked}. ${result.has_more ? "More saved comments remain; run recovery again for the next batch. " : ""}Refresh Community review to inspect linked comments.`);
      })}>Link / recover saved comments</button> Processes previously received verified comments locally. This does not send a reply.</p>}
      {current.status === "PUBLISHED" && <section aria-label="Observed post performance"><h4>Compare observed post metrics</h4>
        <p>Compare two saved observations from this post. Missing counters stay unknown and decreases remain visible. This describes observed changes; it does not establish their cause or update editorial policy.</p>
        {operator && <fieldset disabled={busy}><label>Earlier observation <select value={baselineId} onChange={event => setBaselineId(event.target.value)}><option value="">Choose baseline</option>{snapshots.map(row => <option key={row.id} value={row.id}>{row.captured_at} · {row.id}</option>)}</select></label>{" "}<label>Later observation <select value={comparisonId} onChange={event => setComparisonId(event.target.value)}><option value="">Choose later sample</option>{snapshots.map(row => <option key={row.id} value={row.id}>{row.captured_at} · {row.id}</option>)}</select></label><p><button disabled={!validPair} onClick={() => action(async () => { await json(`social-publishes/${current.id}/learning`, "POST", { baseline_snapshot_id: baselineId, current_snapshot_id: comparisonId, idempotency_key: keyFor(`learning:${current.id}:${baselineId}:${comparisonId}`) }); await loadLearning(); })}>Save / recover descriptive comparison</button></p>{!validPair && <p>Select two distinct observations in increasing capture-time order.</p>}</fieldset>}
        {learning.map(report => <article key={report.id}><p>{report.status} · {report.payload.baseline_observed_at} → {report.payload.current_observed_at}</p><table><thead><tr><th>Metric</th><th>Earlier</th><th>Later</th><th>Change</th><th>Direction</th></tr></thead><tbody>{Object.entries(report.payload.metrics).map(([name, metric]) => <tr key={name}><th title={report.payload.definitions[name]}>{name}</th><td>{metric.baseline === null ? "Unavailable" : metric.baseline}</td><td>{metric.current === null ? "Unavailable" : metric.current}</td><td>{metric.delta === null ? "Unknown" : metric.delta}</td><td>{metric.direction}</td></tr>)}</tbody></table><p>{report.payload.limitations.join(" · ")}</p></article>)}
      </section>}
      <details><summary>Saved jobs, decisions and scoped insight observations</summary><pre style={{ whiteSpace: "pre-wrap", overflowWrap: "anywhere" }}>{JSON.stringify({ jobs: current.jobs, decisions: current.decisions, insights: current.insights }, null, 2)}</pre></details>
    </article>}
  </section>;
}
