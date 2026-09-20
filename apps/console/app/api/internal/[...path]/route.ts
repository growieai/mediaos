import { NextRequest } from "next/server";

const safeHeaders = { "Cache-Control": "no-store", "X-Content-Type-Options": "nosniff" };

async function proxy(request: NextRequest, context: { params: Promise<{ path: string[] }> }) {
  const { path } = await context.params;
  if (path.some(segment => !/^[a-zA-Z0-9-]+$/.test(segment))) return Response.json({ detail: "Invalid API path" }, { status: 400, headers: safeHeaders });
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
    const response = await fetch((process.env.INTERNAL_API_URL || "http://127.0.0.1:8000") + "/v1/" + path.join("/"), {
      method: request.method, body,
      headers: { Authorization: request.headers.get("authorization") || "", "X-Tenant-ID": request.headers.get("x-tenant-id") || "", "Content-Type": "application/json" },
      cache: "no-store", redirect: "manual", signal: AbortSignal.timeout(60000),
    });
    const contentType = response.headers.get("content-type")?.split(";")[0].trim().toLowerCase();
    if (!contentType || !["application/json", "image/png", "application/zip"].includes(contentType)) {
      return Response.json({ detail: "Unsupported internal API response" }, { status: 502, headers: safeHeaders });
    }
    const headers: Record<string, string> = { "Content-Type": contentType, ...safeHeaders };
    const disposition = response.headers.get("content-disposition");
    const filename = disposition?.match(/^attachment;\s*filename="([a-zA-Z0-9][a-zA-Z0-9._-]{0,180})"$/)?.[1];
    if (filename && ((contentType === "application/zip" && filename.endsWith(".zip")) || (contentType === "image/png" && filename.endsWith(".png")))) {
      headers["Content-Disposition"] = `attachment; filename="${filename}"`;
    }
    return new Response(await response.arrayBuffer(), { status: response.status, headers });
  } catch { return Response.json({ detail: "Internal API unavailable" }, { status: 503, headers: safeHeaders }); }
}
export const GET = proxy;
export const POST = proxy;
