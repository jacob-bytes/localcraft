import type {
  ApiErrorBody,
  ErrorDetails,
  FieldError,
  TokenPair,
  User,
} from "./types";

/**
 * The single HTTP entry point for the app (docs/04 §1 — native fetch, no axios).
 *
 * Implements the full auth handshake from CONTRACT §3:
 *  - access token lives in a module-level variable, NEVER in localStorage or
 *    sessionStorage (§3.1). It is lost on reload by design.
 *  - `refreshSession()` is a singleton promise: concurrent 401s share one
 *    `POST /auth/refresh` (no refresh storm — §3.2 ④).
 *  - 401 TOKEN_EXPIRED → silent refresh → replay the original request once.
 *  - 403 PASSWORD_CHANGE_REQUIRED → hand off to the app (go to /change-password).
 *  - error envelope {code, message, details, request_id} → `ApiError`, with
 *    `details.fields` exposed ready-made for react-hook-form.
 *
 * When `VITE_ENABLE_MOCKS=true` the same URLs are answered by MSW; the app code
 * below is identical either way.
 */

export const API_BASE = "/api/v1";

/** Client-side sentinel codes (never produced by the backend, never compared to
 *  a backend code). Kept out of `ErrorCode` deliberately. */
export const CLIENT_ERROR_CODES = {
  network: "CLIENT_NETWORK_ERROR",
  malformed: "CLIENT_MALFORMED_RESPONSE",
  unknown: "CLIENT_UNKNOWN_ERROR",
} as const;

/* -------------------------------------------------------------------------- */
/* In-memory session (CONTRACT §3.1)                                           */
/* -------------------------------------------------------------------------- */

let accessToken: string | null = null;

export function getAccessToken(): string | null {
  return accessToken;
}

export function setAccessToken(token: string | null): void {
  accessToken = token;
}

/* -------------------------------------------------------------------------- */
/* ApiError                                                                    */
/* -------------------------------------------------------------------------- */

export class ApiError extends Error {
  readonly status: number;
  readonly code: string;
  readonly details: ErrorDetails | null;
  readonly requestId: string | null;
  /** Field-level messages from `details.fields`, ready for form `setError()`. */
  readonly fieldErrors: Record<string, string>;

  constructor(init: {
    status: number;
    code: string;
    message: string;
    details?: ErrorDetails | null;
    requestId?: string | null;
  }) {
    super(init.message);
    this.name = "ApiError";
    this.status = init.status;
    this.code = init.code;
    this.details = init.details ?? null;
    this.requestId = init.requestId ?? null;
    this.fieldErrors = toFieldErrorMap(this.details);
  }

  is(code: string): boolean {
    return this.code === code;
  }

  /** `details.retry_after_seconds` for 423 ACCOUNT_LOCKED (CONTRACT §3.3). */
  get retryAfterSeconds(): number | null {
    const value = this.details?.retry_after_seconds;
    return typeof value === "number" && Number.isFinite(value) ? value : null;
  }
}

function toFieldErrorMap(details: ErrorDetails | null): Record<string, string> {
  const fields = details?.fields;
  if (!Array.isArray(fields)) return {};
  const map: Record<string, string> = {};
  for (const entry of fields as FieldError[]) {
    if (!entry || typeof entry.field !== "string") continue;
    if (map[entry.field] === undefined) map[entry.field] = entry.message;
  }
  return map;
}

export function isApiError(error: unknown): error is ApiError {
  return error instanceof ApiError;
}

/** Human-readable fallback for toasts (docs/04 §7.3). */
export function getErrorMessage(error: unknown): string {
  if (isApiError(error)) {
    if (error.fieldErrors && Object.keys(error.fieldErrors).length > 0) {
      const first = Object.values(error.fieldErrors)[0];
      if (first) return first;
    }
    return error.message;
  }
  if (error instanceof Error) return error.message;
  return "请求失败，请稍后重试";
}

/* -------------------------------------------------------------------------- */
/* Session event hooks (wired by AuthProvider)                                 */
/* -------------------------------------------------------------------------- */

type SessionExpiredHandler = () => void;
type PasswordChangeRequiredHandler = () => void;
type SessionRefreshedHandler = (pair: TokenPair) => void;

let sessionExpiredHandler: SessionExpiredHandler | null = null;
let passwordChangeRequiredHandler: PasswordChangeRequiredHandler | null = null;
let sessionRefreshedHandler: SessionRefreshedHandler | null = null;

export function setSessionExpiredHandler(handler: SessionExpiredHandler | null): void {
  sessionExpiredHandler = handler;
}

export function setPasswordChangeRequiredHandler(
  handler: PasswordChangeRequiredHandler | null,
): void {
  passwordChangeRequiredHandler = handler;
}

export function setSessionRefreshedHandler(handler: SessionRefreshedHandler | null): void {
  sessionRefreshedHandler = handler;
}

