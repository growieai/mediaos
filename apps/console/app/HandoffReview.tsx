"use client";

import { useCallback, useEffect, useMemo, useRef, useState } from "react";

type Business = { id: string; business_reference: string; revision: number; status: string; content_hash: string; payload: Record<string, unknown> };
type Transport = { id: string; destination_version_id: string; endpoint: string; protocol: string };
type Delivery = { id: string; status: string; operation: "SEND" | "REVOKE"; business_identity_id: string; transport_id: string; attestation_id: string; original_delivery_id: string | null; payload_hash: string; payload: Record<string, unknown>; expires_at: string; retention_due: boolean; transport: Transport; decisions: unknown[]; attempts: unknown[] };
type Conversion = { id: string; content_hash: string; business_reference: string; destination_version_id: string; attestations: { id: string }[] };
type Props = { tenant: string; token: string; request: Conversion; operator: boolean; approver: boolean };
type Collection = { businesses: Business[]; transports: Transport[]; deliveries: Delivery[]; dependencies: { live_enabled: boolean; credentials_configured: boolean } };
const empty: Collection = { businesses: [], transports: [], deliveries: [], dependencies: { live_enabled: false, credentials_configured: false } };

export default function HandoffReview({ tenant, token, request, operator, approver }: Props) {
  const [data, setData] = useState<Collection>(empty);
  const [businessId, setBusinessId] = useState("");
  const [transportId, setTransportId] = useState("");
  const [deliveryId, setDeliveryId] = useState("");
  const [comment, setComment] = useState("");
  const [acknowledgement, setAcknowledgement] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [message, setMessage] = useState("");
  const scope = useMemo(() => ({}), [tenant, token, request.id, request.content_hash, request.business_reference, request.destination_version_id, operator, approver]);
  const [loadedScope, setLoadedScope] = useState<object | null>(null);
  const active = useRef<object | null>(scope); active.current = scope;
  const controller = useRef<AbortController | null>(null);
  const locked = useRef(false);
  const keys = useRef(new Map<string, string>());
  const json = useCallback(async <T,>(path: string, method = "GET", body?: unknown): Promise<T> => {
    if (active.current !== scope || controller.current?.signal.aborted) throw new Error("Handoff context changed. Refresh current state.");
    const response = await fetch(`/api/internal/${path}`, { method, cache: "no-store", signal: controller.current?.signal,
      headers: { Authorization: `Bearer ${token}`, "X-Tenant-ID": tenant, "Content-Type": "application/json" }, body: body === undefined ? undefined : JSON.stringify(body) });
    if (response.headers.get("content-type")?.split(";")[0] !== "application/json") throw new Error("Invalid handoff response. Refresh saved state.");
    const result = await response.json();
    if (!response.ok) throw new Error(typeof result.detail === "string" ? result.detail : "Request failed. Refresh before retrying.");
    return result as T;
  }, [tenant, token, scope]);
  const load = useCallback(async (preferred?: string) => {
    if (active.current !== scope || controller.current?.signal.aborted) return;
    const [businesses, transports, deliveries, dependencies] = await Promise.all([
      json<Business[]>("business-identities"), json<Transport[]>("conversion-transports"),
      json<Delivery[]>(`conversion-requests/${request.id}/deliveries`), json<Collection["dependencies"]>("conversion-dependencies"),
    ]);
    if (active.current !== scope || controller.current?.signal.aborted) return;
    setData({ businesses: businesses.filter(row => row.business_reference === request.business_reference), transports: transports.filter(row => row.destination_version_id === request.destination_version_id), deliveries, dependencies });
    setDeliveryId(old => preferred && deliveries.some(row => row.id === preferred) ? preferred : deliveries.some(row => row.id === old) ? old : deliveries.at(-1)?.id ?? "");
    setAcknowledgement(null);
    setLoadedScope(scope);
  }, [json, request.id, request.business_reference, request.destination_version_id, scope]);
  useEffect(() => {
    const abort = new AbortController(); controller.current = abort; active.current = scope;
    setData(empty); setBusinessId(""); setTransportId(""); setDeliveryId(""); setComment(""); setAcknowledgement(null); setBusy(false); setMessage(""); locked.current = false; keys.current.clear();
    setLoadedScope(null);
    load().catch(error => { if (active.current === scope && !abort.signal.aborted) setMessage(error instanceof Error ? error.message : "Could not load handoffs."); });
    return () => { abort.abort(); if (active.current === scope) active.current = null; };
  }, [scope, load]);
  function key(payload: unknown) {
    const signature = JSON.stringify(payload);
    if (!keys.current.has(signature)) keys.current.set(signature, crypto.randomUUID());
    return keys.current.get(signature);
  }
  async function action(work: () => Promise<void>, requiresLoaded = true) {
    if (locked.current || active.current !== scope || (requiresLoaded && loadedScope !== scope)) return;
    locked.current = true; setBusy(true); setMessage(""); setAcknowledgement(null);
    try { await work(); }
    catch (error) { if (active.current === scope) setMessage(error instanceof Error ? error.message : "Request failed; refresh persisted history."); }
    finally { if (active.current === scope) { locked.current = false; setBusy(false); } }
  }
  const current = data.deliveries.find(row => row.id === deliveryId);
  const business = data.businesses.find(row => row.id === businessId);
  const exact = current ? `${current.id}:${current.payload_hash}:${current.status}` : "";
  const live = data.dependencies.live_enabled && data.dependencies.credentials_configured;
  const replacementHeld = current && data.deliveries.some(row => row.id !== current.id && row.status !== "REJECTED"
    && row.operation === current.operation && row.business_identity_id === current.business_identity_id
    && row.transport_id === current.transport_id && row.attestation_id === current.attestation_id
    && row.original_delivery_id === current.original_delivery_id);

  return <section aria-label="External handoff review">
    <h3>External handoff</h3>
    <p>A destination receipt confirms receipt of a request, not completion of a business audit or a conversion. The destination must implement the configured signed receipt protocol.</p>
    <p>External execution: {live ? "Configured; exact authorization required" : "Disabled or credentials missing"}.</p>
    <button disabled={busy} onClick={() => void action(() => load(), false)}>Refresh handoff history</button>
    <p role="status">{busy ? "Working… If interrupted, refresh saved state before continuing." : message}</p>
    <fieldset disabled={busy || loadedScope !== scope}><legend>Business identity and destination</legend>
      <label>Business evidence <select value={businessId} onChange={event => { setBusinessId(event.target.value); setAcknowledgement(null); }}><option value="">Choose a recorded business</option>{data.businesses.map(row => <option key={row.id} value={row.id}>Revision {row.revision} · {row.status}</option>)}</select></label>
      {business && <pre style={{ whiteSpace: "pre-wrap", overflowWrap: "anywhere" }}>{JSON.stringify(business.payload, null, 2)}</pre>}
      {!data.businesses.length && <p>Record the business and its registry evidence through the internal business-identities API before preparing a handoff. Identity review does not establish consent.</p>}
      <p><label>Review note <textarea maxLength={2000} value={comment} onChange={event => { setComment(event.target.value); setAcknowledgement(null); }} /></label></p>
      {approver && business?.status === "AWAITING_REVIEW" && <><label><input type="checkbox" checked={acknowledgement === business.content_hash} onChange={event => setAcknowledgement(event.target.checked ? business.content_hash : null)} /> I checked this exact business identity against the cited evidence.</label><button disabled={!comment.trim() || acknowledgement !== business.content_hash} onClick={() => void action(async () => { await json(`business-identities/${business.id}/review`, "POST", { content_hash: business.content_hash, identity_attested: true, comment }); await load(); })}>Review business identity</button></>}
      <p><label>Provisioned destination <select value={transportId} onChange={event => { setTransportId(event.target.value); setAcknowledgement(null); }}><option value="">Choose exact destination</option>{data.transports.map(row => <option key={row.id} value={row.id}>{row.endpoint}</option>)}</select></label></p>
      {!data.transports.length && <p>An administrator must provision a destination for this exact consent request. Credentials stay on the server.</p>}
      {operator && <button disabled={business?.status !== "OPERATOR_ASSERTION_REVIEWED" || !transportId || !request.attestations.length} onClick={() => void action(async () => {
        const body = { business_identity_id: businessId, transport_id: transportId, attestation_id: request.attestations.at(-1)!.id, request_hash: request.content_hash };
        const saved = await json<Delivery>(`conversion-requests/${request.id}/deliveries`, "POST", { ...body, idempotency_key: key(body) }); await load(saved.id);
      })}>Prepare exact handoff for review</button>}
    </fieldset>
    <p><label>Saved handoff <select disabled={busy} value={deliveryId} onChange={event => { setDeliveryId(event.target.value); setAcknowledgement(null); }}><option value="">Choose a handoff</option>{data.deliveries.map(row => <option key={row.id} value={row.id}>{row.operation} · {row.status} · {row.id}</option>)}</select></label></p>
    {current && <fieldset disabled={busy || loadedScope !== scope}><legend>{current.operation} · {current.status}</legend>
      <p>Destination: {current.transport.endpoint}. Retention deadline: {current.expires_at}.</p>
      <pre style={{ whiteSpace: "pre-wrap", overflowWrap: "anywhere" }}>{JSON.stringify({ payload: current.payload, payload_hash: current.payload_hash }, null, 2)}</pre>
      <label><input type="checkbox" checked={acknowledgement === exact} onChange={event => setAcknowledgement(event.target.checked ? exact : null)} /> I reviewed the exact payload, destination and operation displayed above.</label>
      {approver && ["AWAITING_AUTHORIZATION", "AUTHORIZED"].includes(current.status) && <p>{(["AUTHORIZE", "REJECT"] as const).map(decision => <button key={decision} disabled={!comment.trim() || acknowledgement !== exact} onClick={() => void action(async () => { await json(`conversion-deliveries/${current.id}/authorize`, "POST", { payload_hash: current.payload_hash, decision, comment }); await load(current.id); })}>{decision === "AUTHORIZE" ? "Authorize exact handoff" : "Reject handoff"}</button>)}</p>}
      {operator && current.status === "AUTHORIZED" && <button disabled={!live || acknowledgement !== exact} onClick={() => void action(async () => { await json(`conversion-deliveries/${current.id}/dispatch`, "POST"); await load(current.id); })}>Send authorized handoff</button>}
      {operator && current.status === "UNKNOWN_OUTCOME" && <p>The original request will not be resent. <button disabled={!live || acknowledgement !== exact} onClick={() => void action(async () => { await json(`conversion-deliveries/${current.id}/reconcile`, "POST"); await load(current.id); })}>Check destination receipt</button></p>}
      {operator && current.operation === "SEND" && ["RECEIVED", "UNKNOWN_OUTCOME"].includes(current.status) && <p><button disabled={acknowledgement !== exact} onClick={() => void action(async () => { const saved = await json<Delivery>(`conversion-deliveries/${current.id}/revocation`, "POST", { idempotency_key: key({ revocation_of: current.id }) }); await load(saved.id); })}>Prepare revocation notice for separate review</button></p>}
      {operator && current.status === "REJECTED" && current.attempts.length === 0 && <p>
        This request was rejected before dispatch. A replacement requires its own authorization.
        {replacementHeld && <span> Another matching handoff already exists. Select it above to review or recover its recorded outcome.</span>}
        <button disabled={replacementHeld || acknowledgement !== exact || (current.operation === "REVOKE" && !current.original_delivery_id)} onClick={() => void action(async () => {
          // Pin a new key to this known rejection. An interrupted replacement keeps
          // that same key until its result is recovered; it never rotates on retry.
          const idempotency_key = key({ replacement_of: current.id });
          const saved = current.operation === "SEND"
            ? await json<Delivery>(`conversion-requests/${request.id}/deliveries`, "POST", { business_identity_id: current.business_identity_id, transport_id: current.transport_id, attestation_id: current.attestation_id, request_hash: request.content_hash, idempotency_key })
            : await json<Delivery>(`conversion-deliveries/${current.original_delivery_id}/revocation`, "POST", { idempotency_key });
          await load(saved.id);
        })}>Prepare replacement for fresh review</button>
      </p>}
      <details><summary>Authorization and receipt history</summary><pre style={{ whiteSpace: "pre-wrap", overflowWrap: "anywhere" }}>{JSON.stringify({ decisions: current.decisions, attempts: current.attempts }, null, 2)}</pre></details>
    </fieldset>}
  </section>;
}
