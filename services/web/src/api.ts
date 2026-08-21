export type Session = {user_id: string; email: string; email_verified: boolean; csrf_token: string; telegram_linked: boolean};
export type Connection = {linked: boolean; username: string | null; display_name: string | null};
export type LinkRequest = {id: string; status: "pending_telegram" | "pending_web_confirmation" | "completed" | "expired" | "cancelled"; expires_at: string; deep_link?: string | null; candidate?: {username: string | null; display_name: string | null} | null};

let csrf: string | null = null;
export class ApiError extends Error {constructor(public status: number, public code: string, message: string) {super(message);}}

export function setCsrf(value: string | null) {csrf = value;}
export async function api<T>(path: string, options: RequestInit = {}): Promise<T> {
  const method = options.method ?? "GET";
  const headers = new Headers(options.headers);
  if (options.body) headers.set("Content-Type", "application/json");
  if (!["GET", "HEAD", "OPTIONS"].includes(method) && csrf) headers.set("X-CSRF-Token", csrf);
  const response = await fetch(path, {...options, headers, credentials: "include"});
  if (!response.ok) {
    const body = await response.json().catch(() => null) as {detail?: {code?: string; message?: string}} | null;
    throw new ApiError(response.status, body?.detail?.code ?? "request_failed", body?.detail?.message ?? "Something went wrong");
  }
  if (response.status === 204 || response.headers.get("content-length") === "0") return undefined as T;
  return response.json() as Promise<T>;
}
