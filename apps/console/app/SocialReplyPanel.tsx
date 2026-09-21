"use client";

import { useCallback, useEffect, useMemo, useRef, useState } from "react";

type Reply = { id: string; sequence: number; status: string; exact_text: string; text_hash: string; comment_id: string; connection_id: string; reply_id: string | null; jobs: unknown[]; decisions: unknown[] };
type Connection = { id: string; account_id: string; username: string };
type Props = { tenant: string; token: string; reviewId: string; eligible: boolean; operator: boolean; approver: boolean };

export default function SocialReplyPanel({ tenant, token, reviewId, eligible, operator, approver }: Props) {
  const [rows, setRows] = useState<Reply[]>([]);
  const [accounts, setAccounts] = useState<Connection[]>([]);
  const [enabled, setEnabled] = useState(false);
  const [busy, setBusy] = useState(false);
  const [message, setMessage] = useState("");
  const [reviewed, setReviewed] = useState("");
  const [sendConfirmed, setSendConfirmed] = useState("");
  const scope = useMemo(() => ({}), [tenant, token, reviewId, eligible]);
  const active = useRef(scope); active.current = scope;
  const controller = useRef<AbortController | null>(null);
  const key = useRef("");
  const request = useCallback(async <T,>(path: string, method = "GET", body?: unknown): Promise<T> => {
    const response = await fetch(`/api/internal/${path}`, {
      method, cache: "no-store", signal: controller.current?.signal,
      headers: { Authorization: `Bearer ${token}`, "X-Tenant-ID": tenant, "Content-Type": "application/json" },
      body: body === undefined ? undefined : JSON.stringify(body),
    });
    if (response.headers.get("content-type")?.split(";")[0] !== "application/json") throw new Error("Unexpected reply transport response.");
    const result = await response.json();
    if (!response.ok) throw new Error(typeof result.detail === "string" ? result.detail : `Reply request failed (${response.status}). Refresh saved status.`);
    return result as T;
  }, [tenant, token]);
  const load = useCallback(async () => {
    const [saved, dependencies, connected] = await Promise.all([
      request<Reply[]>(`community-reviews/${reviewId}/reply-dispatches`),
      request<{ reply_dispatch_enabled: boolean }>("social/dependencies"),
      request<{ connections: Connection[] }>("social/connections"),
    ]);
    if (active.current !== scope || controller.current?.signal.aborted) return;
    setRows([...saved].sort((a, b) => b.sequence - a.sequence)); setEnabled(dependencies.reply_dispatch_enabled); setAccounts(connected.connections);
  }, [request, reviewId, scope]);
  useEffect(() => {
    const abort = new AbortController(); controller.current = abort; key.current = crypto.randomUUID();
    setRows([]); setAccounts([]); setEnabled(false); setBusy(false); setMessage(""); setReviewed(""); setSendConfirmed("");
    load().catch(error => { if (active.current === scope && !abort.signal.aborted) setMessage(error instanceof Error ? error.message : "Could not load reply dispatches."); });
    return () => abort.abort();
  }, [load, scope]);
  async function action(work: () => Promise<void>) {
    setBusy(true); setMessage("");
    try { await work(); }
    catch (error) { if (active.current === scope && !controller.current?.signal.aborted) setMessage(error instanceof Error ? error.message : "Reply action failed; refresh saved status."); }
    finally { if (active.current === scope) setBusy(false); }
  }
  return <section aria-label="Verified platform reply dispatch">
    <h4>Separate Instagram reply authorization</h4>
    <p>Only this verified platform comment can receive a reply. Internal draft review, outbound authorization and sending are separate actions.</p>
    <p>Reply transport: {enabled ? "Enabled" : "Disabled"}. <button disabled={busy} onClick={() => action(load)}>Refresh dispatches</button></p>
    <p role="status">{busy ? "Working… Refresh saved status after a timeout." : message}</p>
    {operator && <button disabled={busy || !eligible} onClick={() => action(async () => { await request(`community-reviews/${reviewId}/reply-dispatches`, "POST", { idempotency_key: key.current }); await load(); })}>Prepare / recover exact reply dispatch</button>}
    {rows.map(row => {
      const identity = `${row.id}:${row.text_hash}`;
      const dispatchIdentity = `${identity}:${row.status}`;
      const recoveringReply = row.status === "SENDING";
      const account = accounts.find(c => c.id === row.connection_id);
      const current = eligible && row.id === rows[0]?.id;
      return <article key={row.id}>
        <p><strong>{row.status}</strong> · @{account?.username ?? "Unknown account"} · {account?.account_id ?? row.connection_id} · comment {row.comment_id}</p>
        <blockquote style={{ whiteSpace: "pre-wrap", overflowWrap: "anywhere" }}>{row.exact_text}</blockquote>
        <p>Exact text hash: <code>{row.text_hash}</code></p>
        {approver && row.status === "AWAITING_REPLY_APPROVAL" && <fieldset disabled={busy}><legend>Review exact outbound reply</legend>
          <label><input type="checkbox" disabled={!current || !account} checked={reviewed === identity} onChange={event => setReviewed(event.target.checked ? identity : "")} /> I reviewed this exact reply, disclosure, account and destination comment.</label>{" "}
          {(["AUTHORIZE_REPLY", "REJECT"] as const).map(decision => <button key={decision} disabled={decision === "AUTHORIZE_REPLY" && (!current || !account || reviewed !== identity)} onClick={() => action(async () => { await request(`social-reply-runs/${row.id}/review`, "POST", { decision, text_hash: row.text_hash, comment: null }); setReviewed(""); await load(); })}>{decision === "AUTHORIZE_REPLY" ? "Authorize exact reply" : "Reject reply dispatch"}</button>)}
        </fieldset>}
        {operator && ["AUTHORIZED", "SENDING"].includes(row.status) && <fieldset disabled={busy || (!recoveringReply && !current) || !enabled}><legend>{recoveringReply ? "Recover interrupted reply" : "Public reply action"}</legend>
          {recoveringReply && <p>Recovery checks the saved provider receipt without sending again. An unconfirmed outcome requires reconciliation.</p>}
          <label><input type="checkbox" checked={sendConfirmed === dispatchIdentity} onChange={event => setSendConfirmed(event.target.checked ? dispatchIdentity : "")} /> {recoveringReply ? `I confirm recovery of this interrupted reply to comment ${row.comment_id}, without sending it again.` : `I confirm sending this authorized exact public reply to comment ${row.comment_id}.`}</label>{" "}<button disabled={sendConfirmed !== dispatchIdentity} onClick={() => action(async () => { setSendConfirmed(""); await request(`social-reply-runs/${row.id}/execute`, "POST"); await load(); })}>{recoveringReply ? "Recover interrupted reply" : "Send authorized reply"}</button>
        </fieldset>}
        {row.status === "UNKNOWN_OUTCOME" && <p>The provider outcome is uncertain. Administrative reconciliation is required. No blind retry is available.</p>}
        {row.status === "SENT" && <p>Confirmed platform reply: {row.reply_id}.</p>}
        <details><summary>Saved reply jobs and decisions</summary><pre style={{ whiteSpace: "pre-wrap", overflowWrap: "anywhere" }}>{JSON.stringify({ jobs: row.jobs, decisions: row.decisions }, null, 2)}</pre></details>
      </article>;
    })}
  </section>;
}
