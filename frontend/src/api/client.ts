/**
 * The single HTTP client.
 *
 * Three things live here and nowhere else in the frontend:
 *
 * 1. **Session minting.** The backend hands out an HttpOnly cookie plus a CSRF token
 *    on first load. The cookie is unreadable from script by design, so the CSRF token
 *    has to be carried in memory and attached to every state-changing request.
 * 2. **Automatic re-minting.** A session that expired overnight is a normal event for
 *    an application left open in a tab. A 401 with `no_session` triggers one mint and
 *    one retry — never a loop, because a failure that survives a fresh session is a
 *    real failure and should surface as one.
 * 3. **Error shape.** Every non-2xx becomes an `ApiError` carrying the backend's
 *    `detail` and `code`, so screens can show the reason the request was refused
 *    rather than a generic "something went wrong".
 *
 * No token, refresh token or client secret is ever handled here: the backend never
 * sends one, which is the property that makes this file safe to write quickly.
 */

export class ApiError extends Error {
  readonly status: number;
  readonly code: string | undefined;

  constructor(status: number, detail: string, code?: string) {
    super(detail);
    this.name = "ApiError";
    this.status = status;
    this.code = code;
  }
}

let csrfToken: string | null = null;
let minting: Promise<void> | null = null;

const SAFE = new Set(["GET", "HEAD", "OPTIONS"]);

async function mintSession(): Promise<void> {
  const response = await fetch("/api/session", {
    method: "POST",
    credentials: "same-origin",
    headers: { Accept: "application/json" },
  });
  if (!response.ok) {
    throw new ApiError(response.status, "Could not establish a local session.");
  }
  const body = (await response.json()) as { csrf_token: string };
  csrfToken = body.csrf_token;
}

/** Establish the session. Safe to call repeatedly; concurrent calls share one mint. */
export async function ensureSession(): Promise<void> {
  if (csrfToken) return;
  if (!minting) {
    minting = mintSession().finally(() => {
      minting = null;
    });
  }
  await minting;
}

export function csrfTokenValue(): string | null {
  return csrfToken;
}

interface RequestOptions {
  method?: string;
  body?: unknown;
  signal?: AbortSignal;
}

export async function request<T>(path: string, options: RequestOptions = {}): Promise<T> {
  const method = (options.method ?? "GET").toUpperCase();
  await ensureSession();

  const headers: Record<string, string> = { Accept: "application/json" };
  if (options.body !== undefined) headers["Content-Type"] = "application/json";
  if (!SAFE.has(method) && csrfToken) headers["X-CSRF-Token"] = csrfToken;

  const doFetch = () =>
    fetch(path, {
      method,
      credentials: "same-origin",
      headers,
      body: options.body === undefined ? undefined : JSON.stringify(options.body),
      ...(options.signal ? { signal: options.signal } : {}),
    });

  let response = await doFetch();

  if (response.status === 401) {
    const payload = await safeJson(response);
    if ((payload as { code?: string } | null)?.code === "no_session") {
      // The tab was left open past the session lifetime. Re-mint once and retry.
      csrfToken = null;
      await ensureSession();
      if (!SAFE.has(method) && csrfToken) headers["X-CSRF-Token"] = csrfToken;
      response = await doFetch();
    }
  }

  if (!response.ok) {
    const payload = await safeJson(response);
    const detail =
      (payload as { detail?: string } | null)?.detail ??
      `Request failed with status ${response.status}.`;
    const code = (payload as { code?: string } | null)?.code;
    throw new ApiError(response.status, detail, code);
  }

  if (response.status === 204) return undefined as T;
  return (await response.json()) as T;
}

async function safeJson(response: Response): Promise<unknown> {
  try {
    return await response.json();
  } catch {
    return null;
  }
}

export const api = {
  get: <T>(path: string, signal?: AbortSignal) =>
    request<T>(path, signal ? { signal } : {}),
  post: <T>(path: string, body?: unknown) => request<T>(path, { method: "POST", body }),
  put: <T>(path: string, body?: unknown) => request<T>(path, { method: "PUT", body }),
  del: <T>(path: string) => request<T>(path, { method: "DELETE" }),
};
