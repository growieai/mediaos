"use client";
import { useEffect, useRef, useState } from "react";
import Icon from "./Icons";
import { currentConnections, request, type Accounts, type Influencer, type Session } from "./types";
export default function Channels({ session, creators, accounts, dependencies, admin, refresh }: { session: Session; creators: Influencer[]; accounts: Accounts; dependencies: Record<string, boolean>; admin: boolean; refresh: () => Promise<void> }) {
  const [creatorId, setCreatorId] = useState(creators[0]?.id ?? ""); const [authorization, setAuthorization] = useState(""); const [message, setMessage] = useState(""); const [busy, setBusy] = useState(false);
  const controller = useRef<AbortController | null>(null); const lock = useRef(false);
  const ready = ["connect_enabled", "app_configured", "vault_configured"].every(key => dependencies[key] === true);
  const scope = `${session.tenant}:${session.token}:${creatorId}:${admin}:${ready}`;
  const currentScope = useRef(scope); currentScope.current = scope;
  useEffect(() => { setCreatorId(creators[0]?.id ?? ""); }, [session.tenant, session.token]);
  useEffect(() => {
    const abort = new AbortController(); controller.current = abort; lock.current = false;
    setAuthorization(""); setMessage(""); setBusy(false);
    return () => abort.abort();
  }, [scope]);
  const current = currentConnections(accounts);
  async function connect() {
    const abort = controller.current;
    const active = () => currentScope.current === scope && controller.current === abort && !!abort && !abort.signal.aborted;
    if (lock.current || !admin || !ready || !creators.some(row => row.id === creatorId) || !active()) return;
    lock.current = true; setBusy(true); setMessage(""); setAuthorization("");
    try {
      const result = await request<{ authorization_url: string }>(session, "social/connect", "POST", { influencer_id: creatorId }, abort?.signal);
      const url = new URL(result.authorization_url);
      if (url.protocol !== "https:" || url.hostname !== "www.instagram.com" || url.pathname !== "/oauth/authorize" || url.port || url.username || url.password) throw new Error("The authorization destination could not be verified.");
      if (active()) setAuthorization(url.href);
    } catch (error) { if (active()) setMessage(error instanceof Error ? error.message : "Could not start account connection."); }
    finally { if (active()) { lock.current = false; setBusy(false); } }
  }
  return <><div className="page-heading"><div><span className="eyebrow">YOUR DISTRIBUTION</span><h1>One studio. Your audience.</h1><p>Give each creator a home. Every post gets its own review before it goes live.</p></div><button className="button secondary" onClick={() => void refresh().catch(error => setMessage(error.message))}>Refresh accounts</button></div>
    <div className="channel-layout"><section className="surface channel-card"><div className="channel-brand"><span className="instagram-icon"><Icon name="instagram" size={30}/></span><div><h2>Instagram</h2><p>Professional accounts · Carousel publishing</p></div><span className={`pill ${current.length ? "success" : "neutral"}`}>{current.length ? `${current.length} connected` : "Not connected"}</span></div>
      {current.map(row => <div className="connected-account" key={row.id}><Icon name="check"/><div><strong>@{row.username}</strong><p>{creators.find(creator => creator.id === row.influencer_id)?.name ?? "Workspace influencer"}</p></div><span className="pill success">Connected</span></div>)}
      <div className="channel-connect"><h3>Connect an influencer’s account</h3><p className="muted">You’ll authorize access directly with Instagram. Media OS never asks for your Instagram password.</p><label>Influencer<select value={creatorId} onChange={event => { setCreatorId(event.target.value); setAuthorization(""); }}>{!creators.length && <option value="">Create an influencer first</option>}{creators.map(creator => <option value={creator.id} key={creator.id}>{creator.name}</option>)}</select></label>
        <button className="button primary" disabled={busy || !ready || !admin || !creatorId} onClick={() => void connect()}><Icon name="instagram" size={18}/>{busy ? "Preparing secure connection…" : "Connect Instagram"}</button>
        {authorization && <a className="button primary" href={authorization} target="_blank" rel="noopener noreferrer">Continue securely to Instagram<Icon name="arrow" size={16}/></a>}
        {(!ready || !admin) && <p className="notice small">{!admin ? "A workspace administrator connects social accounts." : "Account connection is not activated for this workspace yet. An administrator needs to configure the Meta app, secure token storage and HTTPS callback."}</p>}
        {message && <p role="alert" className="notice error">{message}</p>}
      </div>
    </section><aside className="surface publishing-guide"><span className="eyebrow">THE PUBLISHING FLOW</span><h2>Creative control.<br/>All the way through.</h2>{[["01", "Create", "Build a sourced carousel with your influencer’s point of view."], ["02", "Review", "Approve the content, then the exact rendered design."], ["03", "Publish", "Check the account, images and caption. Authorize and send the post."]].map(([number, title, description]) => <div className="guide-step" key={number}><span>{number}</span><div><strong>{title}</strong><p>{description}</p></div></div>)}<div className="notice"><Icon name="shield"/><span>Connecting an account does not publish anything.</span></div></aside></div>
    <div className="format-strip"><Icon name="layers"/><div><strong>Built for the format.</strong><p>Carousel exports are 1080 × 1350. Speaking-video composition is vertical; video publishing and additional networks are not connected yet.</p></div></div>
  </>;
}
