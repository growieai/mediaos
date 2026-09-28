export type Session = { tenant: string; token: string };
export type Category = { id: string; name: string; description: string; accent: string };
export type Influencer = { id: string; name: string; category_id: string | null; language: string; tone: string; audience: string[]; objective: string; mission_id: string; influencer_version_id: string; character_config_version_id: string; visual_config_version_id: string | null; opportunity_discovery: boolean; portrait_available?: boolean; created_at: string };
export type Workflow = { id: string; influencer_id: string; mission_id: string; state: string; title?: string; source_snapshot_id: string; asset_version_id: string | null; research_version_id: string | null; qa_report_id: string | null; created_at: string; updated_at: string };
export type Identity = { tenant_id: string; memberships: { roles: string[] }[] };
export type Overview = { tenant_id: string; tenant_name?: string; counts: { influencers: number; workflow_runs: number; awaiting_approval: number }; workflows: Workflow[] };
export type Artifact = { id: string; payload?: Record<string, unknown>; [key: string]: unknown };
export type Artifacts = Record<string, Artifact[]>;
export type TextBlock = { text: string; kind: string; fact_ids: string[] };
export type Carousel = { slides: { index: number; headline: TextBlock; body: TextBlock }[]; caption: TextBlock; cta: TextBlock; disclosure: string; language: string };
export type Readiness = { current_ids: { asset_version_id: string | null; research_version_id: string | null; qa_report_id: string | null; render_run_id: string | null }; score: number | null; gate: string; summary: string; disclaimer: string; components: { id: string; label: string; score: number | null; max_points: number; status: string; reason: string; suggestion: string | null }[]; blockers: { code: string; message: string }[] };
export type Connection = { id: string; influencer_id: string; username: string; account_id: string; version: number; api_version: string };
export type Accounts = { connections: Connection[]; revocations: { connection_id: string }[] };
export type StudioData = { identity: Identity; overview: Overview; influencers: Influencer[]; categories: Category[]; creationEnabled: boolean; accounts: Accounts; dependencies: Record<string, boolean> };

export function currentConnections(accounts: Accounts) {
  return accounts.connections.filter(row => !accounts.revocations.some(revoked => revoked.connection_id === row.id) && !accounts.connections.some(other => other.account_id === row.account_id && other.version > row.version));
}
export function label(value: string) { return value.toLowerCase().replaceAll("_", " ").replace(/^./, first => first.toUpperCase()); }
export function stateTone(state: string) { return ["BLOCKED", "FAILED", "REJECTED"].includes(state) ? "danger" : state === "APPROVED" || state === "PUBLISHED" ? "success" : state.includes("AWAITING") || state === "REVISION_REQUIRED" ? "warning" : "neutral"; }
export function initials(name: string) { return name.trim().split(/\s+/).slice(0, 2).map(part => part[0] ?? "").join("").toUpperCase(); }

export async function request<T>(session: Session, path: string, method = "GET", body?: unknown, signal?: AbortSignal): Promise<T> {
  const response = await fetch(`/api/internal/${path}`, { method, cache: "no-store", signal,
    headers: { Authorization: `Bearer ${session.token}`, "X-Tenant-ID": session.tenant, "Content-Type": "application/json" },
    body: body === undefined ? undefined : JSON.stringify(body) });
  if (response.headers.get("content-type")?.split(";")[0] !== "application/json") throw new Error("The workspace returned an unreadable response. Please refresh.");
  const data = await response.json();
  if (!response.ok) throw new Error(data && typeof data === "object" && typeof data.detail === "string" ? data.detail : `Request failed (${response.status}).`);
  return data as T;
}

export function carouselFrom(artifacts: Artifacts, run: Workflow): Carousel | null {
  const payload = artifacts.content_asset_versions?.find(row => row.id === run.asset_version_id)?.payload;
  return payload && Array.isArray(payload.slides) ? payload as unknown as Carousel : null;
}
