"use client";

import { useState } from "react";

type Run = { id: string; state: string; source_snapshot_id: string; asset_version_id: string | null; research_version_id: string | null; qa_report_id: string | null };
type Identity = { memberships: { roles: string[] }[] };
type Artifact = { id: string; payload?: Record<string, unknown>; [key: string]: unknown };

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
    setIdentity(await api("context"));
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
    <p>Review persisted sources, evidence and exact content revisions. AI mock mode. Human approval required.</p>
    <fieldset disabled={busy}>
      <legend>Internal identity</legend>
      <label>Tenant UUID <input value={tenant} onChange={e => { setTenant(e.target.value); setIdentity(null); setRun(null); setArtifacts({}); setAudit([]); setRuns([]); }} /></label>{" "}
      <label>Bearer token <input type="password" autoComplete="off" value={token} onChange={e => { setToken(e.target.value); setIdentity(null); setRun(null); setArtifacts({}); setAudit([]); setRuns([]); }} /></label>{" "}
      <button onClick={() => action(() => refresh())}>Connect / refresh</button>
    </fieldset>
    <p role="status">{busy ? "Working…" : message}</p>
    {identity && <p>Roles: {roles.join(", ")}. Credentials stay in page memory.</p>}
    {operator && <details><summary>Submit manual source</summary>
      <p>Paste the typed request documented in LOCAL_DEVELOPMENT. New sources require reviewer attestation before execution.</p>
      <textarea aria-label="Workflow request JSON" value={submission} onChange={e => setSubmission(e.target.value)} rows={10} style={{ width: "100%" }} />
      <button disabled={busy} onClick={() => action(async () => { const created = await api("workflow-runs", "POST", JSON.parse(submission)); await refresh(created.id); })}>Create workflow</button>
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
      {Object.entries(artifacts).map(([name, rows]) => <details key={name} open={["content_asset_versions", "qa_reports"].includes(name)}>
        <summary>{name} ({rows.length})</summary>
        <pre style={{ whiteSpace: "pre-wrap", overflowWrap: "anywhere", background: "#fff", padding: 12 }}>{JSON.stringify(rows, null, 2)}</pre>
      </details>)}
      <details><summary>Audit trail</summary><pre style={{ whiteSpace: "pre-wrap" }}>{JSON.stringify(audit, null, 2)}</pre></details>
    </section>}
  </main>;
}
