import { NextRequest } from "next/server";

const safeHeaders = { "Cache-Control": "no-store", "X-Content-Type-Options": "nosniff" };
const maximumMediaBytes = 200 * 1024 * 1024;

async function proxy(request: NextRequest, context: { params: Promise<{ path: string[] }> }) {
  const { path } = await context.params;
  if (path.some(segment => !/^[a-zA-Z0-9-]+$/.test(segment))) return Response.json({ detail: "Invalid API path" }, { status: 400, headers: safeHeaders });
  const authRoute = path.length === 2 && path[0] === "auth" && ((request.method === "POST" && ["login", "setup", "logout"].includes(path[1])) || (request.method === "GET" && path[1] === "session"));
  if (path[0] === "auth" && !authRoute) return Response.json({ detail: "Not found" }, { status: 404, headers: safeHeaders });
  const configuredOrigin = process.env.AUTH_PUBLIC_ORIGIN || (process.env.NODE_ENV !== "production" ? "http://127.0.0.1:3000" : "");
  let origin: string;
  try {
    const parsed = new URL(configuredOrigin);
    if (parsed.origin !== configuredOrigin || parsed.username || parsed.password || !["http:", "https:"].includes(parsed.protocol) || (parsed.protocol === "http:" && !["127.0.0.1", "localhost", "[::1]"].includes(parsed.hostname))) throw new Error();
    origin = parsed.origin;
  } catch { return Response.json({ detail: "Workspace sign-in is not configured" }, { status: 503, headers: safeHeaders }); }
  const cookieName = origin.startsWith("https:") ? "__Host-mediaos_session" : "mediaos_session";
  const cookies = (request.headers.get("cookie") || "").split(";").map(value => value.trim()).filter(value => value.startsWith(`${cookieName}=`));
  if (cookies.length > 1) return Response.json({ detail: "Ambiguous session cookie" }, { status: 401, headers: safeHeaders });
  const cookie = cookies[0] && /^[a-zA-Z0-9_-]+=([a-zA-Z0-9_-]{20,512})$/.test(cookies[0]) ? cookies[0] : "";
  const bearer = request.headers.get("authorization");
  const bearerAccess = !!bearer?.match(/^Bearer \S+$/) && !!request.headers.get("x-tenant-id");
  if (!authRoute && !cookie && !bearerAccess) return Response.json({ detail: "Please sign in to your workspace" }, { status: 401, headers: safeHeaders });
  // Cookie credentials are ambient: validate the configured public origin, never Host/X-Forwarded-Host.
  if (request.method !== "GET" && (authRoute || cookie || request.headers.has("origin")) && request.headers.get("origin") !== origin) {
    return Response.json({ detail: "This request must come from your workspace" }, { status: 403, headers: safeHeaders });
  }
  const videoRoute = request.method === "GET" && path.length === 3 && path[0] === "media-runs" && path[2] === "video";
  const jpegRoute = request.method === "GET" && path.length === 4 && path[0] === "social-publishes" && /^[0-9a-f-]{36}$/i.test(path[1]) && path[2] === "slides" && /^(?:[1-9]|10)$/.test(path[3]);
  const query = request.nextUrl.searchParams;
  const exportVideo = videoRoute && query.size === 1 && query.get("export") === "true";
  if (query.size && !exportVideo) return Response.json({ detail: "Unsupported internal API query" }, { status: 400, headers: safeHeaders });
  let body: ArrayBuffer | undefined;
  if (request.method !== "GET") {
    const reader = request.body?.getReader();
    const chunks: Uint8Array[] = [];
    let length = 0;
    if (reader) while (true) {
      const part = await reader.read();
      if (part.done) break;
      length += part.value.length;
      if (length > 262144) { await reader.cancel(); return Response.json({ detail: "Request too large" }, { status: 413, headers: safeHeaders }); }
      chunks.push(part.value);
    }
    const bytes = new Uint8Array(length);
    let offset = 0;
    for (const chunk of chunks) { bytes.set(chunk, offset); offset += chunk.length; }
    body = bytes.buffer;
  }
  try {
    const response = await fetch((process.env.INTERNAL_API_URL || "http://127.0.0.1:8000") + "/v1/" + path.join("/") + (exportVideo ? "?export=true" : ""), {
      method: request.method, body,
      headers: { ...(bearerAccess && !authRoute ? { Authorization: bearer! } : {}), ...(!authRoute && request.headers.get("x-tenant-id") ? { "X-Tenant-ID": request.headers.get("x-tenant-id")! } : {}), ...(cookie && (!bearerAccess || authRoute) ? { Cookie: cookie } : {}), ...(request.headers.get("origin") ? { Origin: request.headers.get("origin")! } : {}), "Content-Type": "application/json" },
      cache: "no-store", redirect: "manual", signal: AbortSignal.timeout(60000),
    });
    const contentType = response.headers.get("content-type")?.split(";")[0].trim().toLowerCase();
    if (!contentType || !["application/json", "image/png", "image/jpeg", "application/zip", "video/mp4"].includes(contentType) || (contentType === "video/mp4" && !videoRoute) || (contentType === "image/jpeg" && !jpegRoute)) {
      return Response.json({ detail: "Unsupported internal API response" }, { status: 502, headers: safeHeaders });
    }
    const headers: Record<string, string> = { "Content-Type": contentType, ...safeHeaders };
    const disposition = response.headers.get("content-disposition");
    const filename = disposition?.match(/^attachment;\s*filename="([a-zA-Z0-9][a-zA-Z0-9._-]{0,180})"$/)?.[1];
    if (filename && ((contentType === "application/zip" && filename.endsWith(".zip")) || (contentType === "image/png" && filename.endsWith(".png")) || (contentType === "video/mp4" && exportVideo && filename.endsWith(".mp4")))) {
      headers["Content-Disposition"] = `attachment; filename="${filename}"`;
    }
    const limit = contentType === "application/json" ? 16 * 1024 * 1024 : contentType === "image/jpeg" ? 8_000_000 : maximumMediaBytes;
    const declared = response.headers.get("content-length");
    if (declared && /^\d+$/.test(declared) && Number(declared) > limit) {
      await response.body?.cancel();
      return Response.json({ detail: "Internal response exceeds the supported size limit" }, { status: 502, headers: safeHeaders });
    }
    const reader = response.body?.getReader();
    const chunks: Uint8Array[] = [];
    let length = 0;
    if (reader) while (true) {
      const part = await reader.read();
      if (part.done) break;
      length += part.value.length;
      if (length > limit) {
        await reader.cancel();
        return Response.json({ detail: "Internal response exceeds the supported size limit" }, { status: 502, headers: safeHeaders });
      }
      chunks.push(part.value);
    }
    const bytes = new Uint8Array(length);
    let offset = 0;
    for (const chunk of chunks) { bytes.set(chunk, offset); offset += chunk.length; }
    const result = new Response(bytes, { status: response.status, headers });
    const retryAfter = response.headers.get("retry-after");
    if (authRoute && response.status === 429 && retryAfter && /^\d{1,5}$/.test(retryAfter)) result.headers.set("Retry-After", retryAfter);
    if (authRoute) for (const value of response.headers.getSetCookie()) {
      if (value.startsWith(`${cookieName}=`)) result.headers.append("Set-Cookie", value);
    }
    return result;
  } catch { return Response.json({ detail: "Internal API unavailable" }, { status: 503, headers: safeHeaders }); }
}
export const GET = proxy;
export const POST = proxy;
