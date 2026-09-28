"use client";
import { useEffect, useRef, useState } from "react";
import Icon from "./Icons";
import Modal from "./Modal";
import { label, request, type Influencer, type Session, type Workflow } from "./types";
type Source = { id: string; source_key: string; enabled: boolean };
type Audience = { id: string; code: string };
type Opportunity = { id: string; canonical_external_id: string; current_version: { id: string; title: string; status: string; closing_date: string | null } };
type Evaluation = { audience_segment: Audience; score: { opportunity_version_id: string; payload: { final_score: number; eligibility: string } }; decision: { decision: string; payload: { reasons: string[] } } };

export async function discoveryWorkflowKey(opportunityId: string, versionId: string, influencerId: string, missionId: string, audienceId: string) {
  const digest = await crypto.subtle.digest("SHA-256", new TextEncoder().encode(JSON.stringify([opportunityId, versionId, influencerId, missionId, audienceId])));
  return `studio-opportunity:${Array.from(new Uint8Array(digest), value => value.toString(16).padStart(2, "0")).join("")}`;
}

export default function Discovery({ session, creators, operator, onClose, onCreated }: { session: Session; creators: Influencer[]; operator: boolean; onClose: () => void; onCreated: (run: Workflow) => Promise<void> }) {
  const [sources, setSources] = useState<Source[]>([]); const [audiences, setAudiences] = useState<Audience[]>([]); const [opportunities, setOpportunities] = useState<Opportunity[]>([]);
  const [sourceId, setSourceId] = useState(""); const [creatorId, setCreatorId] = useState(creators[0]?.id ?? ""); const [audienceId, setAudienceId] = useState(""); const [query, setQuery] = useState(""); const [search, setSearch] = useState("");
  const [message, setMessage] = useState(""); const [busy, setBusy] = useState(false); const [evaluated, setEvaluated] = useState<{ id: string; version: string; creator: string; rows: Evaluation[] } | null>(null);
  const scope = useRef<AbortController | null>(null); const lock = useRef(false); const ingestions = useRef(new Map<string, { key: string; since: string; until: string; id?: string }>());
  const identity = `${session.tenant}:${session.token}:${operator}`;
  const currentIdentity = useRef(identity); currentIdentity.current = identity;
  const selection = useRef({ creatorId, audienceId, sourceId, query }); selection.current = { creatorId, audienceId, sourceId, query };
  const latest = useRef(opportunities); latest.current = opportunities;
  function active(controller: AbortController) { return currentIdentity.current === identity && scope.current === controller && !controller.signal.aborted; }
  useEffect(() => {
    const controller = new AbortController(); scope.current = controller; lock.current = false; ingestions.current.clear();
    setSources([]); setAudiences([]); setOpportunities([]); setSourceId(""); setAudienceId(""); setCreatorId(creators[0]?.id ?? ""); setQuery(""); setSearch(""); setEvaluated(null); setMessage(""); setBusy(false);
    void Promise.all([request<Source[]>(session, "intelligence/sources", "GET", undefined, controller.signal), request<Audience[]>(session, "intelligence/audiences", "GET", undefined, controller.signal), request<Opportunity[]>(session, "intelligence/opportunities", "GET", undefined, controller.signal)]).then(([sourceRows, audienceRows, rows]) => {
      if (!active(controller)) return; setSources(sourceRows); setSourceId(sourceRows.find(source => source.enabled)?.id ?? ""); setAudiences(audienceRows); setAudienceId(audienceRows.find(audience => audience.code === "GENERIC_SMB")?.id ?? audienceRows[0]?.id ?? ""); setOpportunities(rows);
    }).catch(error => { if (active(controller)) setMessage(error.message); });
    return () => controller.abort();
  }, [identity]);
  async function act(work: (controller: AbortController) => Promise<void>) {
    const controller = scope.current; if (!controller || !active(controller) || lock.current || !operator) return;
    lock.current = true; setBusy(true); setMessage("");
    try { await work(controller); } catch (error) { if (active(controller)) setMessage(error instanceof Error ? error.message : "Could not complete discovery."); }
    finally { if (active(controller)) { lock.current = false; setBusy(false); } }
  }
  async function discover(controller: AbortController) {
    if (!sources.some(source => source.id === sourceId && source.enabled) || selection.current.sourceId !== sourceId || selection.current.query !== query) return;
    if (Array.from(query.trim()).length > 150) throw new Error("Search terms must be at most 150 characters.");
    const signature = JSON.stringify([sourceId, query.trim()]);
    let captured = ingestions.current.get(signature);
    if (!captured) { const now = new Date(); captured = { key: crypto.randomUUID(), until: now.toISOString().slice(0, 10), since: new Date(now.getTime() - 14 * 86400000).toISOString().slice(0, 10) }; ingestions.current.set(signature, captured); }
    if (!captured.id) { const run = await request<{ id: string }>(session, "intelligence/ingestions", "POST", { source_definition_id: sourceId, idempotency_key: captured.key, mode: "MANUAL", since: captured.since, until: captured.until, query: query.trim(), max_pages: 1, page_size: 5 }, controller.signal); captured.id = run.id; }
    if (!active(controller)) return;
    await request(session, `intelligence/ingestions/${captured.id}/execute`, "POST", undefined, controller.signal);
    if (!active(controller)) return;
    const rows = await request<Opportunity[]>(session, "intelligence/opportunities", "GET", undefined, controller.signal);
    if (active(controller)) { setOpportunities(rows); setEvaluated(null); setMessage("Discovery cycle saved. Review the opportunities and their audience checks below."); }
  }
  const creator = creators.find(row => row.id === creatorId);
  function currentSelection(row: Opportunity) {
    return !!creator && creator.opportunity_discovery && selection.current.creatorId === creatorId &&
      latest.current.some(current => current.id === row.id && current.current_version.id === row.current_version.id);
  }
  async function evaluate(row: Opportunity, controller: AbortController) {
    if (!currentSelection(row)) return;
    const rows = await request<Evaluation[]>(session, `intelligence/opportunities/${row.id}/evaluate`, "POST", { mission_id: creator!.mission_id }, controller.signal);
    if (active(controller) && currentSelection(row)) setEvaluated({ id: row.id, version: row.current_version.id, creator: creatorId, rows });
  }
  async function createDraft(row: Opportunity, controller: AbortController) {
    const review = evaluated?.id === row.id && evaluated.version === row.current_version.id && evaluated.creator === creatorId ? evaluated.rows.find(item => item.audience_segment.id === audienceId && item.score.opportunity_version_id === row.current_version.id) : null;
    if (!currentSelection(row) || selection.current.audienceId !== audienceId || !audiences.some(audience => audience.id === audienceId) || review?.decision.decision !== "CREATE_CONTENT") return;
    const key = await discoveryWorkflowKey(row.id, row.current_version.id, creator!.id, creator!.mission_id, audienceId);
    if (!active(controller) || !currentSelection(row) || selection.current.audienceId !== audienceId) return;
    const run = await request<Workflow>(session, `intelligence/opportunities/${row.id}/workflow`, "POST", { influencer_id: creator!.id, mission_id: creator!.mission_id, audience_segment_id: audienceId, idempotency_key: key }, controller.signal);
    if (active(controller)) await onCreated(run);
  }
  const visible = opportunities.filter(row => `${row.current_version.title} ${row.canonical_external_id}`.toLowerCase().includes(search.toLowerCase())).slice(0, 40);
  return <Modal title="Discover opportunities" onClose={onClose} wide><div className="composer-heading"><span className="eyebrow">FROM OFFICIAL SOURCE TO USEFUL STORY</span><h2>Find the story behind the opportunity.</h2><p className="muted">Discover from enabled official sources. Evidence, eligibility and freshness checks decide whether an opportunity can become content.</p></div><fieldset className="discovery-controls" disabled={busy || !operator}><label>Official source<select value={sourceId} onChange={event => setSourceId(event.target.value)}>{sources.map(source => <option value={source.id} disabled={!source.enabled} key={source.id}>{source.source_key}{source.enabled ? "" : " · access not enabled"}</option>)}</select></label><label>Search terms<input value={query} maxLength={150} onChange={event => setQuery(event.target.value)} placeholder="Optional programme keyword"/></label><button className="button primary" disabled={!sourceId || busy || !operator} onClick={() => void act(discover)}><Icon name="globe" size={17}/>{busy ? "Working…" : "Discover / resume"}</button></fieldset><p className="small muted">Fetches one bounded page of up to five records, using the last 14 days. Repeating the same request resumes its saved cycle.</p>{message && <p className="notice" role="status">{message}</p>}<div className="form-two"><label>Influencer<select value={creatorId} disabled={busy} onChange={event => { setCreatorId(event.target.value); setEvaluated(null); }}>{creators.map(row => <option value={row.id} key={row.id}>{row.name}</option>)}</select></label><label>Audience<select value={audienceId} disabled={busy} onChange={event => setAudienceId(event.target.value)}>{audiences.map(row => <option value={row.id} key={row.id}>{label(row.code)}</option>)}</select></label></div><label className="search-field discovery-search"><Icon name="search" size={18}/><input aria-label="Search saved opportunities" placeholder="Search saved opportunities…" value={search} onChange={event => setSearch(event.target.value)}/></label><div className="opportunity-list">{visible.map(row => { const review = evaluated?.id === row.id && evaluated.version === row.current_version.id && evaluated.creator === creatorId ? evaluated.rows.find(item => item.audience_segment.id === audienceId && item.score.opportunity_version_id === row.current_version.id) : null; return <article className="opportunity-card" key={row.id}><span className="eyebrow">{row.canonical_external_id}</span><h3>{row.current_version.title}</h3><p className="small muted">{label(row.current_version.status)} · {row.current_version.closing_date ? `Closes ${row.current_version.closing_date}` : "Deadline unknown"}</p>{review && <div className="notice"><strong>{label(review.decision.decision)}</strong><span>{review.decision.payload.reasons.join(" · ")}</span></div>}<div className="opportunity-actions"><button className="button secondary compact" disabled={busy || !creator || !operator} onClick={() => void act(controller => evaluate(row, controller))}>Check audience fit</button><button className="button primary compact" disabled={busy || !creator || !audienceId || !operator || review?.decision.decision !== "CREATE_CONTENT"} onClick={() => void act(controller => createDraft(row, controller))}>Create sourced draft<Icon name="arrow" size={15}/></button></div></article>; })}</div>{!visible.length && <div className="empty-stage"><Icon name="globe" size={34}/><p>No saved opportunities in this view.</p></div>}<p className="small muted">Showing up to 40 matching records. Discovery is available only for influencers with a configured editorial policy.</p></Modal>;
}
