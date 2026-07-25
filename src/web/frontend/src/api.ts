import { acquireAccessToken, startLogin } from "./auth";
import { getRuntime } from "./runtime";

export type Suggestion = {
  id: string;
  ticket_uid: string;
  title: string;
  details: string;
  status: string;
  notification_status: string | null;
  response_text: string | null;
  category?: string | null;
  suggestion_type?: string | null;
  conversation?: Array<{ role?: string; text?: string; content?: string }>;
};

let lastIdempotencyKey: { action: string; key: string } | null = null;

export function idempotencyKeyFor(action: string, body: string): string {
  const fingerprint = `${action}:${body}`;
  if (lastIdempotencyKey && lastIdempotencyKey.action === fingerprint) {
    return lastIdempotencyKey.key;
  }
  const key = crypto.randomUUID();
  lastIdempotencyKey = { action: fingerprint, key };
  return key;
}

export function resetIdempotency(): void {
  lastIdempotencyKey = null;
}

async function authHeaders(): Promise<HeadersInit> {
  const runtime = getRuntime();
  const headers: Record<string, string> = {
    "Content-Type": "application/json",
  };
  if (runtime.mode === "development") {
    headers["X-DCA-Local-Admin-Oid"] = runtime.localAdminOid || "local-dev-admin";
  } else {
    const token = await acquireAccessToken();
    if (token) {
      headers.Authorization = `Bearer ${token}`;
    }
  }
  return headers;
}

async function handleAuthStatus(status: number): Promise<never> {
  if (status === 401) {
    await startLogin();
    throw new Error("unauthorized");
  }
  if (status === 403) {
    throw new Error("forbidden");
  }
  throw new Error(`unexpected auth status: ${status}`);
}

export async function listSuggestions(): Promise<Suggestion[]> {
  const res = await fetch("/api/suggestions", { headers: await authHeaders() });
  if (res.status === 401 || res.status === 403) {
    return handleAuthStatus(res.status);
  }
  if (!res.ok) throw new Error(`list failed: ${res.status}`);
  return res.json();
}

export async function getSuggestion(id: string): Promise<Suggestion> {
  const res = await fetch(`/api/suggestions/${id}`, { headers: await authHeaders() });
  if (res.status === 401 || res.status === 403) {
    return handleAuthStatus(res.status);
  }
  if (res.status === 404) throw new Error("not_found");
  if (!res.ok) throw new Error(`detail failed: ${res.status}`);
  return res.json();
}

export async function respond(
  id: string,
  mode: string,
  responseText: string,
): Promise<{ status: number; body: Suggestion }> {
  const key = idempotencyKeyFor(`${id}:${mode}`, responseText);
  const res = await fetch(`/api/suggestions/${id}/respond`, {
    method: "POST",
    headers: {
      ...(await authHeaders()),
      "Idempotency-Key": key,
    },
    body: JSON.stringify({ mode, response_text: responseText }),
  });
  if (res.status === 401 || res.status === 403) {
    return handleAuthStatus(res.status);
  }
  if (res.status === 409) throw new Error("conflict");
  if (!(res.ok || res.status === 202)) {
    throw new Error(`respond failed: ${res.status}`);
  }
  resetIdempotency();
  return { status: res.status, body: await res.json() };
}
