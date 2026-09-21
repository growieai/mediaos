"use client";

import { useState } from "react";
import VisualReview from "./VisualReview";
import CommunityReview from "./CommunityReview";

type Run = { id: string; state: string; source_snapshot_id: string; asset_version_id: string | null; research_version_id: string | null; qa_report_id: string | null };
type Identity = { memberships: { roles: string[] }[]; influencers: { id: string; name: string }[]; missions: { id: string; name: string }[] };
type Artifact = { id: string; payload?: Record<string, unknown>; [key: string]: unknown };

function replyFacts(artifacts: Record<string, Artifact[]>, run: Run) {
  const claimed = new Set<string>();
  function visit(value: unknown) {
    if (Array.isArray(value)) { value.forEach(visit); return; }
    if (!value || typeof value !== "object") return;
    const row = value as Record<string, unknown>;
    if (row.kind === "FACT" && Array.isArray(row.fact_ids)) row.fact_ids.forEach(id => { if (typeof id === "string") claimed.add(id); });
    Object.values(row).forEach(visit);
  }
  visit(artifacts.content_asset_versions?.find(row => row.id === run.asset_version_id)?.payload);
  const facts = artifacts.research_pack_versions?.find(row => row.id === run.research_version_id)?.payload?.facts;
  if (!Array.isArray(facts)) return [];
  return facts.filter((fact): fact is { id: string; statement: string } => !!fact && typeof fact === "object" && typeof fact.id === "string" && typeof fact.statement === "string" && claimed.has(fact.id));
}

