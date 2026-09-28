"use client";
import { useEffect, useMemo, useRef, useState } from "react";
import VisualReview from "../VisualReview";
import SocialPanel from "../SocialPanel";
import MediaPanel from "../MediaPanel";
import CommunityReview from "../CommunityReview";
import ConversionPanel from "../ConversionPanel";
import Icon from "./Icons";
import Modal from "./Modal";
import { carouselFrom, label, request, stateTone, type Artifacts, type Influencer, type Readiness, type Session, type Workflow } from "./types";

function textChoices(artifacts: Artifacts, run: Workflow) {
  const draft = carouselFrom(artifacts, run); if (!draft) return [];
  return [...draft.slides.flatMap((slide, index) => [{ path: `slides.${index}.headline`, ...slide.headline }, { path: `slides.${index}.body`, ...slide.body }]), { path: "caption", ...draft.caption }, { path: "cta", ...draft.cta }];
}
export default function ContentDetail({ session, initialRun, creator, roles, onClose, onChange, onReplace }: { session: Session; initialRun: Workflow; creator?: Influencer; roles: string[]; onClose: () => void; onChange: () => Promise<void>; onReplace?: () => void }) {
  const [run, setRun] = useState(initialRun); const [artifacts, setArtifacts] = useState<Artifacts>({}); const [audit, setAudit] = useState<Record<string, unknown>[]>([]);
  const [readiness, setReadiness] = useState<Readiness | null>(null); const [tab, setTab] = useState("Content"); const [slide, setSlide] = useState(0); const [message, setMessage] = useState(""); const [busy, setBusy] = useState(false); const [loading, setLoading] = useState(true); const [comment, setComment] = useState(""); const [reviewed, setReviewed] = useState(false);
  const abort = useRef<AbortController | null>(null); const lock = useRef(false);
  const operator = roles.includes("OPERATOR") || roles.includes("ADMIN"); const approver = roles.includes("APPROVER") || roles.includes("ADMIN");
  const scope = `${session.tenant}:${session.token}:${initialRun.id}:${operator}:${approver}`;
  const currentScope = useRef(scope); currentScope.current = scope;
  function active(controller: AbortController | null) { return currentScope.current === scope && abort.current === controller && !!controller && !controller.signal.aborted; }
  async function load(controller = abort.current) {
    if (!active(controller)) return;
    const signal = controller?.signal;
    const [current, all, events, score] = await Promise.all([request<Workflow>(session, `workflow-runs/${initialRun.id}`, "GET", undefined, signal), request<Artifacts>(session, `workflow-runs/${initialRun.id}/artifacts`, "GET", undefined, signal), request<Record<string, unknown>[]>(session, `workflow-runs/${initialRun.id}/audit`, "GET", undefined, signal), request<Readiness>(session, `studio/workflow-runs/${initialRun.id}/readiness`, "GET", undefined, signal)]);
    if (!active(controller)) return;
    const savedSource = all.source_snapshots?.find(row => row.id === current.source_snapshot_id);
    setRun({ ...current, title: typeof savedSource?.title === "string" ? savedSource.title : initialRun.title }); setArtifacts(all); setAudit(events); setReadiness(score); setReviewed(false);
  }
  useEffect(() => {
    const controller = new AbortController(); abort.current = controller; lock.current = false;
    setRun(initialRun); setArtifacts({}); setAudit([]); setReadiness(null); setReviewed(false); setComment(""); setSlide(0); setTab("Content"); setMessage(""); setBusy(false); setLoading(true);
    void load(controller).catch(error => { if (active(controller)) setMessage(error.message); }).finally(() => { if (active(controller)) setLoading(false); });
    return () => controller.abort();
  }, [scope]);
  async function act(work: () => Promise<unknown>) {
    const controller = abort.current;
    if (lock.current || !active(controller)) return;
    lock.current = true; setBusy(true); setMessage(""); setReviewed(false);
    try { await work(); if (active(controller)) { await load(controller); if (active(controller)) await onChange(); } }
    catch (error) { if (active(controller)) setMessage(error instanceof Error ? error.message : "The action could not be completed. Refresh saved state."); }
    finally { if (active(controller)) { lock.current = false; setBusy(false); } }
  }
  async function refreshDetail() {
    const controller = abort.current;
    if (!active(controller)) return;
    await load(controller); if (active(controller)) await onChange();
  }
  const draft = carouselFrom(artifacts, run); const currentSlide = draft?.slides[slide] ?? draft?.slides[0];
  const source = artifacts.source_snapshots?.find(row => row.id === run.source_snapshot_id);
  const sourceMetadata = source?.metadata && typeof source.metadata === "object" && !Array.isArray(source.metadata) ? source.metadata as Record<string, unknown> : null;
  const manualClaimType = typeof sourceMetadata?.manual_claim_classification === "string" ? sourceMetadata.manual_claim_classification : null;
  const generatedSource = source?.source_type === "GENERATED" || !!sourceMetadata?.source_draft_policy;
  const sourceVerified = source?.verification_status === "VERIFIED";
  const needsSourceReview = run.state === "SOURCE_CAPTURED" && !sourceVerified && !generatedSource;
  const needsReplacement = source?.is_fixture === true || ["BLOCKED", "REVISION_REQUIRED", "FAILED"].includes(run.state) || ["BLOCKED", "REVISION_REQUIRED"].includes(readiness?.gate ?? "");
  function replaceStory() {
    if (!operator || busy || loading || lock.current || !active(abort.current)) return;
    onReplace?.();
  }
  const replacementAction = operator && onReplace ? <section className="approval-card"><h3>Start a replacement story</h3><p className="muted small">Use corrected source evidence or shorter exact excerpts to address the checks. A replacement starts a new workflow with fresh QA and approval. This story and its review history stay saved.</p><button className="button secondary" disabled={busy || loading} onClick={replaceStory}>Create replacement story<Icon name="arrow" size={16}/></button></section> : null;
  const qaRecord = artifacts.qa_reports?.find(row => row.id === run.qa_report_id);
  const qa = qaRecord?.payload;
  const findings = Array.isArray(qa?.findings) ? qa.findings as { code: string; message: string }[] : [];
  const facts = useMemo(() => {
    const allowed = new Set(textChoices(artifacts, run).filter(block => block.kind === "FACT").flatMap(block => block.fact_ids));
    const rows = artifacts.research_pack_versions?.find(row => row.id === run.research_version_id)?.payload?.facts;
    return Array.isArray(rows) ? rows.filter((row): row is { id: string; statement: string } => !!row && typeof row === "object" && typeof row.id === "string" && typeof row.statement === "string" && allowed.has(row.id)) : [];
  }, [artifacts, run]);
  const approvalReady = !!readiness && ["NOT_READY", "REQUIRES_APPROVAL_CHECKS"].includes(readiness.gate) &&
    readiness.current_ids?.asset_version_id === run.asset_version_id && readiness.current_ids?.research_version_id === run.research_version_id && readiness.current_ids?.qa_report_id === run.qa_report_id &&
    !!run.asset_version_id && !!run.research_version_id && !!run.qa_report_id && qa?.status === "PASS" && findings.length === 0 &&
    qaRecord?.asset_version_id === run.asset_version_id && qaRecord?.research_version_id === run.research_version_id;
  async function decision(kind: "approve" | "reject") {
    if (!approver || run.state !== "AWAITING_APPROVAL" || !comment.trim() || (kind === "approve" && (!reviewed || !approvalReady))) return;
    return request(session, `workflow-runs/${run.id}/${kind}`, "POST", { asset_version_id: run.asset_version_id, research_version_id: run.research_version_id, qa_report_id: run.qa_report_id, comment: comment.trim() || null }, abort.current?.signal);
  }
  const tabNames = ["Content", "Design", "Video", "Publish", "Activity"];
  return <Modal title="Content studio" onClose={onClose} wide><header className="detail-header"><div><span className="eyebrow">{creator?.name ?? "YOUR CREATOR"} / CONTENT STUDIO</span><h2>{run.title || "Your next post"}</h2><span className={`pill ${stateTone(run.state)}`}>{label(run.state)}</span></div><button className="button secondary compact" disabled={busy} onClick={() => void act(async () => {})}>Refresh</button></header>
    <nav className="detail-tabs" aria-label="Content workspace">{tabNames.map(name => <button key={name} className={name === tab ? "active" : ""} aria-current={name === tab ? "page" : undefined} onClick={() => setTab(name)}>{name}</button>)}</nav>
    {message && <p className="notice error" role="alert">{message}</p>}{loading && <p className="loading">Loading the saved story and checks…</p>}
    {!loading && tab === "Content" && <div className="content-detail-grid"><section>
      {generatedSource && <p className="notice"><strong>Generated planning draft saved.</strong> This text is test material, not verified evidence. It cannot be verified or approved for publication. Create a replacement story with real source evidence when you are ready.</p>}
      {currentSlide && draft ? <><div className={`draft-preview category-${creator?.category_id ?? "business"}`}><div className="draft-eyebrow"><span>{creator?.name ?? "AI CREATOR"}</span><span>{String(slide + 1).padStart(2, "0")} / {String(draft.slides.length).padStart(2, "0")}</span></div><h3>{currentSlide.headline.text}</h3><div className="draft-body">{currentSlide.body.text}</div><footer><p>{draft.cta.text}</p><small>{draft.disclosure}</small></footer></div><div className="slide-navigation"><button className="icon-button" disabled={slide === 0} aria-label="Previous slide" onClick={() => setSlide(slide - 1)}><Icon name="chevron" style={{ transform: "rotate(180deg)" }}/></button><span>Slide {slide + 1} of {draft.slides.length}</span><button className="icon-button" disabled={slide >= draft.slides.length - 1} aria-label="Next slide" onClick={() => setSlide(slide + 1)}><Icon name="chevron"/></button></div><p className="small muted">Content preview. Open Design to generate and review the exact export.</p><details className="evidence-details"><summary>Caption & disclosure</summary><p className="preserve-text">{draft.caption.text}</p><p>{draft.disclosure}</p></details></> : <div className="empty-stage"><Icon name="book" size={40}/><h3>The source comes first.</h3><p>Review the supplied evidence, then generate your creator’s draft.</p></div>}
      {source && <details className="evidence-details" open={!draft}><summary>Source evidence · {String(source.title ?? "Original source")}</summary><p className="small muted">{String(source.publisher ?? "")} · {String(source.verification_status ?? "Not verified")}{source.is_fixture ? " · Test fixture" : ""}</p><p className="small muted">Source relationship: {label(String(source.classification ?? "UNKNOWN"))}{manualClaimType ? ` · Submitted claim type: ${label(manualClaimType)}` : ""}</p>{manualClaimType && <p className="small">Verify this classification as well as the source text. Funding, eligibility and time-sensitive claims require the structured evidence workflow.</p>}<p className="source-origin">{String(source.origin ?? "")}</p><pre>{String(source.raw_content ?? "")}</pre></details>}
    </section><aside><section className="readiness-card"><div className="section-kicker"><Icon name="spark"/><strong>Content readiness</strong></div><div className="readiness-number">{readiness?.score ?? "—"}<small>{readiness?.score !== null && readiness ? "/100" : "Not scored yet"}</small></div><p>{readiness?.summary ?? "Create a draft to see actionable checks."}</p><div className="score-components">{readiness?.components.map(component => <details key={component.id}><summary><span>{component.label}</span><strong>{component.score === null ? "—" : `${component.score}/${component.max_points}`}</strong></summary><p>{component.reason}</p>{component.suggestion && <p className="suggestion">{component.suggestion}</p>}</details>)}</div><p className="score-disclaimer">{readiness?.disclaimer ?? "A transparent quality checklist, not a prediction of reach or virality."}</p></section>
      {readiness?.blockers.map(block => <p className="notice error small" key={block.code}>{block.message}</p>)}{findings.map((finding, index) => <p className="notice error small" key={`${finding.code}:${index}`}>{finding.message}</p>)}
      <section className="approval-card"><h3>{generatedSource ? "Real evidence required" : run.state === "SOURCE_CAPTURED" ? sourceVerified ? "Source verified" : "Verify the source" : "Your approval. Your control."}</h3><p className="muted small">{generatedSource ? "This planning draft stays test material. Start a replacement story with a real source to continue toward approval." : run.state === "SOURCE_CAPTURED" ? sourceVerified ? "The exact source has been reviewed. An operator can now generate the draft; content approval follows QA." : "A reviewer must attest the exact source before generating content." : "Review each slide and its evidence. Approval applies only to these saved revisions."}</p>
        {approver && (needsSourceReview || run.state === "AWAITING_APPROVAL") && <><label>Review note<textarea rows={3} maxLength={2000} value={comment} onChange={event => { setComment(event.target.value); setReviewed(false); }} placeholder="What did you verify?"/></label><label className="checkbox-row"><input type="checkbox" checked={reviewed} onChange={event => setReviewed(event.target.checked)}/>I reviewed the exact content and source evidence.</label></>}
        {approver && needsSourceReview && <button className="button primary" disabled={busy || !reviewed || !comment.trim() || !source || source.is_fixture === true} onClick={() => void act(() => request(session, `source-snapshots/${run.source_snapshot_id}/verify`, "POST", { decision: "VERIFIED", comment: comment.trim() }, abort.current?.signal))}>Verify source evidence<Icon name="check" size={16}/></button>}
        {operator && !generatedSource && !["APPROVED", "BLOCKED", "REVISION_REQUIRED", "AWAITING_APPROVAL", "FAILED"].includes(run.state) && <button className="button primary" disabled={busy || source?.verification_status !== "VERIFIED"} onClick={() => void act(() => request(session, `workflow-runs/${run.id}/execute`, "POST", undefined, abort.current?.signal))}>Generate / resume draft<Icon name="spark" size={16}/></button>}
        {approver && run.state === "AWAITING_APPROVAL" && <><button className="button primary" disabled={busy || !reviewed || !comment.trim() || !approvalReady} onClick={() => void act(() => decision("approve"))}>Approve this content<Icon name="check" size={16}/></button><button className="button secondary" disabled={busy || !comment.trim()} onClick={() => void act(() => decision("reject"))}>Request revision</button></>}
        {run.state === "APPROVED" && <button className="button primary" onClick={() => setTab("Design")}>Review the design<Icon name="arrow" size={16}/></button>}
        {!approver && (needsSourceReview || run.state === "AWAITING_APPROVAL") && <p className="notice small">An authorized reviewer must complete this step.</p>}
      </section>
      {needsReplacement && replacementAction}
    </aside></div>}
    {!loading && tab === "Design" && <><div className="legacy-pane"><VisualReview token={session.token} tenant={session.tenant} run={run} operator={operator} approver={approver} refresh={refreshDetail}/></div>{replacementAction && <details className="evidence-details" open={needsReplacement}><summary>Need to change the story or fix text overflow?</summary>{replacementAction}</details>}</>}
    {!loading && tab === "Video" && <div className="legacy-pane"><MediaPanel token={session.token} tenant={session.tenant} run={run} operator={operator} approver={approver} admin={roles.includes("ADMIN")} choices={textChoices(artifacts, run)}/></div>}
    {!loading && tab === "Publish" && <div className="legacy-pane"><SocialPanel token={session.token} tenant={session.tenant} run={run} operator={operator} approver={approver} admin={roles.includes("ADMIN")}/></div>}
    {!loading && tab === "Activity" && <><h3>Every step, accounted for.</h3><ol className="audit-list">{audit.map((event, index) => <li key={String(event.id ?? index)}><span className="audit-dot"/><div><strong>{label(String(event.event_type ?? "Workflow event"))}</strong><small>{typeof event.created_at === "string" ? new Date(event.created_at).toLocaleString() : "Saved event"}</small></div></li>)}</ol><details className="evidence-details"><summary>Community review and consent handoffs</summary><div className="legacy-pane"><CommunityReview token={session.token} tenant={session.tenant} run={run} operator={operator} approver={approver} facts={facts}/><ConversionPanel token={session.token} tenant={session.tenant} workflowId={run.id} operator={operator} approver={approver}/></div></details><details className="evidence-details"><summary>Exact artifacts & audit data</summary><pre>{JSON.stringify({ artifacts, audit }, null, 2)}</pre></details></>}
  </Modal>;
}
