import { describe, expect, it } from "vitest";
import { screen, within } from "@testing-library/react";
import { baseRoutes, fixtures, mockApi, renderApp } from "./test/helpers.jsx";

const href = (el) => el.getAttribute("href");

describe("MD home", () => {
  const md = fixtures.home.md;
  const setup = async () => {
    mockApi(baseRoutes("md"));
    renderApp("/");
    await screen.findByRole("heading", { name: "What needs attention" });
  };

  it("greets the person by first name with the as-of date", async () => {
    await setup();
    expect(screen.getByRole("heading", { name: "Welcome, Folake" })).toBeInTheDocument();
    expect(screen.getByText(/As of \d{1,2} \w{3} \d{4}/)).toBeInTheDocument();
  });

  it("shows the money owed in documents in full naira", async () => {
    await setup();
    expect(screen.getByText("₦5,528,950,000")).toBeInTheDocument();
    expect(screen.getByText(`across ${md.exposure.buyers} buyers`)).toBeInTheDocument();
  });

  it("makes every attention number a link to the matching list", async () => {
    await setup();
    const panel = screen.getByRole("heading", { name: "What needs attention" }).closest("section");
    const tile = (label) => within(panel).getByText(label).closest("a");
    expect(tile("Overdue")).toHaveTextContent(String(md.attention.overdue));
    expect(href(tile("Overdue"))).toBe("/obligations?overdue=1");
    expect(href(tile("Due this week"))).toBe("/obligations?due_within=7");
    expect(href(tile("Not updated in 14+ days"))).toBe("/obligations?stale=1");
    expect(href(tile("Waiting on others"))).toBe("/obligations?status=awaiting_external");
    expect(href(tile("Need an owner or next action"))).toBe("/obligations?incomplete=1");
    expect(href(tile("All open"))).toBe("/obligations?open=1");
  });

  it("links each ageing bucket to its list", async () => {
    await setup();
    const list = screen.getByRole("list", { name: "Ageing" });
    const links = within(list).getAllByRole("link").map(href);
    expect(links).toEqual([
      "/obligations?age_max=30", "/obligations?age_min=31&age_max=60",
      "/obligations?age_min=61&age_max=90", "/obligations?age_min=91",
    ]);
    expect(within(list).getByText(String(md.ageing.d90_plus))).toBeInTheDocument();
  });

  it("lists estates with links, and flags overdue counts as links too", async () => {
    await setup();
    const first = md.by_estate[0];
    const link = screen.getByRole("link", { name: first.estate });
    expect(href(link)).toBe(`/obligations?estate=${first.estate_id}&open=1`);
    const row = link.closest("tr");
    expect(within(row).getByRole("link", { name: String(first.overdue) })).toHaveAttribute("href", `/obligations?estate=${first.estate_id}&overdue=1`);
  });

  it("explains that per-document amounts overlap", async () => {
    await setup();
    expect(screen.getByText(/must not be added together/)).toBeInTheDocument();
    expect(screen.getByRole("link", { name: md.by_type[0].type })).toHaveAttribute("href", `/obligations?type=${md.by_type[0].type_id}&open=1`);
  });

  it("shows the oldest cases and links to each one", async () => {
    await setup();
    const first = md.oldest[0];
    expect(screen.getByRole("link", { name: first.buyer_name })).toHaveAttribute("href", `/obligations/${first.id}`);
    expect(screen.getAllByText(/days overdue/).length).toBeGreaterThan(0);
  });

  it("only mentions cancelled sales when there are some", async () => {
    await setup();
    expect(screen.queryByText(/cancelled in the last 30 days/)).not.toBeInTheDocument();
  });
});

describe("other roles' homes", () => {
  it("manager sees who holds the ball", async () => {
    mockApi(baseRoutes("manager"));
    renderApp("/");
    expect(await screen.findByRole("heading", { name: "Who holds the ball" })).toBeInTheDocument();
    const owner = fixtures.home.manager.workload.by_owner[0];
    expect(screen.getByRole("link", { name: owner.owner })).toHaveAttribute("href", `/obligations?owner=${owner.owner_id}&open=1`);
    expect(screen.getByText("Unassigned")).toBeInTheDocument();
  });

  it("staff sees their own work first and no company money", async () => {
    mockApi(baseRoutes("staff"));
    renderApp("/");
    expect(await screen.findByRole("heading", { name: "Your work" })).toBeInTheDocument();
    const q = fixtures.home.staff.queue;
    expect(screen.getByText("Overdue").closest("a")).toHaveTextContent(String(q.overdue));
    expect(screen.queryByText(/documents still owed/)).not.toBeInTheDocument();
    expect(screen.getByRole("link", { name: "Open my queue" })).toHaveAttribute("href", "/queue");
  });

  it("sales gets a search prompt, not a dashboard", async () => {
    mockApi(baseRoutes("sales"));
    renderApp("/");
    expect(await screen.findByRole("heading", { name: "Find a buyer's document status" })).toBeInTheDocument();
    expect(screen.queryByText("What needs attention")).not.toBeInTheDocument();
  });

  it("viewer sees the executive layout", async () => {
    mockApi(baseRoutes("viewer"));
    renderApp("/");
    expect(await screen.findByRole("heading", { name: "What needs attention" })).toBeInTheDocument();
  });

  it("shows an error with a retry when the dashboard fails", async () => {
    let calls = 0;
    mockApi(baseRoutes("md", {
      "GET /api/dashboard/home/": () => (++calls === 1 ? new Response(JSON.stringify({ detail: "Server error" }), { status: 500, headers: { "Content-Type": "application/json" } }) : fixtures.home.md),
    }));
    renderApp("/");
    expect(await screen.findByRole("alert")).toHaveTextContent("Server error");
    const { default: userEvent } = await import("@testing-library/user-event");
    await userEvent.click(screen.getByRole("button", { name: "Try again" }));
    expect(await screen.findByRole("heading", { name: "What needs attention" })).toBeInTheDocument();
  });
});
