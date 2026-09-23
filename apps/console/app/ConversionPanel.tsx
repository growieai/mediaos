"use client";

import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import HandoffReview from "./HandoffReview";

type Conversion = {
  id: string; revision: number; status: string; mode: string; purpose: string;
  subject_reference: string; business_reference: string; consent_provenance: string;
  expires_at: string; content_hash: string; destination_hash: string; consent_hash: string;
  destination: { label: string; revision: number; mode: string };
  payload: { consent: { statement: string; origin: string; captured_at: string; expires_at: string } };
  destination_version_id: string; attestations: { id: string }[]; revocations: unknown[]; exports: unknown[];
};
type Props = { tenant: string; token: string; workflowId: string; operator: boolean; approver: boolean };

export default function ConversionPanel({ tenant, token, workflowId, operator, approver }: Props) {
  const [rows, setRows] = useState<Conversion[]>([]);
  const [busy, setBusy] = useState(false);
  const [message, setMessage] = useState("");
  const [selected, setSelected] = useState("");
  const scope = useMemo(() => ({}), [tenant, token, workflowId]);
  const active = useRef(scope); active.current = scope;
  const controller = useRef<AbortController | null>(null);
  const load = useCallback(async () => {
    setBusy(true); setMessage("");
    try {
      const response = await fetch(`/api/internal/workflow-runs/${workflowId}/conversion-requests`, {
        cache: "no-store", signal: controller.current?.signal,
        headers: { Authorization: `Bearer ${token}`, "X-Tenant-ID": tenant },
      });
      if (response.headers.get("content-type")?.split(";")[0] !== "application/json") throw new Error("Invalid request-history response.");
      const body = await response.json();
      if (!response.ok) throw new Error(typeof body.detail === "string" ? body.detail : "Could not load request history.");
      if (active.current === scope && !controller.current?.signal.aborted) setRows(body as Conversion[]);
    } catch (error) {
      if (active.current === scope && !controller.current?.signal.aborted) setMessage(error instanceof Error ? error.message : "Could not load request history.");
    } finally { if (active.current === scope) setBusy(false); }
  }, [tenant, token, workflowId, scope]);
  useEffect(() => {
    const abort = new AbortController(); controller.current = abort; setRows([]); setMessage(""); setSelected("");
    void load(); return () => abort.abort();
  }, [load]);

  return <section aria-label="Consent and manual handoff history" style={{ borderTop: "1px solid #ccc", marginTop: 20 }}>
    <h2>Consent and manual handoff requests</h2>
    <p>Comments and AI classifications do not establish consent. Manual exports prepare JSON for a human. External handoffs require a provisioned destination and separate exact approval.</p>
    <button disabled={busy} onClick={() => void load()}>Refresh request history</button>
    <p role="status">{busy ? "Loading saved requests…" : message}</p>
    {rows.length === 0 && <p>No requests recorded. Use the documented internal conversion API to capture a real explicit request or a clearly marked fixture; this screen does not invent consent.</p>}
    {rows.map(row => <details key={row.id} style={{ marginTop: 10 }}>
      <summary>Revision {row.revision} · {row.status} · {row.mode} · {row.id}</summary>
      <p>Consent provenance: {row.consent_provenance}. Purpose: {row.purpose}.</p>
      <p>Opaque subject: {row.subject_reference} · Business: {row.business_reference}</p>
      <p>Destination: {row.destination.label} · revision {row.destination.revision} · {row.destination.mode}</p>
      <blockquote style={{ whiteSpace: "pre-wrap", overflowWrap: "anywhere" }}>{row.payload.consent.statement}</blockquote>
      <p>Origin: {row.payload.consent.origin} · Captured: {row.payload.consent.captured_at} · Expires: {row.expires_at}</p>
      <p>Manual-export receipts do not prove delivery. Inspect external handoff history below for signed destination receipts. No business audit is performed here.</p>
      <button disabled={busy} onClick={() => setSelected(row.id)}>Inspect external handoffs</button>
      <pre style={{ whiteSpace: "pre-wrap", overflowWrap: "anywhere" }}>{JSON.stringify({ request_hash: row.content_hash, destination_hash: row.destination_hash, consent_hash: row.consent_hash, attestations: row.attestations, revocations: row.revocations, manual_export_receipts: row.exports }, null, 2)}</pre>
    </details>)}
    {rows.filter(row => row.id === selected).map(row => <HandoffReview key={`${tenant}:${workflowId}:${row.id}`} tenant={tenant} token={token} request={row} operator={operator} approver={approver} />)}
  </section>;
}
