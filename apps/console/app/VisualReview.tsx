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
  const scope = useMemo(() => ({ tenant, token, runId: run.id }), [tenant, token, run.id]);
  const currentScope = useRef(scope);
  currentScope.current = scope;
  const controller = useRef<AbortController | null>(null);

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
    const data = await json<RenderCollection>(`workflow-runs/${scope.runId}/renders`);
    if (!mounted.current || currentScope.current !== captured) return;
    const configurations = [...data.configurations].sort((a, b) => b.version - a.version);
    const renders = [...data.renders].sort((a, b) => b.sequence - a.sequence);
    setCollection({ configurations, renders });
    setConfigurationId(previous => configurations.some(c => c.id === previous) ? previous : configurations[0]?.id ?? "");
    setRenderId(previous => renders.some(r => r.id === preferredRender) ? preferredRender! : renders.some(r => r.id === previous) ? previous : renders[0]?.id ?? "");
  }, [json, scope]);

  useEffect(() => {
    mounted.current = true;
    controller.current = new AbortController();
    setCollection({ configurations: [], renders: [] });
    setConfigurationId("");
    setRenderId("");
    setRequestKey(crypto.randomUUID());
    setLoading(true);
    setMessage("");
    load().catch(error => {
      if (mounted.current && currentScope.current === scope && !controller.current?.signal.aborted) {
        setMessage(error instanceof Error ? error.message : "Could not load visual renders.");
      }
    }).finally(() => { if (mounted.current && currentScope.current === scope) setLoading(false); });
    return () => { mounted.current = false; controller.current?.abort(); };
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
    setBusy(true);
    setMessage("");
    try { await work(); }
    catch (error) {
      if (mounted.current && currentScope.current === captured) setMessage(error instanceof Error ? error.message : "Visual request failed.");
    } finally {
      if (mounted.current && currentScope.current === captured) setBusy(false);
    }
  }

  async function refreshAfter(preferred?: string) {
    if (!mounted.current || currentScope.current !== scope) return;
    await load(preferred);
    if (mounted.current && currentScope.current === scope) await refresh(run.id);
  }

  async function createRender() {
    const result = await json<Render>(`workflow-runs/${run.id}/renders`, "POST", {
      asset_version_id: run.asset_version_id,
      visual_config_version_id: configurationId,
      idempotency_key: requestKey,
    });
    await refreshAfter(result.id);
  }

  async function decide(decision: "approve" | "reject") {
    if (!current?.manifest_hash) return;
    await json(`renders/${current.id}/${decision}`, "POST", { manifest_hash: current.manifest_hash, comment: comment.trim() || null });
    await refreshAfter(current.id);
  }

  async function download() {
    if (!current) return;
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

  return <section aria-labelledby="visual-review-heading" style={{ border: "1px solid #bbb", padding: 20, margin: "24px 0", background: "#fff" }}>
    <h2 id="visual-review-heading">Carousel visual review</h2>
    <p>Review and approve the sourced content first. Then review the rendered slides and caption, and approve that exact render before downloading its ZIP.</p>
    <p>Content approval: <strong>{contentApproved ? "APPROVED" : run.state}</strong>. A visual QA PASS still requires a human decision.</p>
    <button disabled={busy || loading} onClick={() => action(() => refreshAfter())}>Refresh visual status</button>
    <p role="status">{loading ? "Loading visual versions…" : busy ? "Working on visual review…" : message}</p>
    {!loading && collection.configurations.length === 0 && <p>No visual configuration is available for this influencer. Seed a visual version before rendering.</p>}
    {operator && collection.configurations.length > 0 && <fieldset disabled={busy || loading}>
      <legend>Create a render from the current content revision</legend>
      <label>Visual configuration <select value={configurationId} onChange={event => { setConfigurationId(event.target.value); setRequestKey(crypto.randomUUID()); }}>
        {collection.configurations.map(c => <option key={c.id} value={c.id}>{c.payload.display_name} · visual version {c.version}</option>)}
      </select></label>
      <p style={{ overflowWrap: "anywhere" }}>Asset revision: {run.asset_version_id ?? "No content revision yet"}<br />Visual configuration: {configurationId}</p>
      {selectedConfiguration && <details><summary>Character reference for this configuration</summary>
        <p>{selectedConfiguration.reference_sha256 ? "This version includes a proposed character reference. Review its appearance in the rendered slides." : "This version uses a text-only layout without a character image."}</p>
        <pre style={{ whiteSpace: "pre-wrap", overflowWrap: "anywhere" }}>{JSON.stringify(selectedConfiguration.reference_metadata, null, 2)}</pre>
      </details>}
      <p><button disabled={!canCreate || !requestKey} onClick={() => action(createRender)}>Create / recover this render request</button>{" "}
        <button onClick={() => { setRequestKey(crypto.randomUUID()); setMessage("A new render request is ready. Click Create to execute it."); }}>Prepare new render request</button></p>
      <p style={{ fontSize: 13, overflowWrap: "anywhere" }}>Request key: {requestKey}. Reusing it recovers the same render. After changing content or configuration, prepare a new request. Failed or rejected renders stay in history.</p>
      {!canCreate && <p>Rendering requires the latest visual configuration, current content QA PASS, and a workflow that is awaiting approval or approved.</p>}
    </fieldset>}
    {collection.renders.length > 0 && <>
      <p><label>Render history <select disabled={busy} value={renderId} onChange={event => setRenderId(event.target.value)}>
        {collection.renders.map(r => <option key={r.id} value={r.id}>{r.status} · {new Date(r.created_at).toLocaleString()} · {r.id}</option>)}
      </select></label></p>
      {current && <>
        <p>Visual QA: <strong>{current.status}</strong>. Human visual decision: <strong>{visualDecision?.decision ?? "Pending"}</strong>.</p>
        {stale && <p role="alert" style={{ color: "#8a2900" }}>Historical preview: {staleArtifact ? "the content, research or QA revision has changed. " : ""}{newerRender ? "A newer render exists. " : ""}{newerConfiguration ? "A newer visual configuration exists. " : ""}Review a current render before approval or export.</p>}
        <p style={{ overflowWrap: "anywhere", fontSize: 13 }}>Render: {current.id}<br />Asset revision: {current.asset_version_id}<br />Research revision: {current.research_version_id}<br />Content QA: {current.qa_report_id}<br />Visual configuration: {current.visual_config_version_id}<br />Manifest hash: {current.manifest_hash ?? "Not completed"}</p>
        {renderedConfiguration && <details><summary>Rendered character reference metadata</summary><pre style={{ whiteSpace: "pre-wrap", overflowWrap: "anywhere" }}>{JSON.stringify(renderedConfiguration.reference_metadata, null, 2)}</pre></details>}
        {current.error_category && <p role="alert">Render execution failed: {current.error_category}. Inspect the persisted skill attempt before preparing a new request.</p>}
        {operator && ["CREATED", "RENDERING"].includes(current.status) && <button disabled={busy} onClick={() => action(async () => { await json(`renders/${current.id}/execute`, "POST"); await refreshAfter(current.id); })}>Execute / resume saved render</button>}
        {manifest && <>
          {manifest.findings.length > 0 ? <ul>{manifest.findings.map((finding, index) => <li key={`${finding.code}:${index}`}><strong>{finding.severity}: {finding.code}</strong> · {finding.field_path}{finding.slide_index ? ` · slide ${finding.slide_index}` : ""}<br />{finding.message}</li>)}</ul> : <p>No visual QA findings in this manifest.</p>}
          {status === "PASS" && <>
            {previewError ? <p role="alert">{previewError} <button disabled={busy} onClick={() => setPreviewReload(value => value + 1)}>Reload preview</button></p> : !allImagesLoaded && <p>Loading authenticated slide previews…</p>}
            <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fit, minmax(240px, 1fr))", gap: 16 }}>
              {images.map(image => <figure key={image.index} style={{ margin: 0 }}>
                <img src={image.url} alt={`Rendered carousel slide ${image.index}. Exact text and disclosure are included in the image.`} width={1080} height={1350} style={{ display: "block", width: "100%", height: "auto", border: "1px solid #ccc" }} />
                <figcaption>Slide {image.index} · 1080 × 1350</figcaption>
              </figure>)}
            </div>
          </>}
          <h3>Caption included in the export</h3>
          <p style={{ whiteSpace: "pre-wrap", overflowWrap: "anywhere" }}>{manifest.caption.text}</p>
          {manifest.caption.fact_ids.length > 0 && <p style={{ fontSize: 13, overflowWrap: "anywhere" }}>Caption fact references: {manifest.caption.fact_ids.join(", ")}</p>}
        </>}
        <p>Previews can remain visible after evidence or revisions become stale. Approval and download always recheck current evidence, revisions and permissions on the server.</p>
        {approver && !visualDecision && <fieldset disabled={busy}>
          <legend>Human visual decision</legend>
          {!contentApproved && <p>Approve the exact content revisions above before making a visual decision.</p>}
          <label><input type="checkbox" checked={reviewed} disabled={!allImagesLoaded || !canDecide} onChange={event => setReviewed(event.target.checked)} /> I reviewed every slide, the caption, the AI disclosure and the character appearance.</label>
          <p><label>Visual review comment <textarea value={comment} maxLength={2000} rows={3} onChange={event => setComment(event.target.value)} style={{ display: "block", width: "100%" }} /></label></p>
          <button disabled={!canDecide || !allImagesLoaded || !reviewed} onClick={() => action(() => decide("approve"))}>Approve exact render</button>{" "}
          <button disabled={!canDecide} onClick={() => action(() => decide("reject"))}>Reject this render</button>
        </fieldset>}
        {visualDecision && <p>Decision recorded at {new Date(visualDecision.created_at).toLocaleString()} by {visualDecision.approver_id}.{visualDecision.comment ? ` Comment: ${visualDecision.comment}` : ""}</p>}
        <p><button disabled={busy || !contentApproved || status !== "PASS" || visualDecision?.decision !== "APPROVE" || stale} onClick={() => action(download)}>Download approved carousel ZIP</button></p>
        <DeliveryPreflight key={`${tenant}:${run.id}:${current.id}`} token={token} tenant={tenant} workflowId={run.id} renderId={current.id} allowed={operator && contentApproved && status === "PASS" && visualDecision?.decision === "APPROVE" && !stale} />
        <MetricsReview key={`metrics:${tenant}:${run.id}:${current.id}`} token={token} tenant={tenant} workflowId={run.id} renderId={current.id} operator={operator} hasHistoricalApproval={visualDecision?.decision === "APPROVE"} />
      </>}
    </>}
    {!loading && collection.renders.length === 0 && <p>No saved renders for this workflow.</p>}
  </section>;
}
