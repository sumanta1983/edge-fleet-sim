// Typed API client. Every path, parameter and response type comes from
// schema.d.ts, which is generated from FastAPI's OpenAPI schema
// (`npm run gen:api`). If the backend changes a field, `npm run build`
// fails here instead of the dashboard breaking at runtime.
import createClient, { type Middleware } from "openapi-fetch";
import type { components, paths } from "./schema";

export type Schemas = components["schemas"];

const TOKEN_KEY = "edgefleet.token";

// sessionStorage survives a reload but not closing the tab. Any script on the
// page can read it, so an XSS bug would leak the token; a production app
// would keep it in an httpOnly cookie instead.
export const tokenStore = {
  get: () => sessionStorage.getItem(TOKEN_KEY),
  set: (token: string) => sessionStorage.setItem(TOKEN_KEY, token),
  clear: () => sessionStorage.removeItem(TOKEN_KEY),
};

export class ApiError extends Error {
  constructor(
    public status: number,
    body: unknown,
  ) {
    super(describe(body));
  }
}

// FastAPI errors are {"detail": "text"} or, for validation (422),
// {"detail": [{"loc": [...], "msg": "..."}]}.
function describe(body: unknown): string {
  const detail = (body as { detail?: unknown } | undefined)?.detail;
  if (typeof detail === "string") return detail;
  if (Array.isArray(detail)) {
    return detail
      .map((e: { loc?: unknown[]; msg?: string }) => `${(e.loc ?? []).slice(1).join(".")}: ${e.msg}`)
      .join("; ");
  }
  return "request failed";
}

const auth: Middleware = {
  onRequest({ request }) {
    const token = tokenStore.get();
    if (token) request.headers.set("Authorization", `Bearer ${token}`);
    return request;
  },
  onResponse({ response }) {
    // Expired or invalid token: start over at the login page.
    if (response.status === 401 && tokenStore.get()) {
      tokenStore.clear();
      window.location.assign("/login");
    }
    return response;
  },
};

export const api = createClient<paths>({ baseUrl: "/api" });
api.use(auth);

// openapi-fetch returns {data} or {error}; TanStack Query wants a value or a
// thrown error.
export async function unwrap<T>(request: Promise<{ data?: T; error?: unknown; response: Response }>): Promise<T> {
  const { data, error, response } = await request;
  if (error !== undefined || data === undefined) throw new ApiError(response.status, error);
  return data;
}

export async function orNull<T>(request: Promise<T>): Promise<T | null> {
  try {
    return await request;
  } catch (e) {
    if (e instanceof ApiError && e.status === 404) return null;
    throw e;
  }
}
