import { apiUrl } from "./routes";

export class ApiError extends Error {
  constructor(
    message: string,
    public status: number,
  ) {
    super(message);
    this.name = "ApiError";
  }
}
export async function api<T>(
  path: string,
  options: RequestInit = {},
): Promise<T> {
  const response = await fetch(apiUrl(path), {
    ...options,
    credentials: "same-origin",
    cache: "no-store",
    headers: {
      ...(options.body ? { "Content-Type": "application/json" } : {}),
      ...options.headers,
    },
  });
  if (!response.ok) {
    if (
      response.status === 401 &&
      !["session", "login"].includes(path.split("?")[0]) &&
      typeof window !== "undefined"
    )
      window.dispatchEvent(new Event("session-expired"));
    let message = "The request could not be completed. Please try again.";
    try {
      const body = await response.json();
      message =
        typeof body.detail === "string" ? body.detail : body.message || message;
    } catch {
      /* Do not display upstream HTML. */
    }
    throw new ApiError(message, response.status);
  }
  if (response.status === 204) return undefined as T;
  return response.json() as Promise<T>;
}
export const send = <T>(path: string, body: unknown, method = "POST") =>
  api<T>(path, { method, body: JSON.stringify(body) });
export function items<T>(data: T[] | { items?: T[]; rows?: T[] }): T[] {
  return Array.isArray(data) ? data : data.items || data.rows || [];
}
export function localTimestamp(value?: string): string {
  if (!value) return "Not yet imported";
  const date = new Date(value);
  return Number.isNaN(date.getTime()) ? value : date.toLocaleString();
}