/* -------------------------------------------------------------------------- */
/* Query strings                                                               */
/* -------------------------------------------------------------------------- */

export type QueryValue =
  | string
  | number
  | boolean
  | null
  | undefined
  | ReadonlyArray<string | number>;

export function toQueryString(query?: Record<string, QueryValue>): string {
  if (!query) return "";
  const search = new URLSearchParams();
  for (const [key, value] of Object.entries(query)) {
    if (value === null || value === undefined || value === "") continue;
    if (Array.isArray(value)) {
      for (const item of value) {
        if (item === null || item === undefined || item === "") continue;
        search.append(key, String(item));
      }
    } else {
      search.append(key, String(value));
    }
  }
  const qs = search.toString();
  return qs ? `?${qs}` : "";
}

/* -------------------------------------------------------------------------- */
/* Core request                                                                */
/* -------------------------------------------------------------------------- */

export type HttpMethod = "GET" | "POST" | "PATCH" | "PUT" | "DELETE";

export interface RequestOptions {
  method?: HttpMethod;
  /** Serialised as JSON unless it is a FormData instance. */
  body?: unknown;
  query?: Record<string, QueryValue>;
  signal?: AbortSignal;
  /** Attach `Authorization: Bearer` when a token is held. Default `true`. */
  auth?: boolean;
  /** @internal set after the first 401 replay — prevents refresh loops. */
  retried?: boolean;
  /** @internal `POST /auth/refresh` must never try to refresh itself. */
  skipRefresh?: boolean;
}

function buildUrl(path: string, query?: Record<string, QueryValue>): string {
  const base = path.startsWith("/") ? path : `/${path}`;
  return `${API_BASE}${base}${toQueryString(query)}`;
}

async function parseBody(response: Response): Promise<unknown> {
  if (response.status === 204 || response.status === 205) return null;
  const text = await response.text();
  if (!text) return null;
  try {
    return JSON.parse(text) as unknown;
  } catch {
    return text;
  }
}

function toApiError(response: Response, payload: unknown, fallbackMessage: string): ApiError {
  const requestIdHeader = response.headers.get("X-Request-Id");
  if (payload && typeof payload === "object" && "code" in payload) {
    const body = payload as Partial<ApiErrorBody>;
    return new ApiError({
      status: response.status,
      code: typeof body.code === "string" ? body.code : CLIENT_ERROR_CODES.unknown,
      message: typeof body.message === "string" ? body.message : fallbackMessage,
      details: (body.details ?? null) as ErrorDetails | null,
      requestId: body.request_id ?? requestIdHeader,
    });
  }
  return new ApiError({
    status: response.status,
    code: response.status === 401 ? "UNAUTHENTICATED" : CLIENT_ERROR_CODES.malformed,
    message: fallbackMessage,
    details: null,
    requestId: requestIdHeader,
  });
}

function isAbortError(error: unknown): boolean {
  return error instanceof DOMException && error.name === "AbortError";
}

export async function request<T>(path: string, options: RequestOptions = {}): Promise<T> {
  const {
    method = "GET",
    body,
    query,
    signal,
    auth = true,
    retried = false,
    skipRefresh = false,
  } = options;

  const headers = new Headers({ Accept: "application/json" });
  const isFormData = typeof FormData !== "undefined" && body instanceof FormData;
  if (body !== undefined && !isFormData) {
    headers.set("Content-Type", "application/json");
  }
  if (auth && accessToken) {
    headers.set("Authorization", `Bearer ${accessToken}`);
  }

  let response: Response;
  try {
    response = await fetch(buildUrl(path, query), {
      method,
      headers,
      credentials: "same-origin",
      body:
        body === undefined ? undefined : isFormData ? (body as FormData) : JSON.stringify(body),
      signal,
    });
  } catch (error) {
    if (isAbortError(error)) throw error;
    throw new ApiError({
      status: 0,
      code: CLIENT_ERROR_CODES.network,
      message: "网络连接失败，请检查网络后重试",
      details: null,
      requestId: null,
    });
  }

  const payload = await parseBody(response);

  if (response.ok) {
    return payload as T;
  }

  const error = toApiError(response, payload, `请求失败（HTTP ${response.status}）`);

  /* 401 → silent refresh + replay once (CONTRACT §3.2 ④) */
  if (response.status === 401 && auth && !retried && !skipRefresh) {
    if (error.is("TOKEN_REVOKED")) {
      // Refresh token is gone too — going straight to login avoids a pointless
      // /auth/refresh round trip.
      sessionExpiredHandler?.();
      throw error;
    }
    try {
      await refreshSession();
    } catch {
      sessionExpiredHandler?.();
      throw error;
    }
    return request<T>(path, { ...options, auth: true, retried: true });
  }

  if (response.status === 401 && auth && retried) {
    // Replay still 401 → give up on this session (CONTRACT §3.2 ④).
    sessionExpiredHandler?.();
    throw error;
  }

  /* 403 PASSWORD_CHANGE_REQUIRED → force the change-password flow (§3.2 ⑥) */
  if (error.is("PASSWORD_CHANGE_REQUIRED")) {
    passwordChangeRequiredHandler?.();
  }

  throw error;
}

