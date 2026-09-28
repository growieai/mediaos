"use client";

import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import DeliveryPreflight from "./DeliveryPreflight";
import MetricsReview from "./MetricsReview";

type Workflow = {
  id: string;
  state: string;
  asset_version_id: string | null;
  research_version_id: string | null;
  qa_report_id: string | null;
};
type VisualConfiguration = {
  id: string;
  version: number;
  created_at: string;
  payload: { display_name: string; template_version: string; required_disclosure: string };
  reference_sha256: string | null;
  reference_metadata: Record<string, unknown>;
};
type Finding = { code: string; severity: string; field_path: string; message: string; slide_index: number | null };
type Manifest = {
  status: string;
  caption: { text: string; fact_ids: string[] };
  slides: { index: number; sha256: string | null; width: number; height: number; overflow: boolean }[];
  findings: Finding[];
};
type Render = {
  id: string;
  sequence: number;
  asset_version_id: string;
  research_version_id: string;
  qa_report_id: string;
  visual_config_version_id: string;
  status: string;
  created_at: string;
  manifest_hash: string | null;
  manifest: Manifest | null;
  error_category: string | null;
  approvals: { id: string; decision: string; approver_id: string; created_at: string; comment: string | null }[];
};
type RenderCollection = { configurations: VisualConfiguration[]; renders: Render[] };
type Preview = { renderId: string; manifestHash: string; images: { index: number; url: string }[] };
type Props = {
  token: string;
  tenant: string;
  run: Workflow;
  operator: boolean;
  approver: boolean;
  refresh: (id?: string) => Promise<void>;
};

async function responseError(response: Response): Promise<Error> {
  if (response.headers.get("content-type")?.split(";")[0] === "application/json") {
    const data: unknown = await response.json();
    if (typeof data === "object" && data !== null && "detail" in data && typeof data.detail === "string") {
      return new Error(data.detail);
    }
  }
  return new Error(`Request failed (${response.status}).`);
}

