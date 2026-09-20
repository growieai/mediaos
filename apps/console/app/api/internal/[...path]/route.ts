import { NextRequest } from "next/server";

async function proxy(request: NextRequest, context: { params: Promise<{ path: string[] }> }) {
  const { path } = await context.params;
  if (path.some(segment => !/^[a-zA-Z0-9-]+$/.test(segment))) return Response.json({ detail: "Invalid API path" }, { status: 400 });
  let body: ArrayBuffer | undefined;
  if (request.method !== "GET") {
    const reader = request.body?.getReader();
    const chunks: Uint8Array[] = [];
    let length = 0;
    if (reader) while (true) {
      const part = await reader.read();
      if (part.done) break;
      length += part.value.length;
      if (length > 262144) { await reader.cancel(); return Response.json({ detail: "Request too large" }, { status: 413 }); }
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
      cache: "no-store", signal: AbortSignal.timeout(60000),
    });
    return new Response(await response.arrayBuffer(), { status: response.status, headers: { "Content-Type": "application/json", "Cache-Control": "no-store" } });
  } catch { return Response.json({ detail: "Internal API unavailable" }, { status: 503 }); }
}
export const GET = proxy;
export const POST = proxy;
