import { describe, expect, it, vi } from "vitest";
import { ApiError, api, setUnauthorizedHandler, toQuery } from "./client.js";
import { json, mockApi } from "../test/helpers.jsx";

describe("toQuery", () => {
  it("skips empty values and writes booleans as 1", () => {
    expect(toQuery({ a: "x", b: "", c: null, d: undefined, e: true, f: false, g: 0 })).toBe("?a=x&e=1&g=0");
    expect(toQuery({})).toBe("");
  });
});

describe("api client", () => {
  it("sends the session cookie on every request and no CSRF header on GET", async () => {
    const { calls } = mockApi({ "GET /api/thing/": { ok: true } });
    await api.get("/api/thing/", { page: 2, q: "" });
    expect(calls[0].url).toBe("/api/thing/?page=2");
    expect(calls[0].init.credentials).toBe("include");
    expect(calls[0].init.headers["X-CSRFToken"]).toBeUndefined();
  });

  it("sends the CSRF token and a JSON body on POST", async () => {
    document.cookie = "csrftoken=abc123; path=/";
    const { calls } = mockApi({ "POST /api/thing/": { ok: true } });
    await api.post("/api/thing/", { a: 1 });
    expect(calls).toHaveLength(1);                              // cookie already present: no extra round trip
    expect(calls[0].init.headers["X-CSRFToken"]).toBe("abc123");
    expect(calls[0].init.headers["Content-Type"]).toBe("application/json");
    expect(calls[0].init.body).toBe('{"a":1}');
  });

  it("fetches a CSRF cookie first when it does not have one", async () => {
    const { calls } = mockApi({
      "GET /api/auth/csrf/": () => { document.cookie = "csrftoken=fresh; path=/"; return { detail: "ok" }; },
      "POST /api/thing/": { ok: true },
    });
    await api.post("/api/thing/", {});
    expect(calls.map((c) => c.url)).toEqual(["/api/auth/csrf/", "/api/thing/"]);
    expect(calls[1].init.headers["X-CSRFToken"]).toBe("fresh");
  });

  it("does not JSON-encode uploads", async () => {
    document.cookie = "csrftoken=abc; path=/";
    const { calls } = mockApi({ "POST /api/imports/": { id: 1 } });
    const form = new FormData();
    form.append("file", new Blob(["x"]), "a.xlsx");
    await api.post("/api/imports/", form);
    expect(calls[0].init.body).toBe(form);
    expect(calls[0].init.headers["Content-Type"]).toBeUndefined();
  });

  it("turns API errors into readable messages", async () => {
    mockApi({
      "GET /api/a/": json({ detail: "Nope." }, 403),
      "GET /api/b/": json({ file: ["The submitted file is empty."] }, 400),
      "GET /api/c/": json({ non_field_errors: ["Bad combo."] }, 400),
      "GET /api/d/": new Response("<html>boom</html>", { status: 500, headers: { "Content-Type": "text/html" } }),
    });
    await expect(api.get("/api/a/")).rejects.toMatchObject({ status: 403, message: "Nope." });
    const err = await api.get("/api/b/").catch((e) => e);
    expect(err).toBeInstanceOf(ApiError);
    expect(err.message).toBe("file: The submitted file is empty.");
    expect(err.fieldErrors).toEqual({ file: ["The submitted file is empty."] });
    await expect(api.get("/api/c/")).rejects.toMatchObject({ message: "Bad combo." });
    await expect(api.get("/api/d/")).rejects.toMatchObject({ status: 500, message: "Request failed (500)." });
  });

  it("reports a network failure clearly", async () => {
    vi.stubGlobal("fetch", vi.fn(() => Promise.reject(new TypeError("Failed to fetch"))));
    await expect(api.get("/api/x/")).rejects.toMatchObject({ status: 0, message: expect.stringContaining("Could not reach") });
  });

  it("signs the user out when the session has expired, but not when they are merely not allowed", async () => {
    const handler = vi.fn();
    setUnauthorizedHandler(handler);
    mockApi({
      "GET /api/expired/": json({ detail: "Authentication credentials were not provided." }, 403),
      "GET /api/forbidden/": json({ detail: "You do not have permission to perform this action." }, 403),
      "GET /api/unauth/": json({ detail: "Invalid token." }, 401),
    });
    await api.get("/api/forbidden/").catch(() => {});
    expect(handler).not.toHaveBeenCalled();
    await api.get("/api/expired/").catch(() => {});
    expect(handler).toHaveBeenCalledTimes(1);
    await api.get("/api/unauth/").catch(() => {});
    expect(handler).toHaveBeenCalledTimes(2);
    setUnauthorizedHandler(null);
  });

  it("handles empty 204 answers", async () => {
    mockApi({ "POST /api/auth/logout/": new Response(null, { status: 204 }) });
    document.cookie = "csrftoken=abc; path=/";
    await expect(api.post("/api/auth/logout/")).resolves.toBeNull();
  });
});
