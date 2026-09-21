import { NextRequest } from "next/server";

const safeHeaders = { "Cache-Control": "no-store", "X-Content-Type-Options": "nosniff" };
const maximumMediaBytes = 200 * 1024 * 1024;

async function proxy(request: NextRequest, context: { params: Promise<{ path: string[] }> }) {
  if (!request.headers.get("authorization")?.startsWith("Bearer ") || !request.headers.get("x-tenant-id")) {
    return Response.json({ detail: "Tenant and bearer credential required" }, { status: 401, headers: safeHeaders });
  }
  const { path } = await context.params;
  if (path.some(segment => !/^[a-zA-Z0-9-]+$/.test(segment))) return Response.json({ detail: "Invalid API path" }, { status: 400, headers: safeHeaders });
  const videoRoute = request.method === "GET" && path.length === 3 && path[0] === "media-runs" && path[2] === "video";
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
      headers: { Authorization: request.headers.get("authorization") || "", "X-Tenant-ID": request.headers.get("x-tenant-id") || "", "Content-Type": "application/json" },
      cache: "no-store", redirect: "manual", signal: AbortSignal.timeout(60000),
    });
    const contentType = response.headers.get("content-type")?.split(";")[0].trim().toLowerCase();
    if (!contentType || !["application/json", "image/png", "application/zip", "video/mp4"].includes(contentType) || (contentType === "video/mp4" && !videoRoute)) {
      return Response.json({ detail: "Unsupported internal API response" }, { status: 502, headers: safeHeaders });
    }
    const headers: Record<string, string> = { "Content-Type": contentType, ...safeHeaders };
    const disposition = response.headers.get("content-disposition");
    const filename = disposition?.match(/^attachment;\s*filename="([a-zA-Z0-9][a-zA-Z0-9._-]{0,180})"$/)?.[1];
    if (filename && ((contentType === "application/zip" && filename.endsWith(".zip")) || (contentType === "image/png" && filename.endsWith(".png")) || (contentType === "video/mp4" && exportVideo && filename.endsWith(".mp4")))) {
      headers["Content-Disposition"] = `attachment; filename="${filename}"`;
    }
    const limit = contentType === "application/json" ? 16 * 1024 * 1024 : maximumMediaBytes;
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
    return new Response(bytes, { status: response.status, headers });
  } catch { return Response.json({ detail: "Internal API unavailable" }, { status: 503, headers: safeHeaders }); }
}
export const GET = proxy;
export const POST = proxy;
