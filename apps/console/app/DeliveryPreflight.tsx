"use client";

import { useCallback, useEffect, useMemo, useRef, useState } from "react";

type Target = { id: string; target_key: string; version: number; display_name: string; enabled: boolean; mode: "DRY_RUN" };
type Delivery = {
  id: string; render_run_id: string; target_id: string; status: string; created_at: string;
  attempt_count: number; retry_at: string | null; error_category: string | null;
  receipt: Record<string, unknown> | null;
};
type Props = { token: string; tenant: string; workflowId: string; renderId: string; allowed: boolean };

export default function DeliveryPreflight({ token, tenant, workflowId, renderId, allowed }: Props) {
  const [targets, setTargets] = useState<Target[]>([]);
  const [deliveries, setDeliveries] = useState<Delivery[]>([]);
  const [targetId, setTargetId] = useState("");
  const [key, setKey] = useState("");
  const [busy, setBusy] = useState(false);
  const [message, setMessage] = useState("");
  const scope = useMemo(() => ({ token, tenant, workflowId, renderId }), [token, tenant, workflowId, renderId]);
  const active = useRef(scope);
  active.current = scope;
  const controller = useRef<AbortController | null>(null);

  const request = useCallback(async <T,>(path: string, method = "GET", body?: unknown): Promise<T> => {
    const response = await fetch(`/api/internal/${path}`, {
      method, cache: "no-store", signal: controller.current?.signal,
      headers: { Authorization: `Bearer ${token}`, "X-Tenant-ID": tenant, "Content-Type": "application/json" },
      body: body === undefined ? undefined : JSON.stringify(body),
    });
    if (response.headers.get("content-type")?.split(";")[0] !== "application/json") throw new Error(`Preflight request failed (${response.status}).`);
    const data = await response.json();
    if (!response.ok) throw new Error(typeof data.detail === "string" ? data.detail : `Preflight request failed (${response.status}).`);
    return data as T;
  }, [token, tenant]);

  const load = useCallback(async () => {
    const [targetRows, runRows] = await Promise.all([
      request<Target[]>("delivery-targets"), request<Delivery[]>(`workflow-runs/${workflowId}/deliveries`),
    ]);
    if (active.current !== scope || controller.current?.signal.aborted) return;
    const latest = targetRows.filter(t => !targetRows.some(other => other.target_key === t.target_key && other.version > t.version));
    setTargets(latest);
    setTargetId(previous => latest.some(t => t.id === previous && t.enabled) ? previous : latest.find(t => t.enabled)?.id ?? "");
    setDeliveries([...runRows].sort((a, b) => b.created_at.localeCompare(a.created_at)));
  }, [request, scope, workflowId]);

  useEffect(() => {
    controller.current = new AbortController();
    setTargets([]); setDeliveries([]); setTargetId(""); setBusy(false); setMessage("");
    load().catch(error => {
      if (active.current === scope && !controller.current?.signal.aborted) setMessage(error instanceof Error ? error.message : "Could not load preflight history.");
    });
    return () => controller.current?.abort();
  }, [load, scope]);

  useEffect(() => { setKey(crypto.randomUUID()); }, [scope, targetId]);

  async function action(work: () => Promise<unknown>) {
    const captured = scope;
    setBusy(true); setMessage("");
    try {
      await work();
      if (active.current !== captured || controller.current?.signal.aborted) return;
      await load();
      if (active.current === captured) setMessage("Saved preflight status refreshed. Nothing was posted.");
    } catch (error) {
      if (active.current === captured && !controller.current?.signal.aborted) {
        try { await load(); } catch { /* Preserve the original request error. */ }
        if (active.current === captured) setMessage(error instanceof Error ? error.message : "Preflight failed.");
      }
    } finally { if (active.current === captured) setBusy(false); }
  }

  return <section aria-label="Delivery preflight" style={{ borderTop: "1px solid #ccc", marginTop: 20 }}>
    <h3>Delivery preflight</h3>
    <p><strong>DRY RUN — nothing posted.</strong> This checks the approved caption and slide files and saves a receipt. Instagram is not connected.</p>
    {!allowed && <p>An operator can run preflight after the current content and exact render have both been approved.</p>}
    <label>Target <select disabled={busy} value={targetId} onChange={event => setTargetId(event.target.value)}>
      <option value="">Select a target</option>
      {targets.map(t => <option key={t.id} value={t.id} disabled={!t.enabled}>{t.display_name} · version {t.version}{!t.enabled ? " · disabled" : ""}</option>)}
    </select></label>{" "}
    <button disabled={busy || !allowed || !targetId || !key} onClick={() => action(() => request(`renders/${renderId}/deliveries`, "POST", { target_id: targetId, idempotency_key: key, mode: "DRY_RUN" }))}>Run / recover delivery preflight</button>{" "}
    <button disabled={busy} onClick={() => action(load)}>Refresh delivery status</button>
    {allowed && <p><button disabled={busy} onClick={() => { setKey(crypto.randomUUID()); setMessage("A new request is ready. Existing receipts and failed attempts remain in history."); }}>Prepare new preflight request</button></p>}
    <p role="status">{busy ? "Checking saved approvals and files…" : message}</p>
    {deliveries.length === 0 && <p>No saved preflight runs for this workflow.</p>}
    {deliveries.map(delivery => <details key={delivery.id}>
      <summary>{delivery.status} · {new Date(delivery.created_at).toLocaleString()} · {delivery.id}</summary>
      <p style={{ overflowWrap: "anywhere" }}>Render: {delivery.render_run_id}<br />Target version: {delivery.target_id}<br />Attempts: {delivery.attempt_count}</p>
      {delivery.error_category && <p>Failure category: {delivery.error_category}</p>}
      {delivery.retry_at && <p>Retry available after {new Date(delivery.retry_at).toLocaleString()}.</p>}
      {allowed && delivery.render_run_id === renderId && ["CREATED", "VALIDATING", "RETRY_WAIT"].includes(delivery.status) && <button disabled={busy} onClick={() => action(() => request(`deliveries/${delivery.id}/execute`, "POST"))}>Resume saved preflight</button>}
      {delivery.receipt && <pre style={{ whiteSpace: "pre-wrap", overflowWrap: "anywhere" }}>{JSON.stringify(delivery.receipt, null, 2)}</pre>}
      <p>A receipt records this historical check. It does not authorize publishing or guarantee that the source remains current.</p>
    </details>)}
  </section>;
}