export default function VisualReview({ token, tenant, run, operator, approver, refresh }: Props) {
  const [collection, setCollection] = useState<RenderCollection>({ configurations: [], renders: [] });
  const [configurationId, setConfigurationId] = useState("");
  const [renderId, setRenderId] = useState("");
  const [requestKey, setRequestKey] = useState("");
  const [busy, setBusy] = useState(false);
  const [loading, setLoading] = useState(true);
  const [message, setMessage] = useState("");
  const [comment, setComment] = useState("");
  const [reviewed, setReviewed] = useState(false);
  const [preview, setPreview] = useState<Preview | null>(null);
  const [previewError, setPreviewError] = useState("");
  const [previewReload, setPreviewReload] = useState(0);
  const mounted = useRef(false);
  const scope = useMemo(() => ({ tenant, token, runId: run.id, operator, approver, assetId: run.asset_version_id, researchId: run.research_version_id, qaId: run.qa_report_id, state: run.state }), [tenant, token, run.id, operator, approver, run.asset_version_id, run.research_version_id, run.qa_report_id, run.state]);
  const currentScope = useRef(scope);
  currentScope.current = scope;
  const controller = useRef<AbortController | null>(null);
  const lifecycle = useRef(0);
  const actionFlight = useRef<{ scope: typeof scope; id: symbol } | null>(null);

  const request = useCallback(async (path: string, method = "GET", body?: unknown, signal?: AbortSignal) => {
    return fetch(`/api/internal/${path}`, {
      method,
      headers: { Authorization: `Bearer ${token}`, "X-Tenant-ID": tenant, "Content-Type": "application/json" },
      body: body === undefined ? undefined : JSON.stringify(body),
      cache: "no-store",
      signal: signal ?? controller.current?.signal,
    });
  }, [tenant, token]);

  const json = useCallback(async <T,>(path: string, method = "GET", body?: unknown): Promise<T> => {
    const response = await request(path, method, body);
    if (!response.ok) throw await responseError(response);
    if (response.headers.get("content-type")?.split(";")[0] !== "application/json") throw new Error("Invalid API response type.");
    return response.json() as Promise<T>;
  }, [request]);

  const load = useCallback(async (preferredRender?: string) => {
    const captured = scope;
    const generation = lifecycle.current;
    const data = await json<RenderCollection>(`workflow-runs/${scope.runId}/renders`);
    if (!mounted.current || currentScope.current !== captured || lifecycle.current !== generation) return;
    const configurations = [...data.configurations].sort((a, b) => b.version - a.version);
    const renders = [...data.renders].sort((a, b) => b.sequence - a.sequence);
    setCollection({ configurations, renders });
    setConfigurationId(previous => configurations.some(c => c.id === previous) ? previous : configurations[0]?.id ?? "");
    setRenderId(previous => renders.some(r => r.id === preferredRender) ? preferredRender! : renders.some(r => r.id === previous) ? previous : renders[0]?.id ?? "");
  }, [json, scope]);

  useEffect(() => {
    mounted.current = true;
    const abort = new AbortController();
    controller.current = abort;
    const generation = ++lifecycle.current;
    setCollection({ configurations: [], renders: [] });
    setConfigurationId("");
    setRenderId("");
    setRequestKey(crypto.randomUUID());
    setLoading(true);
    setBusy(false);
    setMessage("");
    load().catch(error => {
      if (mounted.current && currentScope.current === scope && lifecycle.current === generation && !abort.signal.aborted) {
        setMessage(error instanceof Error ? error.message : "Could not load visual renders.");
      }
    }).finally(() => { if (mounted.current && currentScope.current === scope && lifecycle.current === generation) setLoading(false); });
    return () => { mounted.current = false; abort.abort(); };
  }, [load, scope]);

  const current = collection.renders.find(r => r.id === renderId);
  const selectedConfiguration = collection.configurations.find(c => c.id === configurationId);
  const renderedConfiguration = collection.configurations.find(c => c.id === current?.visual_config_version_id);
  const staleArtifact = !!current && (
    current.asset_version_id !== run.asset_version_id || current.research_version_id !== run.research_version_id || current.qa_report_id !== run.qa_report_id
  );
  const newerRender = !!current && collection.renders.some(r => r.sequence > current.sequence);
  const newerConfiguration = !!renderedConfiguration && collection.configurations.some(c => c.version > renderedConfiguration.version);
  const stale = staleArtifact || newerRender || newerConfiguration;
  const visualDecision = current?.approvals[0];
  const contentApproved = run.state === "APPROVED";
  const manifest = current?.manifest;
  const manifestHash = current?.manifest_hash;
  const status = current?.status;

  useEffect(() => {
    setReviewed(false);
    setComment("");
  }, [renderId, manifestHash, run.asset_version_id, run.qa_report_id, run.state]);

  useEffect(() => {
    setRequestKey(crypto.randomUUID());
  }, [run.asset_version_id]);

  useEffect(() => {
    const abort = new AbortController();
    const urls: string[] = [];
    let cancelled = false;
    setPreview(null);
    setPreviewError("");
    if (!renderId || status !== "PASS" || !manifestHash || !manifest) return () => abort.abort();
    const capturedRender = renderId;
    const capturedHash = manifestHash;
    async function loadImages() {
      const images: { index: number; url: string }[] = [];
      for (const slide of manifest!.slides) {
        const response = await request(`renders/${capturedRender}/slides/${slide.index}`, "GET", undefined, abort.signal);
        if (!response.ok) throw await responseError(response);
        if (response.headers.get("content-type")?.split(";")[0] !== "image/png") throw new Error("Preview did not return a PNG image.");
        const blob = await response.blob();
        if (cancelled) return;
        const url = URL.createObjectURL(blob);
        urls.push(url);
        images.push({ index: slide.index, url });
      }
      if (!cancelled) setPreview({ renderId: capturedRender, manifestHash: capturedHash, images });
    }
    loadImages().catch(error => {
      if (!cancelled) {
        for (const url of urls) URL.revokeObjectURL(url);
        setPreviewError(error instanceof Error ? error.message : "Could not load preview images.");
      }
    });
    return () => { cancelled = true; abort.abort(); for (const url of urls) URL.revokeObjectURL(url); };
  }, [renderId, status, manifestHash, manifest, request, previewReload, scope]);

  const images = preview?.renderId === renderId && preview?.manifestHash === manifestHash ? preview.images : [];
  const allImagesLoaded = !!manifest && manifest.slides.length > 0 && images.length === manifest.slides.length;
  const canDecide = approver && contentApproved && status === "PASS" && !stale && !visualDecision && !!manifestHash;
  const latestConfigurationSelected = configurationId === collection.configurations[0]?.id;
  const canCreate = operator && !!run.asset_version_id && !!configurationId && latestConfigurationSelected && ["AWAITING_APPROVAL", "APPROVED"].includes(run.state);

  async function action(work: () => Promise<void>) {
    const captured = scope;
    const generation = lifecycle.current;
    if (!mounted.current || currentScope.current !== captured || actionFlight.current?.scope === captured) return;
    const flight = { scope: captured, id: Symbol("visual-action") };
    actionFlight.current = flight;
    setBusy(true);
    setMessage("");
    try { await work(); }
    catch (error) {
      if (mounted.current && currentScope.current === captured && lifecycle.current === generation) setMessage(error instanceof Error ? error.message : "Visual request failed.");
    } finally {
      if (actionFlight.current === flight) actionFlight.current = null;
      if (mounted.current && currentScope.current === captured && lifecycle.current === generation) setBusy(false);
    }
  }

  async function refreshAfter(preferred?: string) {
    if (!mounted.current || currentScope.current !== scope) return;
    await load(preferred);
    if (mounted.current && currentScope.current === scope) await refresh(run.id);
  }

  async function createRender() {
    if (!canCreate || !requestKey) return;
    const result = await json<Render>(`workflow-runs/${run.id}/renders`, "POST", {
      asset_version_id: run.asset_version_id,
      visual_config_version_id: configurationId,
      idempotency_key: requestKey,
    });
    await refreshAfter(result.id);
  }

  async function decide(decision: "approve" | "reject") {
    if (!canDecide || !current?.manifest_hash || (decision === "approve" && (!reviewed || !allImagesLoaded))) return;
    await json(`renders/${current.id}/${decision}`, "POST", { manifest_hash: current.manifest_hash, comment: comment.trim() || null });
    await refreshAfter(current.id);
  }

  async function download() {
    if (!current || !contentApproved || status !== "PASS" || visualDecision?.decision !== "APPROVE" || stale) return;
    const response = await request(`renders/${current.id}/export`);
    if (!response.ok) throw await responseError(response);
    if (response.headers.get("content-type")?.split(";")[0] !== "application/zip") throw new Error("Export did not return a ZIP archive.");
    const blob = await response.blob();
    if (!mounted.current || currentScope.current !== scope) return;
    const url = URL.createObjectURL(blob);
    try {
      const anchor = document.createElement("a");
      anchor.href = url;
      anchor.download = `carousel-${current.id}.zip`;
      document.body.appendChild(anchor);
      anchor.click();
      anchor.remove();
    } finally { URL.revokeObjectURL(url); }
    setMessage("Approved ZIP downloaded. Source freshness and exact approvals were checked by the server.");
    await refreshAfter(current.id);
  }

  return <section aria-labelledby="visual-review-heading" className="visual-review">
    <div style={{ display: "flex", flexWrap: "wrap", alignItems: "start", justifyContent: "space-between", gap: 16 }}>
      <div>
        <p className="eyebrow">Instagram · Portrait carousel · 4:5</p>
        <h2 id="visual-review-heading">Carousel design</h2>
        <p>Turn your sourced draft into a set of slides. Preview every image, review the caption, then approve the design.</p>
      </div>
      <button disabled={busy || loading} onClick={() => action(() => refreshAfter())}>Refresh visual status</button>
    </div>
    <p className="visual-approval-status">Content approval: <strong>{contentApproved ? "Approved" : run.state === "AWAITING_APPROVAL" ? "Awaiting review" : run.state}</strong> · Design approval is a separate step.</p>
    <p role="status">{loading ? "Loading your designs…" : busy ? "Working on your carousel…" : message}</p>
    {!loading && collection.configurations.length === 0 && <div className="empty-state"><h3>Choose a visual identity first</h3><p>This influencer needs a saved visual configuration before its carousel can be generated.</p></div>}
    {operator && collection.configurations.length > 0 && <fieldset disabled={busy || loading}>
      <legend>Your visual direction</legend>
      <label>Visual configuration <select value={configurationId} onChange={event => { setConfigurationId(event.target.value); setRequestKey(crypto.randomUUID()); }}>
        {collection.configurations.map(c => <option key={c.id} value={c.id}>{c.payload.display_name} · {c.payload.template_version === "social-editorial-v2" ? "Social editorial" : "Classic editorial"} · version {c.version}</option>)}
      </select></label>
      {selectedConfiguration && <p>{selectedConfiguration.payload.template_version === "social-editorial-v2" ? "Bold headlines, generous typography and distinct cover, detail and closing layouts." : "The classic editorial layout for this saved visual version."}{" "}{selectedConfiguration.reference_sha256 ? "Includes your configured character portrait." : selectedConfiguration.payload.template_version === "social-editorial-v2" ? "Uses an abstract identity design without a portrait." : "Uses a text-only design without a portrait."}</p>}
      <p><button className="primary" aria-label="Create / recover this render request" disabled={!canCreate || !requestKey} onClick={() => action(createRender)}>Generate carousel</button>{" "}
        <button onClick={() => { setRequestKey(crypto.randomUUID()); setMessage("A fresh design request is ready. Select Generate carousel to create it."); }}>Prepare new render request</button></p>
      {!canCreate && <p>Use the latest visual configuration and a draft that has passed content QA to generate a carousel.</p>}
      <details><summary>Design request details</summary>
        <p style={{ overflowWrap: "anywhere" }}>Asset revision: {run.asset_version_id ?? "No content revision yet"}<br />Visual configuration: {configurationId}<br />Request key: {requestKey}</p>
        <p>The same request recovers its saved result. Prepare a new request to create another design. Previous designs and decisions remain in history.</p>
        {selectedConfiguration && <><h4>Character reference metadata</h4><pre style={{ whiteSpace: "pre-wrap", overflowWrap: "anywhere" }}>{JSON.stringify(selectedConfiguration.reference_metadata, null, 2)}</pre></>}
      </details>
    </fieldset>}
    {collection.renders.length > 0 && <>
      <p><label>Render history <select disabled={busy} value={renderId} onChange={event => setRenderId(event.target.value)}>
        {collection.renders.map(r => <option key={r.id} value={r.id}>Design {r.sequence} · {r.status === "PASS" ? "Visual checks passed" : r.status} · {new Date(r.created_at).toLocaleString()}</option>)}
      </select></label></p>
      {current && <>
        <div style={{ display: "flex", flexWrap: "wrap", gap: 16, alignItems: "baseline" }}><h3>Design {current.sequence}</h3><p>Visual checks: <strong>{current.status === "PASS" ? "Passed" : current.status}</strong> · Your team: <strong>{visualDecision?.decision === "APPROVE" ? "Approved" : visualDecision?.decision === "REJECT" ? "Changes requested" : "Awaiting design review"}</strong></p></div>
        {stale && <p role="alert" style={{ color: "#8a2900" }}>Historical preview: {staleArtifact ? "the content, research or QA revision has changed. " : ""}{newerRender ? "A newer render exists. " : ""}{newerConfiguration ? "A newer visual configuration exists. " : ""}Review a current render before approval or export.</p>}
        {current.error_category && <p role="alert">Render execution failed: {current.error_category}. Inspect the persisted skill attempt before preparing a new request.</p>}
        {operator && ["CREATED", "RENDERING"].includes(current.status) && <button disabled={busy} onClick={() => action(async () => { await json(`renders/${current.id}/execute`, "POST"); await refreshAfter(current.id); })}>Execute / resume saved render</button>}
        {manifest && <>
          {manifest.findings.length > 0 && <div role="alert"><h4>Resolve these design checks</h4><ul>{manifest.findings.map((finding, index) => <li key={`${finding.code}:${index}`}><strong>{finding.severity}</strong>{finding.slide_index ? ` · Slide ${finding.slide_index}` : ""}<br />{finding.message}</li>)}</ul></div>}
          {status === "PASS" && <>
            {previewError ? <p role="alert">{previewError} <button disabled={busy} onClick={() => setPreviewReload(value => value + 1)}>Reload preview</button></p> : !allImagesLoaded && <p>Loading authenticated slide previews…</p>}
            <div className="carousel-preview-gallery" style={{ display: "flex", gap: 20, overflowX: "auto", padding: "8px 2px 20px", scrollSnapType: "x mandatory" }}>
              {images.map(image => <figure key={image.index} style={{ margin: 0, flex: "0 0 min(82vw, 390px)", scrollSnapAlign: "start" }}>
                <img src={image.url} alt={`Rendered carousel slide ${image.index}. Exact text and disclosure are included in the image.`} width={1080} height={1350} style={{ display: "block", width: "100%", height: "auto", borderRadius: 14, boxShadow: "0 8px 24px #132a2a18" }} />
                <figcaption style={{ padding: "12px 0", display: "flex", justifyContent: "space-between", gap: 12 }}><strong>Slide {image.index}</strong><span>1080 × 1350</span></figcaption>
              </figure>)}
            </div>
          </>}
          <h3>Your caption</h3>
          <p style={{ whiteSpace: "pre-wrap", overflowWrap: "anywhere", padding: 20, borderRadius: 14, background: "#f7f5ef", color: "#132a2a" }}>{manifest.caption.text}</p>
        </>}
        <p>Review these exact slides and caption. Approval and download recheck the current source evidence and revisions.</p>
        {approver && !visualDecision && <fieldset disabled={busy}>
          <legend>Approve this design</legend>
          {!contentApproved && <p>Approve the exact content revisions above before making a visual decision.</p>}
          <label><input type="checkbox" checked={reviewed} disabled={!allImagesLoaded || !canDecide} onChange={event => setReviewed(event.target.checked)} /> I reviewed every slide, the caption, the AI disclosure and the character appearance.</label>
          <p><label>Visual review comment <textarea value={comment} maxLength={2000} rows={3} onChange={event => setComment(event.target.value)} style={{ display: "block", width: "100%" }} /></label></p>
          <button disabled={!canDecide || !allImagesLoaded || !reviewed} onClick={() => action(() => decide("approve"))}>Approve exact render</button>{" "}
          <button disabled={!canDecide} onClick={() => action(() => decide("reject"))}>Reject this render</button>
        </fieldset>}
        {visualDecision && <p>Decision recorded {new Date(visualDecision.created_at).toLocaleString()}.{visualDecision.comment ? ` Comment: ${visualDecision.comment}` : ""}</p>}
        <p><button disabled={busy || !contentApproved || status !== "PASS" || visualDecision?.decision !== "APPROVE" || stale} onClick={() => action(download)}>Download approved carousel ZIP</button></p>
        <details><summary>Version history and technical details</summary>
          <p style={{ overflowWrap: "anywhere" }}>Render: {current.id}<br />Asset revision: {current.asset_version_id}<br />Research revision: {current.research_version_id}<br />Content QA: {current.qa_report_id}<br />Visual configuration: {current.visual_config_version_id}<br />Manifest hash: {current.manifest_hash ?? "Not completed"}</p>
          {manifest && manifest.caption.fact_ids.length > 0 && <p style={{ overflowWrap: "anywhere" }}>Caption fact references: {manifest.caption.fact_ids.join(", ")}</p>}
          {manifest && manifest.findings.length > 0 && <pre style={{ whiteSpace: "pre-wrap", overflowWrap: "anywhere" }}>{JSON.stringify(manifest.findings, null, 2)}</pre>}
          {renderedConfiguration && <><h4>Rendered character reference metadata</h4><pre style={{ whiteSpace: "pre-wrap", overflowWrap: "anywhere" }}>{JSON.stringify(renderedConfiguration.reference_metadata, null, 2)}</pre></>}
          {visualDecision && <p style={{ overflowWrap: "anywhere" }}>Approver identity: {visualDecision.approver_id}</p>}
        </details>
        <details><summary>Advanced delivery checks and observations</summary>
          <DeliveryPreflight key={`${tenant}:${run.id}:${current.id}`} token={token} tenant={tenant} workflowId={run.id} renderId={current.id} allowed={operator && contentApproved && status === "PASS" && visualDecision?.decision === "APPROVE" && !stale} />
          <MetricsReview key={`metrics:${tenant}:${run.id}:${current.id}`} token={token} tenant={tenant} workflowId={run.id} renderId={current.id} operator={operator} hasHistoricalApproval={visualDecision?.decision === "APPROVE"} />
        </details>
      </>}
    </>}
    {!loading && collection.renders.length === 0 && <div className="empty-state"><h3>Your carousel will appear here</h3><p>Generate the design to preview real slides, then send them through visual approval.</p></div>}
  </section>;
}