export default function Home() {
  const [tenant, setTenant] = useState("");
  const [token, setToken] = useState("");
  const [runs, setRuns] = useState<Run[]>([]);
  const [run, setRun] = useState<Run | null>(null);
  const [identity, setIdentity] = useState<Identity | null>(null);
  const [artifacts, setArtifacts] = useState<Record<string, Artifact[]>>({});
  const [audit, setAudit] = useState<unknown[]>([]);
  const [message, setMessage] = useState("");
  const [busy, setBusy] = useState(false);
  const [submission, setSubmission] = useState("");
  const [comment, setComment] = useState("");
  const [sources, setSources] = useState<Artifact[]>([]);
  const [audiences, setAudiences] = useState<Artifact[]>([]);
  const [opportunities, setOpportunities] = useState<Artifact[]>([]);
  const [ingestionRequest, setIngestionRequest] = useState("");
  const [intelligenceResult, setIntelligenceResult] = useState<unknown>(null);
  const [influencerId, setInfluencerId] = useState("");
  const [missionId, setMissionId] = useState("");
  const [audienceId, setAudienceId] = useState("");

  async function api(path: string, method = "GET", body?: unknown) {
    const response = await fetch("/api/internal/" + path, {
      method, headers: { Authorization: "Bearer " + token, "X-Tenant-ID": tenant, "Content-Type": "application/json" },
      body: body === undefined ? undefined : JSON.stringify(body), cache: "no-store",
    });
    const result = await response.json();
    if (!response.ok) throw new Error(result.detail || "Request failed");
    return result;
  }
  async function action(work: () => Promise<void>) {
    setBusy(true); setMessage("");
    try { await work(); } catch (error) { setMessage(error instanceof Error ? error.message : "Request failed"); }
    finally { setBusy(false); }
  }
  async function refresh(id?: string) {
    const currentIdentity: Identity = await api("context");
    setIdentity(currentIdentity);
    setInfluencerId(currentIdentity.influencers[0]?.id ?? "");
    setMissionId(currentIdentity.missions[0]?.id ?? "");
    const sourceRows: Artifact[] = await api("intelligence/sources");
    const audienceRows: Artifact[] = await api("intelligence/audiences");
    setSources(sourceRows); setAudiences(audienceRows);
    setAudienceId(audienceRows.find(a => a.code === "GENERIC_SMB")?.id ?? audienceRows[0]?.id ?? "");
    setOpportunities(await api("intelligence/opportunities"));
    if (!ingestionRequest && sourceRows.some(s => s.enabled)) {
      const until = new Date(); const since = new Date(until.getTime() - 14 * 86400000);
      setIngestionRequest(JSON.stringify({ source_definition_id: sourceRows.find(s => s.enabled)?.id, idempotency_key: crypto.randomUUID(), mode: "MANUAL", since: since.toISOString().slice(0, 10), until: until.toISOString().slice(0, 10), query: "", max_pages: 1, page_size: 5 }, null, 2));
    }
    setRuns(await api("workflow-runs"));
    if (id) {
      const current = await api("workflow-runs/" + id);
      setRun(current);
      setArtifacts(await api("workflow-runs/" + id + "/artifacts"));
      setAudit(await api("workflow-runs/" + id + "/audit"));
    }
  }
  const roles = identity?.memberships.flatMap(m => m.roles) ?? [];
  const approver = roles.includes("APPROVER") || roles.includes("ADMIN");
  const operator = roles.includes("OPERATOR") || roles.includes("ADMIN");
  const waiting = run?.state === "AWAITING_APPROVAL";
  async function decision(kind: "approve" | "reject") {
    if (!run) return;
    await api("workflow-runs/" + run.id + "/" + kind, "POST", {
      asset_version_id: run.asset_version_id, research_version_id: run.research_version_id,
      qa_report_id: run.qa_report_id, comment: comment || null,
    });
    await refresh(run.id);
  }

  return <main style={{ maxWidth: 1100, margin: "auto", padding: 24 }}>
    <h1>Growie Media OS · Internal console</h1>
    <p>Discover official opportunities and review sourced drafts. Deterministic extraction and drafting. Human approval required.</p>
    <fieldset disabled={busy}>
      <legend>Internal identity</legend>
      <label>Tenant UUID <input value={tenant} onChange={e => { setTenant(e.target.value); setIdentity(null); setRun(null); setArtifacts({}); setAudit([]); setRuns([]); setSources([]); setAudiences([]); setOpportunities([]); setIntelligenceResult(null); setIngestionRequest(""); }} /></label>{" "}
      <label>Bearer token <input type="password" autoComplete="off" value={token} onChange={e => { setToken(e.target.value); setIdentity(null); setRun(null); setArtifacts({}); setAudit([]); setRuns([]); setSources([]); setAudiences([]); setOpportunities([]); setIntelligenceResult(null); setIngestionRequest(""); }} /></label>{" "}
      <button onClick={() => action(() => refresh())}>Connect / refresh</button>
    </fieldset>
    <p role="status">{busy ? "Working…" : message}</p>
    {identity && <p>Roles: {roles.join(", ")}. Credentials stay in page memory.</p>}
    {operator && <details><summary>Submit manual source</summary>
      <p>Paste the typed request documented in LOCAL_DEVELOPMENT. New sources require reviewer attestation before execution.</p>
      <textarea aria-label="Workflow request JSON" value={submission} onChange={e => setSubmission(e.target.value)} rows={10} style={{ width: "100%" }} />
      <button disabled={busy} onClick={() => action(async () => { const created = await api("workflow-runs", "POST", JSON.parse(submission)); await refresh(created.id); })}>Create workflow</button>
    </details>}
    {operator && <details><summary>Official-source intelligence</summary>
      <p>Manual, bounded ingestion. Reusing the request key resumes or returns its saved run.</p>
      {sources.map(s => <p key={s.id}>{String(s.source_key)} · {s.enabled ? "Enabled" : "Permission required; automatic access disabled"} · {s.id}</p>)}
      <textarea aria-label="Ingestion request JSON" rows={10} value={ingestionRequest} onChange={e => setIngestionRequest(e.target.value)} style={{ width: "100%" }} />
      <button disabled={busy} onClick={() => action(async () => {
        const created = await api("intelligence/ingestions", "POST", JSON.parse(ingestionRequest));
        setIntelligenceResult(created);
        setIntelligenceResult(await api(`intelligence/ingestions/${created.id}/execute`, "POST"));
        await refresh();
      })}>Discover / resume ingestion</button>
      <p><label>Influencer <select value={influencerId} onChange={e => setInfluencerId(e.target.value)}>{identity?.influencers.map(i => <option key={i.id} value={i.id}>{i.name}</option>)}</select></label>{" "}
      <label>Mission <select value={missionId} onChange={e => setMissionId(e.target.value)}>{identity?.missions.map(m => <option key={m.id} value={m.id}>{m.name}</option>)}</select></label>{" "}
      <label>Audience <select value={audienceId} onChange={e => setAudienceId(e.target.value)}>{audiences.map(a => <option key={a.id} value={a.id}>{String(a.code)}</option>)}</select></label></p>
      {opportunities.map(o => <p key={o.id}>{String(o.canonical_external_id)} · {String((o.current_version as Record<string, unknown>)?.title ?? "")}{" "}
        <button disabled={busy} onClick={() => action(async () => setIntelligenceResult(await api(`intelligence/opportunities/${o.id}`)))}>Evidence / versions</button>{" "}
        <button disabled={busy || !missionId} onClick={() => action(async () => setIntelligenceResult(await api(`intelligence/opportunities/${o.id}/evaluate`, "POST", { mission_id: missionId })))}>Score audiences</button>{" "}
        <button disabled={busy || !missionId || !audienceId || !influencerId} onClick={() => action(async () => {
          const created = await api(`intelligence/opportunities/${o.id}/workflow`, "POST", { influencer_id: influencerId, mission_id: missionId, audience_segment_id: audienceId, idempotency_key: `editorial:${o.id}:${missionId}:${audienceId}` });
          await refresh(created.id);
        })}>Create sourced draft</button>
      </p>)}
      <pre style={{ whiteSpace: "pre-wrap", overflowWrap: "anywhere" }}>{JSON.stringify(intelligenceResult, null, 2)}</pre>
    </details>}
    <h2>Workflow runs</h2>
    {runs.length === 0 && <p>No runs loaded.</p>}
    {runs.map(r => <p key={r.id}><button disabled={busy} onClick={() => action(() => refresh(r.id))}>{r.id}</button> · {r.state}</p>)}
    {run && <section>
      <h2>{run.state}</h2>
      <p>Run: {run.id}</p>
      <p>{waiting ? "QA passed. An authorized human must approve these exact revisions." : "Approval is available only after current-revision QA passes."}</p>
      <label>Review comment <input value={comment} onChange={e => setComment(e.target.value)} /></label>{" "}
      {approver && run.state === "SOURCE_CAPTURED" && <button disabled={busy || !comment.trim()} onClick={() => action(async () => {
        await api("source-snapshots/" + run.source_snapshot_id + "/verify", "POST", { decision: "VERIFIED", comment });
        await refresh(run.id);
      })}>Attest source evidence</button>}
      {operator && <button disabled={busy || ["APPROVED", "BLOCKED", "REVISION_REQUIRED", "AWAITING_APPROVAL", "FAILED"].includes(run.state)} onClick={() => action(async () => { await api("workflow-runs/" + run.id + "/execute", "POST"); await refresh(run.id); })}>Execute / resume</button>}
      {approver && <><button disabled={busy || !waiting} onClick={() => action(() => decision("approve"))}>Approve exact revisions</button>{" "}
      <button disabled={busy || !waiting} onClick={() => action(() => decision("reject"))}>Reject</button></>}
      <VisualReview key={`${tenant}:${run.id}`} token={token} tenant={tenant} run={run} operator={operator} approver={approver} refresh={id => action(() => refresh(id))} />
      <CommunityReview key={`community:${tenant}:${run.id}`} token={token} tenant={tenant} run={run} operator={operator} approver={approver} facts={replyFacts(artifacts, run)} />
      {Object.entries(artifacts).map(([name, rows]) => <details key={name} open={["content_asset_versions", "qa_reports"].includes(name)}>
        <summary>{name} ({rows.length})</summary>
        <pre style={{ whiteSpace: "pre-wrap", overflowWrap: "anywhere", background: "#fff", padding: 12 }}>{JSON.stringify(rows, null, 2)}</pre>
      </details>)}
      <details><summary>Audit trail</summary><pre style={{ whiteSpace: "pre-wrap" }}>{JSON.stringify(audit, null, 2)}</pre></details>
    </section>}
  </main>;
}
