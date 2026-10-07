// One place that talks to the Django API: session cookie + CSRF header + tidy errors.
const SAFE_METHODS = new Set(["GET", "HEAD", "OPTIONS"]);

export class ApiError extends Error {
  constructor(status, data) {
    super(messageFrom(data, status));
    this.name = "ApiError";
    this.status = status;
    this.data = data;
  }
  /** Field errors in the DRF shape {"field": ["message"]}, or {}. */
  get fieldErrors() {
    const d = this.data;
    if (!d || typeof d !== "object" || Array.isArray(d)) return {};
    return Object.fromEntries(Object.entries(d).filter(([k, v]) => k !== "detail" && Array.isArray(v)));
  }
}

function messageFrom(data, status) {
  if (data && typeof data === "object") {
    if (typeof data.detail === "string") return data.detail;
    for (const [field, value] of Object.entries(data)) {
      const first = Array.isArray(value) ? value[0] : value;
      if (typeof first === "string") return field === "non_field_errors" ? first : `${field}: ${first}`;
    }
  }
  if (status === 0) return "Could not reach the server. Check your connection.";
  return `Request failed (${status}).`;
}

export function getCookie(name) {
  const part = document.cookie.split("; ").find((row) => row.startsWith(`${name}=`));
  return part ? decodeURIComponent(part.slice(name.length + 1)) : null;
}

export function toQuery(params = {}) {
  const search = new URLSearchParams();
  for (const [key, value] of Object.entries(params)) {
    if (value === undefined || value === null || value === "" || value === false) continue;
    search.set(key, value === true ? "1" : String(value));
  }
  const text = search.toString();
  return text ? `?${text}` : "";
}

let onUnauthorized = null;
/** AuthContext registers a callback here so an expired session sends the user back to the login page. */
export function setUnauthorizedHandler(fn) {
  onUnauthorized = fn;
}

async function ensureCsrf() {
  if (!getCookie("csrftoken")) {
    await fetch("/api/auth/csrf/", { credentials: "include" });
  }
}

// The API answers 403 (not 401) for "not logged in". Tell it apart from "logged in but not allowed".
function isSessionProblem(status, data) {
  if (status === 401) return true;
  return status === 403 && typeof data?.detail === "string" && data.detail.startsWith("Authentication credentials");
}

async function request(method, path, { params, body } = {}) {
  const headers = { Accept: "application/json" };
  const init = { method, credentials: "include", headers };
  if (body !== undefined) {
    if (body instanceof FormData) {
      init.body = body; // the browser sets the multipart boundary itself
    } else {
      headers["Content-Type"] = "application/json";
      init.body = JSON.stringify(body);
    }
  }
  if (!SAFE_METHODS.has(method)) {
    await ensureCsrf();
    headers["X-CSRFToken"] = getCookie("csrftoken") || "";
  }

  let response;
  try {
    response = await fetch(path + toQuery(params), init);
  } catch {
    throw new ApiError(0, null);
  }
  if (response.status === 204) return null;
  const contentType = response.headers.get("Content-Type") || "";
  const data = contentType.includes("application/json") ? await response.json() : await response.text();
  if (!response.ok) {
    if (isSessionProblem(response.status, data) && onUnauthorized) onUnauthorized();
    throw new ApiError(response.status, data);
  }
  return data;
}

export const api = {
  get: (path, params) => request("GET", path, { params }),
  post: (path, body) => request("POST", path, { body: body ?? {} }),
  patch: (path, body) => request("PATCH", path, { body }),
};
