"use client";
import { useEffect, useState } from "react";
import { initials, type Influencer, type Session } from "./types";
export default function Avatar({ creator, session, large = false }: { creator: Influencer; session: Session; large?: boolean }) {
  const [image, setImage] = useState<{ identity: string; url: string } | null>(null);
  const identity = `${session.tenant}:${session.token}:${creator.id}:${creator.visual_config_version_id}`;
  useEffect(() => {
    const abort = new AbortController(); let url = "";
    if (creator.portrait_available !== false) void (async () => {
      const response = await fetch(`/api/internal/studio/influencers/${creator.id}/portrait`, { credentials: "same-origin", headers: { ...(session.token ? { Authorization: `Bearer ${session.token}` } : {}), "X-Tenant-ID": session.tenant }, cache: "no-store", signal: abort.signal });
      if (!response.ok || response.headers.get("content-type")?.split(";")[0] !== "image/png") return;
      const blob = await response.blob(); if (abort.signal.aborted || blob.size > 12_000_000) return;
      url = URL.createObjectURL(blob); setImage({ identity, url });
    })().catch(() => {});
    return () => { abort.abort(); if (url) URL.revokeObjectURL(url); };
  }, [identity, creator.id, creator.portrait_available, session.tenant, session.token]);
  return <div className={`creator-avatar ${large ? "large" : ""} category-${creator.category_id ?? "business"}`}>{image?.identity === identity ? <img src={image.url} alt={`${creator.name} character reference`}/> : <span>{initials(creator.name)}</span>}</div>;
}
