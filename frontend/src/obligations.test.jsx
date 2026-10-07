import { describe, expect, it } from "vitest";
import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { baseRoutes, fixtures, mockApi, renderApp } from "./test/helpers.jsx";

const lastListUrl = (api) => api.urls().filter((u) => u.startsWith("/api/obligations/?")).at(-1);

describe("obligations list", () => {
  it("turns the address into API filters (every dashboard link lands here)", async () => {
    const api = mockApi(baseRoutes("manager"));
    renderApp("/obligations?overdue=1&estate=3&age_min=31&age_max=60&junk=1");
    await screen.findByRole("heading", { name: "Obligations" });
    await waitFor(() => expect(lastListUrl(api)).toBeTruthy());
    const url = new URL(lastListUrl(api), "http://x");
    expect(Object.fromEntries(url.searchParams)).toEqual({
      overdue: "1", estate: "3", age_min: "31", age_max: "60", page: "1", page_size: "25",
    });
  });

  it("shows rows with status, owner, waiting time and freshness", async () => {
    mockApi(baseRoutes("manager"));
    renderApp("/obligations");
    const first = fixtures.obligationsList.results[0];
    const link = await screen.findByRole("link", { name: first.buyer_name });
    expect(link).toHaveAttribute("href", `/obligations/${first.id}`);
    const row = link.closest("tr");
    expect(within(row).getByText(first.type_name)).toBeInTheDocument();
    expect(within(row).getByText(/days overdue/)).toBeInTheDocument();
    expect(screen.getByText(`Showing 1–25 of ${fixtures.obligationsList.count}`)).toBeInTheDocument();   // the fixture holds 5 sample rows of a 25-row page
  });

  it("applies a checkbox filter, and clears everything", async () => {
    const api = mockApi(baseRoutes("manager"));
    renderApp("/obligations");
    await screen.findByRole("link", { name: fixtures.obligationsList.results[0].buyer_name });
    await userEvent.click(screen.getByRole("checkbox", { name: "Overdue" }));
    await waitFor(() => expect(lastListUrl(api)).toContain("overdue=1"));
    await userEvent.click(screen.getByRole("button", { name: "Clear filters" }));
    await waitFor(() => expect(lastListUrl(api)).not.toContain("overdue"));
  });

  it("searches when the user presses Enter", async () => {
    const api = mockApi(baseRoutes("manager"));
    renderApp("/obligations");
    await screen.findByRole("heading", { name: "Obligations" });
    await userEvent.type(screen.getByRole("searchbox"), "Bakare{Enter}");
    await waitFor(() => expect(lastListUrl(api)).toContain("q=Bakare"));
  });

  it("shows removable chips for filters that have no control, resolving names", async () => {
    const api = mockApi(baseRoutes("manager"));
    const person = fixtures.users.results[0];
    renderApp(`/obligations?owner=${person.id}&age_min=91&due_within=7`);
    expect(await screen.findByText(`Owner: ${person.full_name}`)).toBeInTheDocument();
    expect(screen.getByText("Waiting over 90 days")).toBeInTheDocument();
    expect(screen.getByText("Due within 7 days")).toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: "Remove filter Waiting over 90 days" }));
    await waitFor(() => expect(lastListUrl(api)).not.toContain("age_min"));
    expect(lastListUrl(api)).toContain("due_within=7");
  });

  it("pages forwards", async () => {
    const api = mockApi(baseRoutes("manager", {
      "GET /api/obligations/": { ...fixtures.obligationsList, count: 80 },
    }));
    renderApp("/obligations");
    await userEvent.click(await screen.findByRole("button", { name: "Next" }));
    await waitFor(() => expect(lastListUrl(api)).toContain("page=2"));
    expect(await screen.findByText("Page 2 of 4")).toBeInTheDocument();
  });

  it("says so when nothing matches", async () => {
    mockApi(baseRoutes("manager", { "GET /api/obligations/": { count: 0, next: null, previous: null, results: [] } }));
    renderApp("/obligations?q=zzz");
    expect(await screen.findByText("Nothing matches these filters.")).toBeInTheDocument();
  });

  it("marks obligations of cancelled sales", async () => {
    const rows = fixtures.obligationsList.results.map((r, i) => (i === 0 ? { ...r, is_cancelled: true, sale_status: "cancelled" } : r));
    mockApi(baseRoutes("manager", { "GET /api/obligations/": { ...fixtures.obligationsList, results: rows } }));
    renderApp("/obligations?include_cancelled=1");
    expect(await screen.findByText("Sale cancelled")).toBeInTheDocument();
  });
});

