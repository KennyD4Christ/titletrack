import { describe, expect, it } from "vitest";
import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { baseRoutes, fixtures, json, mockApi, renderApp } from "./test/helpers.jsx";

const anonymous = () => json({ detail: "Authentication credentials were not provided." }, 403);

describe("signing in and out", () => {
  it("sends a signed-out visitor to the login page, then back to where they were going", async () => {
    let signedIn = false;
    mockApi(baseRoutes("manager", {
      "GET /api/auth/me/": () => (signedIn ? fixtures.me.manager : anonymous()),
      "POST /api/auth/login/": () => { signedIn = true; return fixtures.me.manager; },
    }));
    renderApp("/obligations?overdue=1");
    expect(await screen.findByRole("button", { name: "Sign in" })).toBeInTheDocument();
    await userEvent.type(screen.getByLabelText("Username"), "manager_demohomes");
    await userEvent.type(screen.getByLabelText("Password"), "secret");
    await userEvent.click(screen.getByRole("button", { name: "Sign in" }));
    expect(await screen.findByRole("heading", { name: "Obligations" })).toBeInTheDocument();
  });

  it("shows a plain message for wrong credentials", async () => {
    mockApi(baseRoutes("md", {
      "GET /api/auth/me/": anonymous,
      "POST /api/auth/login/": () => json({ detail: "Invalid credentials." }, 400),
    }));
    renderApp("/");
    await userEvent.type(await screen.findByLabelText("Username"), "x");
    await userEvent.type(screen.getByLabelText("Password"), "y");
    await userEvent.click(screen.getByRole("button", { name: "Sign in" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("That username and password do not match.");
  });

  it("signs out and clears the screen", async () => {
    let signedIn = true;
    mockApi(baseRoutes("md", {
      "GET /api/auth/me/": () => (signedIn ? fixtures.me.md : anonymous()),
      "POST /api/auth/logout/": () => { signedIn = false; return new Response(null, { status: 204 }); },
    }));
    document.cookie = "csrftoken=abc; path=/";
    renderApp("/");
    expect(await screen.findByText(fixtures.me.md.full_name)).toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: "Sign out" }));
    expect(await screen.findByRole("button", { name: "Sign in" })).toBeInTheDocument();
    expect(screen.queryByText(fixtures.me.md.full_name)).not.toBeInTheDocument();
  });

  it("returns to the login page if the session expires while working", async () => {
    mockApi(baseRoutes("md", {
      "GET /api/dashboard/home/": anonymous,
    }));
    renderApp("/");
    expect(await screen.findByRole("button", { name: "Sign in" })).toBeInTheDocument();
  });
});

describe("menu and page access by role", () => {
  const menu = async (role) => {
    mockApi(baseRoutes(role));
    renderApp("/");
    const nav = await screen.findByRole("navigation", { name: "Main" });
    return within(nav).getAllByRole("link").map((a) => a.textContent);
  };

  it.each(["md", "manager"])("%s gets Home, My Queue, Obligations and Import", async (role) => {
    expect(await menu(role)).toEqual(["Home", "My Queue", "Obligations", "Import"]);
  });
  it("staff get Home, My Queue and Obligations (no Import)", async () => {
    expect(await menu("staff")).toEqual(["Home", "My Queue", "Obligations"]);
  });
  it.each(["sales", "viewer"])("%s gets no queue", async (role) => {
    expect(await menu(role)).toEqual(["Home", "Obligations"]);
  });
  it("shows the company, name and role in the top bar", async () => {
    mockApi(baseRoutes("staff"));
    renderApp("/");
    expect(await screen.findByText(fixtures.me.staff.company.name)).toBeInTheDocument();
    expect(screen.getByText(fixtures.me.staff.full_name)).toBeInTheDocument();
    expect(screen.getByText("Staff")).toBeInTheDocument();
  });
  it("blocks a page the role may not use", async () => {
    mockApi(baseRoutes("sales"));
    renderApp("/queue");
    expect(await screen.findByRole("heading", { name: "You do not have access to this page" })).toBeInTheDocument();
  });
  it("shows a not-found page for unknown addresses", async () => {
    mockApi(baseRoutes("md"));
    renderApp("/nonsense");
    expect(await screen.findByRole("heading", { name: "Page not found" })).toBeInTheDocument();
  });
});