/* -------------------------------------------------------------------------- */
/* Refresh (singleton — CONTRACT §3.2 ④)                                       */
/* -------------------------------------------------------------------------- */

let refreshPromise: Promise<TokenPair> | null = null;

/**
 * Refreshes the access token using the HttpOnly refresh cookie.
 *
 * Concurrent callers (e.g. several TanStack queries mounting at once, or the
 * boot-time session restore racing a page request) all await the same promise,
 * so exactly one `POST /auth/refresh` is in flight at any time.
 */
export function refreshSession(): Promise<TokenPair> {
  if (!refreshPromise) {
    refreshPromise = performRefresh().finally(() => {
      refreshPromise = null;
    });
  }
  return refreshPromise;
}

async function performRefresh(): Promise<TokenPair> {
  const pair = await request<TokenPair>("/auth/refresh", {
    method: "POST",
    auth: false,
    skipRefresh: true,
  });
  setAccessToken(pair.access_token);
  sessionRefreshedHandler?.(pair);
  return pair;
}

/** Used by the login flow — the login response already carries a fresh pair. */
export function acceptTokenPair(pair: TokenPair): User {
  setAccessToken(pair.access_token);
  return pair.user;
}

/** Clears memory only; the refresh cookie is invalidated by `/auth/logout`. */
export function clearSession(): void {
  setAccessToken(null);
}

/* -------------------------------------------------------------------------- */
/* Multipart upload with progress (docs/04 §6.7 "上传中：进度条 + 取消")        */
/* -------------------------------------------------------------------------- */

export interface UploadOptions {
  /** `multipart/form-data` body. */
  formData: FormData;
  onProgress?: (loadedBytes: number, totalBytes: number) => void;
  signal?: AbortSignal;
  /** @internal set after the first 401 replay. */
  retried?: boolean;
}

/**
 * `fetch` cannot report upload progress, so multipart uploads go through
 * `XMLHttpRequest`. Auth, error normalisation and the silent-refresh-then-replay
 * contract are kept identical to `request()` (CONTRACT §3.2 ④).
 */
export function upload<T>(path: string, options: UploadOptions): Promise<T> {
  const { formData, onProgress, signal, retried = false } = options;

  return new Promise<T>((resolve, reject) => {
    const xhr = new XMLHttpRequest();
    xhr.open("POST", buildUrl(path), true);
    xhr.withCredentials = true;
    xhr.responseType = "text";
    xhr.setRequestHeader("Accept", "application/json");
    if (accessToken) xhr.setRequestHeader("Authorization", `Bearer ${accessToken}`);

    const onAbort = () => xhr.abort();
    signal?.addEventListener("abort", onAbort, { once: true });

    const cleanup = () => {
      signal?.removeEventListener("abort", onAbort);
    };

    xhr.upload.onprogress = (event) => {
      if (!onProgress || !event.lengthComputable) return;
      onProgress(event.loaded, event.total);
    };

    xhr.onerror = () => {
      cleanup();
      reject(
        new ApiError({
          status: 0,
          code: CLIENT_ERROR_CODES.network,
          message: "网络连接失败，请检查网络后重试",
          details: null,
          requestId: null,
        }),
      );
    };

    xhr.onabort = () => {
      cleanup();
      reject(new DOMException("Upload aborted", "AbortError"));
    };

    xhr.onload = () => {
      cleanup();
      let payload: unknown = null;
      if (xhr.responseText) {
        try {
          payload = JSON.parse(xhr.responseText) as unknown;
        } catch {
          payload = xhr.responseText;
        }
      }

      if (xhr.status >= 200 && xhr.status < 300) {
        resolve(payload as T);
        return;
      }

      const response = new Response(xhr.responseText, {
        status: xhr.status,
        headers: { "X-Request-Id": xhr.getResponseHeader("X-Request-Id") ?? "" },
      });
      const error = toApiError(response, payload, `上传失败（HTTP ${xhr.status}）`);

      if (xhr.status === 401 && !retried) {
        void (async () => {
          try {
            await refreshSession();
          } catch {
            sessionExpiredHandler?.();
            reject(error);
            return;
          }
          try {
            resolve(await upload<T>(path, { ...options, retried: true }));
          } catch (replayError) {
            reject(replayError);
          }
        })();
        return;
      }

      if (error.is("PASSWORD_CHANGE_REQUIRED")) {
        passwordChangeRequiredHandler?.();
      }
      reject(error);
    };

    xhr.send(formData);
  });
}
