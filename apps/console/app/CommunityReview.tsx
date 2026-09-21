"use client";

import { useCallback, useEffect, useMemo, useRef, useState } from "react";

type Workflow = { id: string; state: string; asset_version_id: string | null; research_version_id: string | null; qa_report_id: string | null };
type Fact = { id: string; statement: string };
type Block = { kind: string; text: string; fact_ids: string[] };
type Review = {
  id: string; revision: number; status: string; asset_version_id: string; research_version_id: string; qa_report_id: string;
  classification: Record<string, unknown> | null;
  draft: { language: string; blocks: Block[]; disclosure: string } | null;
  qa: { status: string; findings: { code: string; message: string; severity: string }[] } | null;
  draft_hash: string | null; qa_hash: string | null; retry_at?: string | null;
  decisions: { id: string; decision: string; comment: string | null }[];
  attempts: { id: string; skill_identifier: string; status: string; attempt: number; error_category: string | null }[];
};
type Event = { id: string; mode: string; origin: string; participant_reference: string; comment_text: string; captured_at: string; reviews?: Review[] };
type Props = { token: string; tenant: string; run: Workflow; operator: boolean; approver: boolean; facts: Fact[] };

export default function CommunityReview({ token, tenant, run, operator, approver, facts }: Props) {
  const [events, setEvents] = useState<Event[]>([]);
  const [selected, setSelected] = useState("");
  const [detail, setDetail] = useState<Event | null>(null);
  const [mode, setMode] = useState("FIXTURE");
  const [origin, setOrigin] = useState("");
  const [participant, setParticipant] = useState("");
  const [commentText, setCommentText] = useState("");
  const [capturedAt, setCapturedAt] = useState("");
  const [factIds, setFactIds] = useState<string[]>([]);
  const [reviewComment, setReviewComment] = useState("");
  const [acknowledged, setAcknowledged] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [message, setMessage] = useState("");
  const scope = useMemo(() => ({}), [tenant, token, run.id, run.asset_version_id, run.research_version_id, run.qa_report_id, run.state]);
  const active = useRef(scope); active.current = scope;
  const selectedRef = useRef(selected); selectedRef.current = selected;
  const controller = useRef<AbortController | null>(null);
  const keys = useRef(new Map<string, string>());

  const request = useCallback(async <T,>(path: string, method = "GET", body?: unknown): Promise<T> => {
    const response = await fetch(`/api/internal/${path}`, {
      method, cache: "no-store", signal: controller.current?.signal,
      headers: { Authorization: `Bearer ${token}`, "X-Tenant-ID": tenant, "Content-Type": "application/json" },
      body: body === undefined ? undefined : JSON.stringify(body),
    });
    if (response.headers.get("content-type")?.split(";")[0] !== "application/json") throw new Error(`Community request failed (${response.status}).`);
    const result = await response.json();
    if (!response.ok) throw new Error(typeof result.detail === "string" ? result.detail : `Community request failed (${response.status}).`);
    return result as T;
  }, [tenant, token]);

  const load = useCallback(async (preferred?: string) => {
    const rows = await request<Event[]>(`workflow-runs/${run.id}/community-events`);
    if (active.current !== scope || controller.current?.signal.aborted) return;
    setEvents(rows);
    setSelected(previous => rows.some(row => row.id === preferred) ? preferred! : rows.some(row => row.id === previous) ? previous : rows[0]?.id ?? "");
  }, [request, run.id, scope]);

  const loadDetail = useCallback(async () => {
    if (!selected) return;
    const row = await request<Event>(`community-events/${selected}`);
    if (active.current === scope && selectedRef.current === selected && !controller.current?.signal.aborted) setDetail(row);
  }, [request, selected, scope]);

  useEffect(() => {
    controller.current = new AbortController(); keys.current.clear();
    setEvents([]); setSelected(""); setDetail(null); setMessage(""); setBusy(false);
    setMode("FIXTURE"); setOrigin(""); setParticipant(""); setCommentText(""); setCapturedAt(new Date().toISOString());
    setFactIds([]); setReviewComment(""); setAcknowledged(null);
    load().catch(error => { if (active.current === scope && !controller.current?.signal.aborted) setMessage(error instanceof Error ? error.message : "Could not load comments."); });
    return () => controller.current?.abort();
  }, [load, scope]);

  useEffect(() => {
    let cancelled = false;
    setDetail(null); setFactIds([]); setReviewComment(""); setAcknowledged(null);
    loadDetail().catch(error => { if (!cancelled && active.current === scope && !controller.current?.signal.aborted) setMessage(error instanceof Error ? error.message : "Could not load reply reviews."); });
    return () => { cancelled = true; };
  }, [loadDetail, scope]);

  async function action(work: () => Promise<void>) {
    const captured = scope;
    setBusy(true); setMessage("");
    try { await work(); }
    catch (error) { if (active.current === captured && !controller.current?.signal.aborted) setMessage(error instanceof Error ? error.message : "Community request failed."); }
    finally { if (active.current === captured) setBusy(false); }
  }

  async function save(path: string, body: Record<string, unknown>) {
    const signature = JSON.stringify({ path, body });
    if (!keys.current.has(signature)) keys.current.set(signature, crypto.randomUUID());
    return request<{ id: string }>(path, "POST", { ...body, idempotency_key: keys.current.get(signature) });
  }

  const reviews = [...(detail?.reviews ?? [])].sort((a, b) => b.revision - a.revision);
  const latest = reviews[0];
  const parentApproved = run.state === "APPROVED";
  useEffect(() => { setAcknowledged(null); setReviewComment(""); }, [latest?.id, latest?.draft_hash, latest?.qa_hash]);

  return <section aria-label="Community reply review" style={{ borderTop: "1px solid #ccc", marginTop: 20 }}>
    <h2>Comment reply review</h2>
    <p><strong>Draft only — nothing sent.</strong> Manually submitted comments are not verified platform events. Fixture comments are synthetic and cannot be approved. Ambiguous requests require human review; no eligibility or consent is inferred.</p>
    <button disabled={busy} onClick={() => action(async () => { await load(); await loadDetail(); })}>Refresh comment reviews</button>
    <p role="status">{busy ? "Working on saved reply review…" : message}</p>
    {operator && <details><summary>Submit a comment for internal review</summary><fieldset disabled={busy}>
      <p><label>Origin type <select value={mode} onChange={e => setMode(e.target.value)}><option value="FIXTURE">FIXTURE — synthetic testing</option><option value="MANUAL">MANUAL — operator supplied</option></select></label></p>
      <p><label>Source / origin <input value={origin} maxLength={512} onChange={e => setOrigin(e.target.value)} /></label></p>
      <p><label>Opaque participant reference <input value={participant} maxLength={128} onChange={e => setParticipant(e.target.value)} /></label></p>
      <p>Use a local reference; do not paste contact details or credentials.</p>
      <p><label>Captured time (UTC ISO format) <input value={capturedAt} onChange={e => setCapturedAt(e.target.value)} /></label>{" "}<button onClick={() => setCapturedAt(new Date().toISOString())}>Use current UTC time</button></p>
      <label>Exact comment text <textarea value={commentText} maxLength={4000} onChange={e => setCommentText(e.target.value)} /></label>
      <p><button disabled={!origin.trim() || !participant.trim() || !commentText.trim() || !capturedAt} onClick={() => action(async () => {
        const row = await save(`workflow-runs/${run.id}/community-events`, { schema_version: 1, mode, origin, participant_reference: participant, comment_text: commentText, captured_at: capturedAt });
        await load(row.id);
      })}>Save comment</button></p>
    </fieldset></details>}
    {events.length === 0 ? <p>No comments saved for this workflow.</p> : <p><label>Saved comment <select disabled={busy} value={selected} onChange={e => setSelected(e.target.value)}>{events.map(row => <option key={row.id} value={row.id}>{row.mode} · {row.id}</option>)}</select></label></p>}
    {detail && detail.id === selected && <>
      <p><strong>{detail.mode === "FIXTURE" ? "SYNTHETIC FIXTURE" : "OPERATOR-SUPPLIED COMMENT"}</strong> · {detail.origin}</p>
      <blockquote style={{ whiteSpace: "pre-wrap", overflowWrap: "anywhere" }}>{detail.comment_text}</blockquote>
      {operator && <fieldset disabled={busy || !parentApproved}><legend>Prepare a reply review</legend>
        <p>Select up to five source excerpts for a source request. Acknowledgements use only the configured creative response and require no facts.</p>
        {facts.map(fact => <p key={fact.id}><label><input type="checkbox" checked={factIds.includes(fact.id)} disabled={!factIds.includes(fact.id) && factIds.length >= 5} onChange={e => setFactIds(previous => e.target.checked ? [...previous, fact.id] : previous.filter(id => id !== fact.id))} /> {fact.statement} <small>({fact.id})</small></label></p>)}
        <button onClick={() => action(async () => { await save(`community-events/${selected}/reviews`, { fact_ids: [...factIds].sort() }); await loadDetail(); setAcknowledged(null); })}>Create / recover reply draft</button>{" "}
        <button onClick={() => { for (const key of keys.current.keys()) if (key.includes(`community-events/${selected}/reviews`)) keys.current.delete(key); setMessage("A new review request is prepared. Creating it will require a new human decision."); setAcknowledged(null); }}>Prepare new review request</button>
      </fieldset>}
      {!parentApproved && <p>Reply preparation and approval require current approved parent content. Historical reviews remain visible.</p>}
      {reviews.map(review => {
        const exact = review.asset_version_id === run.asset_version_id && review.research_version_id === run.research_version_id && review.qa_report_id === run.qa_report_id;
        const canDecide = approver && review.id === latest?.id && parentApproved && exact && review.status === "AWAITING_REVIEW" && review.qa?.status === "PASS" && detail.mode === "MANUAL";
        const reviewIdentity = `${review.id}:${review.draft_hash}:${review.qa_hash}`;
        return <article key={review.id} style={{ border: "1px solid #ddd", padding: 12, marginTop: 12 }}>
          <h3>Review {review.revision} · {review.status}</h3>
          <p style={{ overflowWrap: "anywhere" }}>{review.id}{!exact && " · Historical parent revision"}</p>
          <pre style={{ whiteSpace: "pre-wrap" }}>{JSON.stringify(review.classification, null, 2)}</pre>
          {review.draft && <><div>{review.draft.blocks.map((block, index) => <p key={index} style={{ whiteSpace: "pre-wrap", overflowWrap: "anywhere" }}>{block.text}{block.fact_ids.length > 0 && <small> · Facts: {block.fact_ids.join(", ")}</small>}</p>)}</div><p><strong>{review.draft.disclosure}</strong></p></>}
          {review.qa && <><p>Reply QA: <strong>{review.qa.status}</strong></p>{review.qa.findings.map((finding, index) => <p key={index}>{finding.severity} · {finding.code}: {finding.message}</p>)}</>}
          {operator && !["HUMAN_REVIEW", "BLOCKED", "REVISION_REQUIRED", "FAILED", "AWAITING_REVIEW", "REVIEWED_DRAFT", "REJECTED"].includes(review.status) && <button disabled={busy} onClick={() => action(async () => { await request(`community-reviews/${review.id}/execute`, "POST"); await loadDetail(); })}>Resume saved review</button>}
          {review.retry_at && <p>Retry after: {review.retry_at}</p>}
          {canDecide && <fieldset disabled={busy}>
            <label><input type="checkbox" checked={acknowledged === reviewIdentity} onChange={e => setAcknowledged(e.target.checked ? reviewIdentity : null)} /> I reviewed this exact reply, source excerpts and AI disclosure.</label>
            <p><label>Decision comment <input value={reviewComment} maxLength={2000} onChange={e => setReviewComment(e.target.value)} /></label></p>
            {(["approve", "reject"] as const).map(decision => <button key={decision} disabled={acknowledged !== reviewIdentity} onClick={() => action(async () => { await request(`community-reviews/${review.id}/${decision}`, "POST", { draft_hash: review.draft_hash, qa_hash: review.qa_hash, comment: reviewComment || null }); await loadDetail(); setAcknowledged(null); })}>{decision === "approve" ? "Approve exact draft (no sending)" : "Reject reply draft"}</button>)}
          </fieldset>}
          {review.decisions?.map(row => <p key={row.id}>Human decision: {row.decision} · {row.comment ?? "No comment"}</p>)}
          <details><summary>Attempts and exact revisions</summary><pre style={{ whiteSpace: "pre-wrap", overflowWrap: "anywhere" }}>{JSON.stringify({ asset_version_id: review.asset_version_id, research_version_id: review.research_version_id, qa_report_id: review.qa_report_id, draft_hash: review.draft_hash, qa_hash: review.qa_hash, attempts: review.attempts }, null, 2)}</pre></details>
        </article>;
      })}
    </>}
  </section>;
}
