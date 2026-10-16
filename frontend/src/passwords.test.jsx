import { describe, expect, it, vi } from "vitest";
import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { MemoryRouter, useLocation } from "react-router-dom";
import App from "./App.jsx";
import { AuthProvider } from "./context/AuthContext.jsx";
import { baseRoutes, fixtures, json, mockApi, renderApp } from "./test/helpers.jsx";

const anonymous = () => json({ detail: "Authentication credentials were not provided." }, 403);
const TOKEN = "1.setup.3.0123456789abcdef:1xHGC5:G8fUtM5pBAb_zE1DImFMNoku81gNrncPoxplQyEr4MM";
const STRONG = "Tr1cky-Horse-Battery-42";
const body = (c) => JSON.parse(c.init.body);
const posts = (api, end) => api.calls.filter((c) => c.method === "POST" && c.url.endsWith(end));

function Where() {
  const l = useLocation();
  return <div data-testid="where">{`${l.pathname}${l.search}${l.hash}`}</div>;
}
function renderAt(route) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={client}>
      <MemoryRouter initialEntries={[route]}>
        <AuthProvider><App /><Where /></AuthProvider>
      </MemoryRouter>
    </QueryClientProvider>,
  );
}
const publicApi = (extra = {}) => mockApi(baseRoutes("md", { "GET /api/auth/me/": anonymous, ...extra }));
const fillPasswords = async (a = STRONG, b = a) => {
  await userEvent.type(await screen.findByLabelText("New password"), a);
  await userEvent.type(screen.getByLabelText("Confirm new password"), b);
};

