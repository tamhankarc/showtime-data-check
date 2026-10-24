import { NextRequest } from "next/server";
import { allowedPath } from "@/lib/proxy";

export const dynamic = "force-dynamic";
export const runtime = "nodejs";
export const maxDuration = 60;

async function proxy(
  request: NextRequest,
  context: { params: Promise<{ path: string[] }> },
) {
  const { path } = await context.params;
  if (!allowedPath(path))
    return Response.json({ detail: "Not found." }, { status: 404 });
  const backend = process.env.BACKEND_URL;
  const secret = process.env.PROXY_SECRET;
  if (!backend || !secret)
    return Response.json(
      {
        detail:
          "The service connection is not configured. Ask the administrator to complete setup.",
      },
      { status: 503 },
    );
  const target = new URL(
    `${backend.replace(/\/$/, "")}/${path.map(encodeURIComponent).join("/")}`,
  );
  target.search = request.nextUrl.search;
  const headers = new Headers({
    "X-Proxy-Secret": secret,
    Accept: request.headers.get("accept") || "application/json",
  });
  for (const key of ["cookie", "origin", "content-type", "referer"]) {
    const value = request.headers.get(key);
    if (value) headers.set(key, value);
  }
  headers.set(
    "X-Forwarded-Host",
    request.headers.get("host") || request.nextUrl.host,
  );
  headers.set("X-Forwarded-Proto", request.nextUrl.protocol.replace(":", ""));
  const forwardedFor =
    process.env.VERCEL === "1"
      ? request.headers.get("x-vercel-forwarded-for")
      : null;
  if (forwardedFor) headers.set("X-Forwarded-For", forwardedFor);
  headers.set("X-Client-IP", forwardedFor?.split(",")[0]?.trim() || "local");
  let body: ArrayBuffer | undefined;
  if (!["GET", "HEAD"].includes(request.method)) {
    if (Number(request.headers.get("content-length") || 0) > 2_000_000)
      return Response.json({ detail: "Request too large." }, { status: 413 });
    body = await request.arrayBuffer();
    if (body.byteLength > 2_000_000)
      return Response.json({ detail: "Request too large." }, { status: 413 });
  }
  try {
    const response = await fetch(target, {
      method: request.method,
      headers,
      body,
      redirect: "manual",
      cache: "no-store",
      signal: AbortSignal.timeout(55_000),
    });
    const resultHeaders = new Headers({
      "Cache-Control": "no-store, private",
      "X-Content-Type-Options": "nosniff",
    });
    for (const key of ["content-type", "content-disposition", "retry-after"]) {
      const value = response.headers.get(key);
      if (value) resultHeaders.set(key, value);
    }
    for (const cookie of response.headers.getSetCookie())
      resultHeaders.append("set-cookie", cookie);
    return new Response(response.body, {
      status: response.status,
      headers: resultHeaders,
    });
  } catch {
    return Response.json(
      {
        detail:
          "The data service is unavailable or took too long. Please try again.",
      },
      { status: 502, headers: { "Cache-Control": "no-store" } },
    );
  }
}
export { proxy as GET, proxy as POST, proxy as PATCH, proxy as DELETE };
