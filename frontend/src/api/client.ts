/** Typed REST client. Error body format: {"error": {"code", "message", "details"}} (docs/API_AND_WS_SPEC.md §0). */

export class ApiError extends Error {
  readonly status: number;
  readonly code: string;
  readonly details: Record<string, unknown>;

  constructor(status: number, code: string, message: string, details: Record<string, unknown> = {}) {
    super(message);
    this.name = 'ApiError';
    this.status = status;
    this.code = code;
    this.details = details;
  }
}

interface ErrorBody {
  error?: { code?: unknown; message?: unknown; details?: unknown };
}

export async function toApiError(resp: Response): Promise<ApiError> {
  let body: ErrorBody | null = null;
  try {
    body = (await resp.json()) as ErrorBody;
  } catch {
    body = null;
  }
  const err = body?.error;
  const code = typeof err?.code === 'string' ? err.code : `http_${resp.status}`;
  const message = typeof err?.message === 'string' ? err.message : resp.statusText || 'Request failed';
  const details =
    err?.details && typeof err.details === 'object' ? (err.details as Record<string, unknown>) : {};
  return new ApiError(resp.status, code, message, details);
}

export async function apiRequest<T>(path: string, init: RequestInit = {}): Promise<T> {
  const headers = new Headers(init.headers);
  if (init.body !== undefined && !(init.body instanceof FormData) && !headers.has('Content-Type')) {
    headers.set('Content-Type', 'application/json');
  }
  const resp = await fetch(`/api${path}`, { credentials: 'same-origin', ...init, headers });
  if (!resp.ok) throw await toApiError(resp);
  if (resp.status === 204) return undefined as T;
  return (await resp.json()) as T;
}

export interface Health {
  status: string;
  db: string;
}

export const getHealth = (): Promise<Health> => apiRequest<Health>('/health');