describe("my queue", () => {
  it("groups the signed-in person's own work", async () => {
    mockApi(baseRoutes("staff"));
    renderApp("/queue");
    expect(await screen.findByRole("heading", { name: /Overdue/ })).toBeInTheDocument();
    const q = fixtures.queue;
    expect(screen.getByRole("link", { name: q.overdue[0].buyer_name })).toBeInTheDocument();
    expect(screen.queryByRole("columnheader", { name: "Owner" })).not.toBeInTheDocument();      // it is all yours
  });

  it("says when there is nothing assigned", async () => {
    mockApi(baseRoutes("staff", { "GET /api/me/queue/": { counts: { open: 0, overdue: 0, due_soon: 0, awaiting_external: 0, other: 0 }, overdue: [], due_soon: [], awaiting_external: [], other: [] } }));
    renderApp("/queue");
    expect(await screen.findByText("Nothing is assigned to you right now.")).toBeInTheDocument();
  });
});

describe("obligation detail (read-only)", () => {
  const d = fixtures.obligationDetail;
  const route = (role = "manager", extra = {}) =>
    baseRoutes(role, { [`GET /api/obligations/${d.id}/`]: d, [`GET /api/obligations/${d.id}/events/`]: fixtures.obligationEvents, ...extra });

  it("answers 'why is this overdue?' with the real numbers", async () => {
    mockApi(route());
    renderApp(`/obligations/${d.id}`);
    expect(await screen.findByRole("heading", { name: "Why is this overdue?" })).toBeInTheDocument();
    expect(screen.getByText(`${d.timeline.expected_days} days`)).toBeInTheDocument();
    expect(screen.getByText(`${d.timeline.actual_days} days`)).toBeInTheDocument();
    expect(screen.getByText(`${d.timeline.delay_days} days late`)).toBeInTheDocument();
    const why = screen.getByRole("heading", { name: "Why is this overdue?" }).closest("section");
    expect(within(why).getByText(d.reason || "No reason recorded")).toBeInTheDocument();
  });

  it("shows the history, newest first", async () => {
    mockApi(route());
    renderApp(`/obligations/${d.id}`);
    const section = (await screen.findByRole("heading", { name: "History" })).closest("section");
    const entries = within(section).getAllByRole("listitem").filter((li) => li.querySelector(".timeline-head"));
    const newest = [...fixtures.obligationEvents].reverse()[0];
    expect(entries[0]).toHaveTextContent(newest.actor_name || "System");
    expect(entries.at(-1)).toHaveTextContent("Created automatically");
    expect(entries).toHaveLength(fixtures.obligationEvents.length);
  });

  it("hides the history from roles that may not see it", async () => {
    const api = mockApi(route("sales"));
    renderApp(`/obligations/${d.id}`);
    await screen.findByRole("heading", { name: "Why is this overdue?" });
    expect(screen.queryByRole("heading", { name: "History" })).not.toBeInTheDocument();
    expect(api.urls().some((u) => u.endsWith("/events/"))).toBe(false);
  });

  it("explains a cancelled sale and drops the overdue framing", async () => {
    const cancelled = { ...d, is_cancelled: true, is_open: false, sale_status: "cancelled", days_overdue: 0,
      timeline: { ...d.timeline, delay_days: 0 }, sale_summary: { ...d.sale_summary, status: "cancelled", cancel_reason: "Buyer defaulted" } };
    mockApi(route("manager", { [`GET /api/obligations/${d.id}/`]: cancelled }));
    renderApp(`/obligations/${d.id}`);
    expect(await screen.findByText(/This sale was cancelled: Buyer defaulted/)).toBeInTheDocument();
    expect(screen.getByRole("heading", { name: "Where this stands" })).toBeInTheDocument();
    expect(screen.queryByText(/days late/)).not.toBeInTheDocument();
  });

  it("shows an error for an obligation that is not found", async () => {
    mockApi(route("manager", { "GET /api/obligations/999/": () => new Response(JSON.stringify({ detail: "Not found." }), { status: 404, headers: { "Content-Type": "application/json" } }) }));
    renderApp("/obligations/999");
    expect(await screen.findByRole("alert")).toHaveTextContent("Not found.");
  });
});