describe("set password (new account link)", () => {
  it("reads the token from the fragment, clears it from the address, and sends it only in the request body", async () => {
    document.cookie = "csrftoken=abc; path=/";
    const setItem = vi.spyOn(Storage.prototype, "setItem");
    const api = publicApi({ "POST /api/auth/set-password/": { detail: "Your password is set. You can now sign in." } });
    renderAt(`/set-password#token=${TOKEN}`);
    expect(await screen.findByRole("heading", { name: "Set your password" })).toBeInTheDocument();
    await waitFor(() => expect(screen.getByTestId("where")).toHaveTextContent(/^\/set-password$/));    // no fragment left
    await fillPasswords();
    await userEvent.click(screen.getByRole("button", { name: "Save password" }));
    expect(await screen.findByText("Your password is set. You can now sign in.")).toBeInTheDocument();
    const call = posts(api, "/api/auth/set-password/")[0];
    expect(body(call)).toEqual({ token: TOKEN, password: STRONG });
    expect(call.url).toBe("/api/auth/set-password/");                                                    // not in the URL
    expect(call.init.headers["X-CSRFToken"]).toBe("abc");
    expect(screen.getByRole("link", { name: "Go to sign in" })).toHaveAttribute("href", "/login");
    expect(setItem).not.toHaveBeenCalled();                                                              // nothing stored
    expect(localStorage.length + sessionStorage.length).toBe(0);
    expect(document.body.textContent).not.toContain(TOKEN);
    expect(api.urls().join(" ")).not.toContain(TOKEN);
    setItem.mockRestore();
  });

  it("checks the two passwords match before sending anything", async () => {
    const api = publicApi();
    renderAt(`/set-password#token=${TOKEN}`);
    await fillPasswords(STRONG, "something else");
    expect(screen.getByRole("alert")).toHaveTextContent("The two passwords do not match.");
    expect(screen.getByRole("button", { name: "Save password" })).toBeDisabled();
    expect(posts(api, "/api/auth/set-password/")).toHaveLength(0);
  });

  it("explains a weak password and lets the same link be used again", async () => {
    document.cookie = "csrftoken=abc; path=/";
    let attempt = 0;
    const api = publicApi({
      "POST /api/auth/set-password/": () => {
        attempt += 1;
        return attempt === 1 ? json({ password: ["This password is too short.", "This password is too common."] }, 400) : { detail: "ok" };
      },
    });
    renderAt(`/set-password#token=${TOKEN}`);
    await fillPasswords("short");
    await userEvent.click(screen.getByRole("button", { name: "Save password" }));
    expect(await screen.findByText("This password is too short.")).toBeInTheDocument();
    expect(screen.getByText("This password is too common.")).toBeInTheDocument();
    expect(screen.getByLabelText("New password")).toBeInTheDocument();                                  // the form is still there
    await userEvent.clear(screen.getByLabelText("New password"));
    await userEvent.clear(screen.getByLabelText("Confirm new password"));
    await fillPasswords();
    await userEvent.click(screen.getByRole("button", { name: "Save password" }));
    expect(await screen.findByText("Your password is set. You can now sign in.")).toBeInTheDocument();
    expect(posts(api, "/api/auth/set-password/").map((c) => body(c).token)).toEqual([TOKEN, TOKEN]);
  });

  it("shows one plain message for an invalid, expired or used link, with no account details", async () => {
    document.cookie = "csrftoken=abc; path=/";
    publicApi({ "POST /api/auth/set-password/": () => json({ detail: "This link is invalid or has expired." }, 400) });
    renderAt(`/set-password#token=${TOKEN}`);
    await fillPasswords();
    await userEvent.click(screen.getByRole("button", { name: "Save password" }));
    const alert = await screen.findByRole("alert");
    expect(alert).toHaveTextContent("This link is invalid or has expired. Ask your administrator for a new one.");
    expect(screen.queryByLabelText("New password")).not.toBeInTheDocument();
    expect(screen.getByRole("link", { name: "Go to sign in" })).toBeInTheDocument();
    expect(document.body.textContent).not.toContain(TOKEN);
  });

  it("treats an address with no token as an invalid link without calling the server", async () => {
    const api = publicApi();
    renderAt("/set-password");
    expect(await screen.findByRole("alert")).toHaveTextContent("This link is invalid or has expired.");
    renderAt("/set-password#nonsense=1");
    expect(posts(api, "/api/auth/set-password/")).toHaveLength(0);
  });

  it("asks the person to wait when the rate limit is reached, and keeps the form", async () => {
    document.cookie = "csrftoken=abc; path=/";
    publicApi({ "POST /api/auth/set-password/": () => json({ detail: "Request was throttled." }, 429) });
    renderAt(`/set-password#token=${TOKEN}`);
    await fillPasswords();
    await userEvent.click(screen.getByRole("button", { name: "Save password" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("Too many attempts. Please wait a while and try again.");
    expect(screen.getByLabelText("New password")).toBeInTheDocument();
  });

  it("shows a network error and keeps the form", async () => {
    document.cookie = "csrftoken=abc; path=/";
    const api = publicApi();
    renderAt(`/set-password#token=${TOKEN}`);
    await fillPasswords();
    api.fn.mockImplementation(async (url) => { if (String(url).includes("set-password")) throw new TypeError("offline"); return json({}); });
    await userEvent.click(screen.getByRole("button", { name: "Save password" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("Could not reach the server. Check your connection.");
    expect(screen.getByLabelText("New password")).toBeInTheDocument();
  });

  it("is a public page: a signed-in person can still use a link they were sent", async () => {
    mockApi(baseRoutes("md", { "POST /api/auth/set-password/": { detail: "ok" } }));
    renderAt(`/set-password#token=${TOKEN}`);
    expect(await screen.findByRole("heading", { name: "Set your password" })).toBeInTheDocument();
  });
});

describe("reset password", () => {
  it("uses the reset endpoint and wording", async () => {
    document.cookie = "csrftoken=abc; path=/";
    const api = publicApi({ "POST /api/auth/reset-password/": { detail: "Your password has been reset." } });
    renderAt(`/reset-password#token=${TOKEN}`);
    expect(await screen.findByRole("heading", { name: "Reset your password" })).toBeInTheDocument();
    await fillPasswords();
    await userEvent.click(screen.getByRole("button", { name: "Save password" }));
    expect(await screen.findByText("Your password is set. You can now sign in.")).toBeInTheDocument();
    expect(body(posts(api, "/api/auth/reset-password/")[0])).toEqual({ token: TOKEN, password: STRONG });
    expect(posts(api, "/api/auth/set-password/")).toHaveLength(0);
  });

  it("shows the same plain message for a bad link", async () => {
    document.cookie = "csrftoken=abc; path=/";
    publicApi({ "POST /api/auth/reset-password/": () => json({ detail: "This link is invalid or has expired." }, 400) });
    renderAt(`/reset-password#token=${TOKEN}`);
    await fillPasswords();
    await userEvent.click(screen.getByRole("button", { name: "Save password" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("This link is invalid or has expired.");
  });
});

describe("change password (signed in)", () => {
  const signedIn = (extra = {}, role = "md") => mockApi(baseRoutes(role, extra));
  const fill = async (current, next, confirm = next) => {
    await userEvent.type(await screen.findByLabelText("Current password"), current);
    await userEvent.type(screen.getByLabelText("New password"), next);
    await userEvent.type(screen.getByLabelText("Confirm new password"), confirm);
  };

  it("is reached from the top bar", async () => {
    signedIn();
    renderApp("/");
    await userEvent.click(await screen.findByRole("link", { name: "Change password" }));
    expect(await screen.findByRole("heading", { name: "Change password" })).toBeInTheDocument();
  });

  it.each(["md", "manager", "staff", "sales", "viewer"])("%s can open it", async (role) => {
    signedIn({}, role);
    renderApp("/account/password");
    expect(await screen.findByRole("heading", { name: "Change password" })).toBeInTheDocument();
  });

  it("changes the password and clears the form", async () => {
    document.cookie = "csrftoken=abc; path=/";
    const api = signedIn({ "POST /api/auth/change-password/": { detail: "Your password has been changed." } });
    renderApp("/account/password");
    await fill("Old-Passw0rd-xx", STRONG);
    await userEvent.click(screen.getByRole("button", { name: "Change password" }));
    expect(await screen.findByText("Your password has been changed.")).toBeInTheDocument();
    expect(body(posts(api, "/api/auth/change-password/")[0])).toEqual({ current_password: "Old-Passw0rd-xx", new_password: STRONG });
    expect(screen.getByLabelText("Current password")).toHaveValue("");
    expect(screen.getByLabelText("New password")).toHaveValue("");
  });

  it("shows the server's reasons next to the right field", async () => {
    document.cookie = "csrftoken=abc; path=/";
    let attempt = 0;
    signedIn({
      "POST /api/auth/change-password/": () => {
        attempt += 1;
        return attempt === 1
          ? json({ current_password: ["Incorrect password."] }, 400)
          : json({ new_password: ["This password is too common."] }, 400);
      },
    });
    renderApp("/account/password");
    await fill("wrong", STRONG);
    await userEvent.click(screen.getByRole("button", { name: "Change password" }));
    expect(await screen.findByText("Incorrect password.")).toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: "Change password" }));
    expect(await screen.findByText("This password is too common.")).toBeInTheDocument();
  });

  it("blocks mismatched passwords and explains the rate limit", async () => {
    document.cookie = "csrftoken=abc; path=/";
    const api = signedIn({ "POST /api/auth/change-password/": () => json({ detail: "Request was throttled." }, 429) });
    renderApp("/account/password");
    await fill("Old-Passw0rd-xx", STRONG, "different");
    expect(screen.getByText("The two passwords do not match.")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Change password" })).toBeDisabled();
    await userEvent.clear(screen.getByLabelText("Confirm new password"));
    await userEvent.type(screen.getByLabelText("Confirm new password"), STRONG);
    await userEvent.click(screen.getByRole("button", { name: "Change password" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("Too many attempts. Please wait a while and try again.");
    expect(posts(api, "/api/auth/change-password/")).toHaveLength(1);
  });

  it("sends a signed-out visitor to the login page", async () => {
    mockApi(baseRoutes("md", { "GET /api/auth/me/": anonymous }));
    renderApp("/account/password");
    expect(await screen.findByRole("button", { name: "Sign in" })).toBeInTheDocument();
  });
});

describe("sign in with email or username", () => {
  const login = async (identifier, password = "secret") => {
    await userEvent.type(await screen.findByLabelText("Email or username"), identifier);
    await userEvent.type(screen.getByLabelText("Password"), password);
    await userEvent.click(screen.getByRole("button", { name: "Sign in" }));
  };

  it("labels the field 'Email or username'", async () => {
    mockApi(baseRoutes("md", { "GET /api/auth/me/": anonymous }));
    renderApp("/");
    expect(await screen.findByLabelText("Email or username")).toBeInTheDocument();
    expect(screen.queryByLabelText("Username")).not.toBeInTheDocument();
  });

  it.each(["Ada@Acme.Test", "  ada@acme.test  ", "md_demohomes"])("sends %j as the same 'username' field the API already expects", async (typed) => {
    document.cookie = "csrftoken=abc; path=/";
    let signed = false;
    const api = mockApi(baseRoutes("md", {
      "GET /api/auth/me/": () => (signed ? fixtures.me.md : anonymous()),
      "POST /api/auth/login/": () => { signed = true; return fixtures.me.md; },
    }));
    renderApp("/");
    await login(typed);
    expect(await screen.findByText(fixtures.me.md.full_name)).toBeInTheDocument();
    expect(body(posts(api, "/api/auth/login/")[0])).toEqual({ username: typed.trim(), password: "secret" });
  });

  it("gives the same plain failure message for any wrong sign-in", async () => {
    document.cookie = "csrftoken=abc; path=/";
    mockApi(baseRoutes("md", {
      "GET /api/auth/me/": anonymous,
      "POST /api/auth/login/": () => json({ detail: "Invalid credentials." }, 400),
    }));
    renderApp("/");
    await login("nobody@acme.test", "nope");
    expect(await screen.findByRole("alert")).toHaveTextContent("That email or username and password do not match.");
  });

  it("returns to the page you were going to after a sign-in", async () => {
    document.cookie = "csrftoken=abc; path=/";
    let signed = false;
    mockApi(baseRoutes("md", {
      "GET /api/auth/me/": () => (signed ? fixtures.me.md : anonymous()),
      "POST /api/auth/login/": () => { signed = true; return fixtures.me.md; },
    }));
    renderApp("/staff");
    await login("md_demohomes");
    expect(await screen.findByRole("heading", { name: "Staff" })).toBeInTheDocument();
  });
});
