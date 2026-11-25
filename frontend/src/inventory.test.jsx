import { describe, expect, it } from "vitest";
import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { baseRoutes, json, mockApi, renderApp } from "./test/helpers.jsx";
import { board, estate, makePreview, renderAppLikeMain } from "./test/inventoryHelpers.jsx";
import { todayIso } from "./utils/sales.js";

import boardMd from "./test/fixtures/plot_board_md.json";
import saleDetail from "./test/fixtures/sale_detail.json";
import salesList from "./test/fixtures/sales_list.json";

const body = (c) => JSON.parse(c.init.body);
const calls = (api, method, end) => api.calls.filter((c) => c.method === method && c.url.split("?")[0].endsWith(end));
const empty = { count: 0, next: null, previous: null, results: [] };
const routes = (extra = {}, role = "md") =>
  baseRoutes(role, {
    "GET /api/plot-board/": board(role),
    [`GET /api/sales-desk/${saleDetail.id}/`]: saleDetail,
    "GET /api/sales-desk/": salesList,
    "GET /api/sale-documents/": empty,
    ...extra,
  });
const openEstate = async (role = "md", extra = {}) => {
  const api = mockApi(routes(extra, role));
  renderApp(`/estates/${estate.id}`);
  await screen.findByRole("heading", { name: estate.name });
  return api;
};
const table = () => screen.getByRole("table", { name: `Plots in ${estate.name}` });
const plotNumbers = () => within(table()).getAllByRole("button").map((b) => b.textContent);
const rowOf = (number) => within(table()).getByRole("button", { name: number }).closest("tr");
const pick = (number) => userEvent.click(within(table()).getByRole("button", { name: number }));
const dialog = () => screen.getByRole("dialog");
const totals = () => within(screen.getByRole("region", { name: "Plot totals" }));

describe("estates list", () => {
  it("lists every estate with a link to its plots", async () => {
    mockApi(routes());
    renderApp("/estates");
    const link = await screen.findByRole("link", { name: "Green Valley Estate" });
    expect(link).toHaveAttribute("href", "/estates/5");
    expect(screen.getByText("Lekki, Lagos")).toBeInTheDocument();
    expect(screen.getAllByRole("row")).toHaveLength(5);                                     // header and four estates
  });

  it.each(["md", "manager"])("%s can add and edit estates", async (role) => {
    mockApi(routes({}, role));
    renderApp("/estates");
    expect(await screen.findByRole("button", { name: "Add estate" })).toBeInTheDocument();
    expect(await screen.findByRole("button", { name: "Edit Green Valley Estate" })).toBeInTheDocument();
  });

  it.each(["staff", "sales", "viewer"])("%s can look but not change anything", async (role) => {
    mockApi(routes({}, role));
    renderApp("/estates");
    await screen.findByRole("link", { name: "Green Valley Estate" });
    expect(screen.queryByRole("button", { name: "Add estate" })).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /^Edit / })).not.toBeInTheDocument();
  });

  it("says so when there are no estates, and what to do about it", async () => {
    mockApi(routes({ "GET /api/estates/": empty }));
    renderApp("/estates");
    expect(await screen.findByText("No estates yet.")).toBeInTheDocument();
    expect(screen.getByText(/Add an estate, then register its plots/)).toBeInTheDocument();
  });

  it("tells a role that cannot add estates to wait for them instead", async () => {
    mockApi(routes({ "GET /api/estates/": empty }, "viewer"));
    renderApp("/estates");
    expect(await screen.findByText(/Estates appear here once they have been added/)).toBeInTheDocument();
  });

  it("does not hide that the list is incomplete", async () => {
    mockApi(routes({ "GET /api/estates/": { count: 450, next: null, previous: null, results: [estate] } }));
    renderApp("/estates");
    expect(await screen.findByText("Showing 1 of 450 estates.")).toBeInTheDocument();
  });

  it("adds an estate and goes to its page to register plots", async () => {
    const api = mockApi(routes({ "POST /api/inventory/estates/": json({ id: 9, name: "Lakeside Meadows", location: "Epe" }, 201) }));
    renderApp("/estates");
    await userEvent.click(await screen.findByRole("button", { name: "Add estate" }));
    await userEvent.type(within(dialog()).getByLabelText("Estate name"), "  Lakeside Meadows ");
    await userEvent.type(within(dialog()).getByLabelText("Location (optional)"), "Epe");
    await userEvent.click(within(dialog()).getByRole("button", { name: "Add estate" }));
    await waitFor(() => expect(calls(api, "POST", "/api/inventory/estates/")).toHaveLength(1));
    expect(body(calls(api, "POST", "/api/inventory/estates/")[0])).toEqual({ name: "Lakeside Meadows", location: "Epe" });
    expect(await screen.findByText("Estate added. Now register its plots.")).toBeInTheDocument();
    expect(api.urls()).toContain("/api/plot-board/?estate=9");
  });

  it("keeps Add estate switched off until there is a name", async () => {
    mockApi(routes());
    renderApp("/estates");
    await userEvent.click(await screen.findByRole("button", { name: "Add estate" }));
    const add = within(dialog()).getByRole("button", { name: "Add estate" });
    expect(add).toBeDisabled();
    await userEvent.type(within(dialog()).getByLabelText("Estate name"), "   ");
    expect(add).toBeDisabled();
    await userEvent.type(within(dialog()).getByLabelText("Estate name"), "X");
    expect(add).toBeEnabled();
  });

  it("shows a duplicate with a link to the estate that is in the way", async () => {
    mockApi(routes({
      "POST /api/inventory/estates/": json({
        detail: "An estate called 'Green Valley Estate' already exists.", existing_estate: { id: 5, name: "Green Valley Estate" },
      }, 409),
    }));
    renderApp("/estates");
    await userEvent.click(await screen.findByRole("button", { name: "Add estate" }));
    await userEvent.type(within(dialog()).getByLabelText("Estate name"), "green valley estate");
    await userEvent.click(within(dialog()).getByRole("button", { name: "Add estate" }));
    expect(await within(dialog()).findByText(/already exists/)).toBeInTheDocument();
    expect(within(dialog()).getByRole("link", { name: "Open Green Valley Estate" })).toHaveAttribute("href", "/estates/5");
  });

  it("edits only what changed, and refreshes the list", async () => {
    const api = mockApi(routes({ "PATCH /api/inventory/estates/5/": json({ ...estate, location: "Ajah" }) }));
    renderApp("/estates");
    await userEvent.click(await screen.findByRole("button", { name: "Edit Green Valley Estate" }));
    const save = within(dialog()).getByRole("button", { name: "Save changes" });
    expect(save).toBeDisabled();
    await userEvent.clear(within(dialog()).getByLabelText("Location (optional)"));
    await userEvent.type(within(dialog()).getByLabelText("Location (optional)"), "Ajah");
    await userEvent.click(save);
    await waitFor(() => expect(calls(api, "PATCH", "/api/inventory/estates/5/")).toHaveLength(1));
    expect(body(calls(api, "PATCH", "/api/inventory/estates/5/")[0])).toEqual({ location: "Ajah" });
    expect(await screen.findByText("Estate saved.")).toBeInTheDocument();
    await waitFor(() => expect(calls(api, "GET", "/api/estates/").length).toBeGreaterThan(1));
  });

  it("sends the reason with a rename, and warns that imports match by name", async () => {
    const api = mockApi(routes({ "PATCH /api/inventory/estates/5/": json({ ...estate, name: "Green Valley Phase 2" }) }));
    renderApp("/estates");
    await userEvent.click(await screen.findByRole("button", { name: "Edit Green Valley Estate" }));
    expect(within(dialog()).getByText(/Spreadsheet imports match estates by name/)).toBeInTheDocument();
    await userEvent.clear(within(dialog()).getByLabelText("Estate name"));
    await userEvent.type(within(dialog()).getByLabelText("Estate name"), "Green Valley Phase 2");
    await userEvent.type(within(dialog()).getByLabelText(/^Reason/), "Rebranded");
    await userEvent.click(within(dialog()).getByRole("button", { name: "Save changes" }));
    await waitFor(() => expect(calls(api, "PATCH", "/api/inventory/estates/5/")).toHaveLength(1));
    expect(body(calls(api, "PATCH", "/api/inventory/estates/5/")[0])).toEqual({ name: "Green Valley Phase 2", reason: "Rebranded" });
  });

  it("shows the server's reason when a rename is refused", async () => {
    mockApi(routes({ "PATCH /api/inventory/estates/5/": json({ detail: "A reason of at least 3 characters is required." }, 400) }));
    renderApp("/estates");
    await userEvent.click(await screen.findByRole("button", { name: "Edit Green Valley Estate" }));
    await userEvent.clear(within(dialog()).getByLabelText("Estate name"));
    await userEvent.type(within(dialog()).getByLabelText("Estate name"), "Renamed");
    await userEvent.click(within(dialog()).getByRole("button", { name: "Save changes" }));
    expect(await within(dialog()).findByText("A reason of at least 3 characters is required.")).toBeInTheDocument();
  });
});

describe("the plot board", () => {
  it("lists plots in natural order with their status, and counts them", async () => {
    await openEstate("md");
    expect(plotNumbers()).toEqual(["A-1", "A-2", "A-3", "A-10"]);                         // the server sent A-10 second
    expect(within(rowOf("A-1")).getByText("Available")).toBeInTheDocument();
    expect(within(rowOf("A-2")).getByText("Sold")).toBeInTheDocument();
    expect(within(rowOf("A-2")).getByText("Part paid")).toBeInTheDocument();
    expect(within(rowOf("A-10")).getByText("Fully paid")).toBeInTheDocument();
    for (const [label, count] of [["Plots", "4"], ["Available", "2"], ["Sold", "2"], ["Part paid", "1"], ["Fully paid", "1"]]) {
      expect(within(totals().getByText(label).closest(".tile")).getByText(count)).toBeInTheDocument();
    }
  });

  it("shows a role without view_sales the status in words and nothing about payment", async () => {
    for (const role of ["staff", "sales", "viewer"]) {
      mockApi(routes({}, role));
      const view = renderApp(`/estates/${estate.id}`);
      await screen.findByRole("heading", { name: estate.name });
      expect(plotNumbers(), role).toEqual(["A-1", "A-2", "A-3", "A-10"]);
      expect(within(rowOf("A-2")).getByText("Sold"), role).toBeInTheDocument();
      expect(screen.queryByText("Part paid"), role).not.toBeInTheDocument();
      expect(screen.queryByText("Fully paid"), role).not.toBeInTheDocument();
      expect(screen.queryByLabelText("Payment"), role).not.toBeInTheDocument();
      expect(totals().queryByText("Part paid"), role).not.toBeInTheDocument();
      view.unmount();
    }
  });

  it("never asks the Sales desk for anything on behalf of a role that may not see sales", async () => {
    for (const role of ["staff", "sales", "viewer"]) {
      const api = mockApi(routes({}, role));
      const view = renderApp(`/estates/${estate.id}`);
      await screen.findByRole("heading", { name: estate.name });
      await pick("A-2");
      expect(await screen.findByRole("heading", { name: "Plot A-2" })).toBeInTheDocument();
      expect(screen.queryByText("Bayo Lawal")).not.toBeInTheDocument();
      expect(screen.queryByRole("link", { name: "Open the sale" })).not.toBeInTheDocument();
      expect(api.urls().filter((u) => u.includes("/api/sales-desk/"))).toEqual([]);
      view.unmount();
    }
  });

  it("filters by plot number, status and payment, and clears the filters", async () => {
    await openEstate("md");
    await userEvent.type(screen.getByRole("searchbox", { name: "Search plot number" }), "a-1");
    expect(plotNumbers()).toEqual(["A-1", "A-10"]);
    await userEvent.click(screen.getByRole("button", { name: "Clear filters" }));
    await userEvent.selectOptions(screen.getByLabelText("Status"), "sold");
    expect(plotNumbers()).toEqual(["A-2", "A-10"]);
    await userEvent.selectOptions(screen.getByLabelText("Payment"), "part_paid");
    expect(plotNumbers()).toEqual(["A-2"]);
    await userEvent.type(screen.getByRole("searchbox", { name: "Search plot number" }), "zzz");
    expect(await screen.findByText("No plots match these filters.")).toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: "Clear filters" }));
    expect(plotNumbers()).toHaveLength(4);
  });

  it("says when an estate has no plots yet, and what a manager can do about it", async () => {
    await openEstate("md", { "GET /api/plot-board/": { ...boardMd, plots: [], count: 0 } });
    expect(await screen.findByText("No plots registered yet.")).toBeInTheDocument();
    expect(screen.getByText(/one at a time, or many at once/)).toBeInTheDocument();
  });

  it("tells a role that cannot register plots to wait for them instead", async () => {
    await openEstate("viewer", { "GET /api/plot-board/": { ...board("viewer"), plots: [], count: 0 } });
    expect(await screen.findByText(/Plots appear here once they have been registered/)).toBeInTheDocument();
  });

  it("says when only part of a very large estate is shown", async () => {
    await openEstate("md", { "GET /api/plot-board/": { ...boardMd, truncated: true, count: 9000 } });
    expect(await screen.findByText("This estate has 9000 plots. Only the first 4 are shown.")).toBeInTheDocument();
  });

  it("explains an estate that does not exist or belongs to someone else, and offers the way back", async () => {
    mockApi(routes({ "GET /api/plot-board/": json({ detail: "Not found." }, 404) }));
    renderApp("/estates/999");
    expect(await screen.findByText("This estate was not found.")).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "← All estates" })).toHaveAttribute("href", "/estates");
    expect(screen.queryByRole("button", { name: "Try again" })).not.toBeInTheDocument();
  });

  it("offers no way to change anything to a role that may only look", async () => {
    for (const role of ["staff", "sales", "viewer"]) {
      mockApi(routes({}, role));
      const view = renderApp(`/estates/${estate.id}`);
      await screen.findByRole("heading", { name: estate.name });
      for (const name of ["Register plot", "Register many plots", "Edit estate"]) {
        expect(screen.queryByRole("button", { name })).not.toBeInTheDocument();
      }
      await pick("A-1");
      for (const name of ["Rename plot", "Remove plot", "Add sale for this plot"]) {
        expect(screen.queryByRole("button", { name })).not.toBeInTheDocument();
      }
      view.unmount();
    }
  });
});

describe("the plot panel", () => {
  it("shows a sold plot's sale to a role that may see sales, from the Sales desk's own endpoint", async () => {
    const api = await openEstate("md");
    await pick("A-2");
    expect(await screen.findByRole("heading", { name: "Plot A-2" })).toBeInTheDocument();
    const panel = within(screen.getByRole("heading", { name: "Plot A-2" }).closest("section"));
    expect(await panel.findByText("Bayo Lawal")).toBeInTheDocument();
    for (const text of ["₦1,000,000", "₦400,000", "₦600,000", "1 open of 1"]) expect(panel.getByText(text)).toBeInTheDocument();
    expect(panel.getByRole("link", { name: "Open the sale" })).toHaveAttribute("href", `/sales/${saleDetail.id}`);
    expect(api.urls()).toContain(`/api/sales-desk/${saleDetail.id}/`);
    expect(panel.queryByRole("button", { name: "Add sale for this plot" })).not.toBeInTheDocument();      // it is sold
  });

  it("offers Add sale only for an available plot, and only to a role that may add sales", async () => {
    await openEstate("md");
    await pick("A-1");
    expect(await screen.findByRole("button", { name: "Add sale for this plot" })).toBeInTheDocument();
    expect(screen.queryByText("The sale")).not.toBeInTheDocument();
  });

  it("closes", async () => {
    await openEstate("md");
    await pick("A-1");
    await userEvent.click(await screen.findByRole("button", { name: "Close plot details" }));
    expect(screen.queryByRole("heading", { name: "Plot A-1" })).not.toBeInTheDocument();
    expect(screen.getByText(/Select a plot to see its details/)).toBeInTheDocument();
  });
});

describe("registering, renaming and removing plots", () => {
  it("registers one plot and refreshes the board", async () => {
    const api = await openEstate("md", {
      "POST /api/inventory/plots/": json({ id: 120, estate: 5, estate_name: estate.name, plot_number: "A-5" }, 201),
    });
    await userEvent.click(screen.getByRole("button", { name: "Register plot" }));
    await userEvent.type(within(dialog()).getByLabelText("Plot number"), " A-5 ");
    await userEvent.click(within(dialog()).getByRole("button", { name: "Register plot" }));
    await waitFor(() => expect(calls(api, "POST", "/api/inventory/plots/")).toHaveLength(1));
    expect(body(calls(api, "POST", "/api/inventory/plots/")[0])).toEqual({ estate: 5, plot_number: "A-5" });
    expect(await screen.findByText("Plot A-5 registered.")).toBeInTheDocument();
    await waitFor(() => expect(calls(api, "GET", "/api/plot-board/").length).toBeGreaterThan(1));
  });

  it("shows a duplicate number plainly and keeps the dialog open", async () => {
    await openEstate("md", {
      "POST /api/inventory/plots/": json({ detail: "Plot 'A-1' already exists in Green Valley Estate.", existing_plot: { id: 101, plot_number: "A-1" } }, 409),
    });
    await userEvent.click(screen.getByRole("button", { name: "Register plot" }));
    await userEvent.type(within(dialog()).getByLabelText("Plot number"), "a-1");
    await userEvent.click(within(dialog()).getByRole("button", { name: "Register plot" }));
    expect(await within(dialog()).findByText(/already exists/)).toBeInTheDocument();
  });

  it("renames a plot, sending the reason, and refreshes the board", async () => {
    const api = await openEstate("md", { "PATCH /api/inventory/plots/101/": json({ id: 101, estate: 5, estate_name: estate.name, plot_number: "A-1B" }) });
    await pick("A-1");
    await userEvent.click(await screen.findByRole("button", { name: "Rename plot" }));
    expect(within(dialog()).getByRole("button", { name: "Rename plot" })).toBeDisabled();
    await userEvent.clear(within(dialog()).getByLabelText("New plot number"));
    await userEvent.type(within(dialog()).getByLabelText("New plot number"), "A-1B");
    await userEvent.type(within(dialog()).getByLabelText(/^Reason/), "Survey correction");
    await userEvent.click(within(dialog()).getByRole("button", { name: "Rename plot" }));
    await waitFor(() => expect(calls(api, "PATCH", "/api/inventory/plots/101/")).toHaveLength(1));
    expect(body(calls(api, "PATCH", "/api/inventory/plots/101/")[0])).toEqual({ plot_number: "A-1B", reason: "Survey correction" });
    expect(await screen.findByText("Plot renamed to A-1B.")).toBeInTheDocument();
    await waitFor(() => expect(calls(api, "GET", "/api/plot-board/").length).toBeGreaterThan(1));
  });

  it("shows why a rename was refused", async () => {
    await openEstate("md", { "PATCH /api/inventory/plots/102/": json({ detail: "A reason of at least 3 characters is required." }, 400) });
    await pick("A-2");
    await userEvent.click(await screen.findByRole("button", { name: "Rename plot" }));
    await userEvent.clear(within(dialog()).getByLabelText("New plot number"));
    await userEvent.type(within(dialog()).getByLabelText("New plot number"), "A-22");
    await userEvent.click(within(dialog()).getByRole("button", { name: "Rename plot" }));
    expect(await within(dialog()).findByText("A reason of at least 3 characters is required.")).toBeInTheDocument();
  });

  it("removes a plot with a reason, closes the panel and refreshes the board", async () => {
    const api = await openEstate("md", { "POST /api/inventory/plots/101/remove/": json({ id: 101, plot_number: "A-1", removed: true }) });
    await pick("A-1");
    await userEvent.click(await screen.findByRole("button", { name: "Remove plot" }));
    expect(within(dialog()).getByRole("heading", { name: "Remove plot A-1" })).toBeInTheDocument();
    await userEvent.type(within(dialog()).getByLabelText("Reason for removing it"), "Registered twice by mistake");
    await userEvent.click(within(dialog()).getByRole("button", { name: "Remove plot" }));
    await waitFor(() => expect(calls(api, "POST", "/api/inventory/plots/101/remove/")).toHaveLength(1));
    expect(body(calls(api, "POST", "/api/inventory/plots/101/remove/")[0])).toEqual({ reason: "Registered twice by mistake" });
    expect(await screen.findByText("Plot A-1 removed.")).toBeInTheDocument();
    expect(screen.queryByRole("heading", { name: "Plot A-1" })).not.toBeInTheDocument();
    await waitFor(() => expect(calls(api, "GET", "/api/plot-board/").length).toBeGreaterThan(1));
  });

  it("explains why a plot with sales history cannot be removed, and keeps it", async () => {
    await openEstate("md", {
      "POST /api/inventory/plots/102/remove/": json({ detail: "Plot 'A-2' has sales history (cancelled sales count), so it cannot be removed." }, 409),
    });
    await pick("A-2");
    await userEvent.click(await screen.findByRole("button", { name: "Remove plot" }));
    await userEvent.type(within(dialog()).getByLabelText("Reason for removing it"), "wanted it gone");
    await userEvent.click(within(dialog()).getByRole("button", { name: "Remove plot" }));
    expect(await within(dialog()).findByText(/has sales history/)).toBeInTheDocument();
    expect(plotNumbers()).toContain("A-2");
  });
});

describe("registering many plots", () => {
  const openBulk = async (extra) => {
    const api = await openEstate("md", extra);
    await userEvent.click(screen.getByRole("button", { name: "Register many plots" }));
    return api;
  };
  const enterLabels = (text) => userEvent.type(within(dialog()).getByLabelText("One plot number per line"), text);
  const preview = () => userEvent.click(within(dialog()).getByRole("button", { name: "Preview" }));

  it("previews, then registers exactly what was previewed, sending both fingerprints", async () => {
    const api = await openBulk({
      "POST /api/inventory/plots/bulk-preview/": json(makePreview(["A-4", "A-5"])),
      "POST /api/inventory/plots/bulk-commit/": json({ created: 2, plots: [], skipped_existing: [], skipped_repeated: [] }, 201),
    });
    await enterLabels("A-4{Enter}A-5");
    await preview();
    await waitFor(() => expect(calls(api, "POST", "/api/inventory/plots/bulk-preview/")).toHaveLength(1));
    expect(body(calls(api, "POST", "/api/inventory/plots/bulk-preview/")[0])).toEqual({ estate: 5, entries: [{ labels: "A-4\nA-5" }] });
    expect(await within(dialog()).findByRole("heading", { name: "Register many plots: review" })).toBeInTheDocument();
    expect(within(dialog()).getAllByText("Will be registered")).toHaveLength(2);
    expect(calls(api, "POST", "/api/inventory/plots/bulk-commit/")).toHaveLength(0);              // nothing registered yet
    await userEvent.click(within(dialog()).getByRole("button", { name: "Register 2 plots" }));
    await waitFor(() => expect(calls(api, "POST", "/api/inventory/plots/bulk-commit/")).toHaveLength(1));
    expect(body(calls(api, "POST", "/api/inventory/plots/bulk-commit/")[0])).toEqual({
      estate: 5, entries: [{ labels: "A-4\nA-5" }], input_fingerprint: "in-f1", preview_fingerprint: "pv-f1",
    });
    expect(await screen.findByText("2 plots registered.")).toBeInTheDocument();
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
    await waitFor(() => expect(calls(api, "GET", "/api/plot-board/").length).toBeGreaterThan(1));
  });

  it("reports what was skipped", async () => {
    await openBulk({
      "POST /api/inventory/plots/bulk-preview/": json(makePreview(["A-1", "A-9", "a-9"], { outcomes: { "A-1": "exists", "a-9": "duplicate_in_request" } })),
      "POST /api/inventory/plots/bulk-commit/": json({ created: 1, plots: [], skipped_existing: ["A-1"], skipped_repeated: ["a-9"] }, 201),
    });
    await enterLabels("A-1{Enter}A-9{Enter}a-9");
    await preview();
    expect(await within(dialog()).findByText(/1 already registered \(skipped\); 1 repeated in your list \(skipped\)/)).toBeInTheDocument();
    await userEvent.click(within(dialog()).getByRole("button", { name: "Register 1 plot" }));
    expect(await screen.findByText("1 plot registered. 1 already registered (skipped). 1 repeated (skipped).")).toBeInTheDocument();
  });

  it("builds a range, with an example, and sends numbers as numbers", async () => {
    const api = await openBulk({ "POST /api/inventory/plots/bulk-preview/": json(makePreview(["B-01", "B-02", "B-03"])) });
    await userEvent.click(within(dialog()).getByRole("radio", { name: "A range" }));
    await userEvent.type(within(dialog()).getByLabelText("Prefix (optional)"), "B-");
    await userEvent.type(within(dialog()).getByLabelText("From"), "1");
    await userEvent.type(within(dialog()).getByLabelText("To"), "3");
    await userEvent.clear(within(dialog()).getByLabelText("Zero-fill to this many digits"));
    await userEvent.type(within(dialog()).getByLabelText("Zero-fill to this many digits"), "2");
    expect(within(dialog()).getByText("This makes B-01 to B-03.")).toBeInTheDocument();
    expect(within(dialog()).getByText("3 plots entered (the most in one request is 500).")).toBeInTheDocument();
    await preview();
    await waitFor(() => expect(calls(api, "POST", "/api/inventory/plots/bulk-preview/")).toHaveLength(1));
    expect(body(calls(api, "POST", "/api/inventory/plots/bulk-preview/")[0]).entries).toEqual([
      { prefix: "B-", start: 1, end: 3, pad: 2, suffix: "" },
    ]);
  });

  it("covers estates numbered in blocks with several entries in one request", async () => {
    const api = await openBulk({ "POST /api/inventory/plots/bulk-preview/": json(makePreview(["A-1", "B-1", "B-2"])) });
    await enterLabels("A-1");
    await userEvent.click(within(dialog()).getByRole("button", { name: "Add another block" }));
    expect(within(dialog()).getByText("Block 1")).toBeInTheDocument();
    expect(within(dialog()).getByText("Block 2")).toBeInTheDocument();
    const second = within(within(dialog()).getByText("Block 2").closest("fieldset"));
    await userEvent.type(second.getByLabelText("Prefix (optional)"), "B-");
    await userEvent.type(second.getByLabelText("From"), "1");
    await userEvent.type(second.getByLabelText("To"), "2");
    await preview();
    await waitFor(() => expect(calls(api, "POST", "/api/inventory/plots/bulk-preview/")).toHaveLength(1));
    expect(body(calls(api, "POST", "/api/inventory/plots/bulk-preview/")[0]).entries).toEqual([
      { labels: "A-1" }, { prefix: "B-", start: 1, end: 2, pad: 0, suffix: "" },
    ]);
  });

  it("catches an obvious mistake before asking the server", async () => {
    const api = await openBulk();
    expect(within(dialog()).getByRole("button", { name: "Preview" })).toBeDisabled();
    await userEvent.click(within(dialog()).getByRole("radio", { name: "A range" }));
    await userEvent.type(within(dialog()).getByLabelText("From"), "9");
    await userEvent.type(within(dialog()).getByLabelText("To"), "2");
    expect(within(dialog()).getByText(/ends before it starts/)).toBeInTheDocument();
    expect(within(dialog()).getByRole("button", { name: "Preview" })).toBeDisabled();
    await userEvent.clear(within(dialog()).getByLabelText("To"));
    await userEvent.type(within(dialog()).getByLabelText("To"), "900");
    expect(within(dialog()).getByText(/at most 500/)).toBeInTheDocument();
    expect(within(dialog()).getByRole("button", { name: "Preview" })).toBeDisabled();
    expect(calls(api, "POST", "/api/inventory/plots/bulk-preview/")).toHaveLength(0);
  });

  it("will not register while any plot number is invalid", async () => {
    await openBulk({ "POST /api/inventory/plots/bulk-preview/": json(makePreview(["A-6", "bad"], { outcomes: { bad: "invalid" } })) });
    await enterLabels("A-6{Enter}bad");
    await preview();
    expect(await within(dialog()).findByText(/Fix the invalid plot numbers/)).toBeInTheDocument();
    expect(within(dialog()).getByText("Invalid")).toBeInTheDocument();
    expect(within(dialog()).getByRole("button", { name: "Register 1 plot" })).toBeDisabled();
  });

  it("will not register past the cap, and says why", async () => {
    await openBulk({ "POST /api/inventory/plots/bulk-preview/": json(makePreview(["A-6"], { cap: true })) });
    await enterLabels("A-6");
    await preview();
    expect(await within(dialog()).findByText("An estate can hold at most 3000 registered plots. This one has 4.")).toBeInTheDocument();
    expect(within(dialog()).getByRole("button", { name: "Register 1 plot" })).toBeDisabled();
  });

  it("flags plot numbers that may be the same plot written another way, without blocking", async () => {
    await openBulk({
      "POST /api/inventory/plots/bulk-preview/": json(makePreview(["05"], { warnings: { "05": ["Looks like the existing plot '5', apart from leading zeros."] } })),
    });
    await enterLabels("05");
    await preview();
    expect(await within(dialog()).findByText("1 plot number may be the same plot written another way. Check the notes below.")).toBeInTheDocument();
    expect(within(dialog()).getByText(/Looks like the existing plot '5'/)).toBeInTheDocument();
    expect(within(dialog()).getByRole("button", { name: "Register 1 plot" })).toBeEnabled();
  });

  it("replaces a stale preview with the fresh one and registers nothing until it is confirmed again", async () => {
    let commits = 0;
    const api = await openBulk({
      "POST /api/inventory/plots/bulk-preview/": json(makePreview(["A-4", "A-5"])),
      "POST /api/inventory/plots/bulk-commit/": () => {
        commits += 1;
        return commits === 1
          ? json({ detail: "The estate has changed since this preview.", preview: makePreview(["A-4", "A-5"], { outcomes: { "A-4": "exists" }, fingerprint: "f2" }) }, 409)
          : json({ created: 1, plots: [], skipped_existing: ["A-4"], skipped_repeated: [] }, 201);
      },
    });
    await enterLabels("A-4{Enter}A-5");
    await preview();
    await userEvent.click(await within(dialog()).findByRole("button", { name: "Register 2 plots" }));
    expect(await within(dialog()).findByText(/The estate changed since you previewed this, so nothing was registered/)).toBeInTheDocument();
    expect(within(dialog()).getByText("Already registered")).toBeInTheDocument();                  // the fresh preview
    expect(screen.queryByText(/plots? registered\./)).not.toBeInTheDocument();
    await userEvent.click(within(dialog()).getByRole("button", { name: "Register 1 plot" }));
    await waitFor(() => expect(calls(api, "POST", "/api/inventory/plots/bulk-commit/")).toHaveLength(2));
    expect(body(calls(api, "POST", "/api/inventory/plots/bulk-commit/")[1])).toMatchObject({
      input_fingerprint: "in-f2", preview_fingerprint: "pv-f2", entries: [{ labels: "A-4\nA-5" }],
    });
    expect(await screen.findByText("1 plot registered. 1 already registered (skipped).")).toBeInTheDocument();
  });

  it("goes back to edit with the entries kept, and needs a new preview", async () => {
    const api = await openBulk({ "POST /api/inventory/plots/bulk-preview/": json(makePreview(["A-4"])) });
    await enterLabels("A-4");
    await preview();
    await userEvent.click(await within(dialog()).findByRole("button", { name: "Back and edit" }));
    expect(within(dialog()).getByLabelText("One plot number per line")).toHaveValue("A-4");
    expect(within(dialog()).queryByRole("button", { name: /^Register \d/ })).not.toBeInTheDocument();
    expect(calls(api, "POST", "/api/inventory/plots/bulk-commit/")).toHaveLength(0);
  });

  it("shows the server's message when a preview is refused", async () => {
    await openBulk({ "POST /api/inventory/plots/bulk-preview/": json({ detail: "A request can register at most 500 labels." }, 400) });
    await enterLabels("A-1");
    await preview();
    expect(await within(dialog()).findByText("A request can register at most 500 labels.")).toBeInTheDocument();
    expect(within(dialog()).getByRole("heading", { name: "Register many plots" })).toBeInTheDocument();          // still on the input step
  });
});

describe("adding a sale from a plot", () => {
  it("opens the sale form with the estate and plot filled in, keeps them editable, and refreshes the board", async () => {
    const api = await openEstate("md", { "POST /api/sales-desk/": json(saleDetail, 201) });
    await pick("A-1");
    await userEvent.click(await screen.findByRole("button", { name: "Add sale for this plot" }));
    expect(within(dialog()).getByRole("heading", { name: "Add a sale" })).toBeInTheDocument();
    expect(within(dialog()).getByLabelText("Estate")).toHaveValue("5");
    expect(within(dialog()).getByLabelText("Registered available plot")).toHaveValue("A-1");
    await userEvent.type(within(dialog()).getByLabelText("Buyer name"), "Ada Obi");
    await userEvent.type(within(dialog()).getByLabelText("Price"), "1500000");
    await userEvent.click(within(dialog()).getByRole("button", { name: "Add sale" }));
    await waitFor(() => expect(calls(api, "POST", "/api/sales-desk/")).toHaveLength(1));
    expect(body(calls(api, "POST", "/api/sales-desk/")[0])).toEqual({
      plot_number: "A-1", price: "1500000", sale_date: todayIso(), estate: 5, buyer_name: "Ada Obi",
    });
    expect(await screen.findByText("Sale added.")).toBeInTheDocument();
    expect(screen.getByRole("heading", { name: estate.name })).toBeInTheDocument();             // stays on the estate page
    await waitFor(() => expect(calls(api, "GET", "/api/plot-board/").length).toBeGreaterThan(1));
  });

  it("lets the person choose another registered available plot", async () => {
    const api = await openEstate("md", { "POST /api/sales-desk/": json(saleDetail, 201) });
    await pick("A-1");
    await userEvent.click(await screen.findByRole("button", { name: "Add sale for this plot" }));
    await userEvent.selectOptions(
      within(dialog()).getByLabelText("Registered available plot"),
      "A-3",
    );
    await userEvent.type(within(dialog()).getByLabelText("Buyer name"), "Ada Obi");
    await userEvent.type(within(dialog()).getByLabelText("Price"), "1500000");
    await userEvent.click(within(dialog()).getByRole("button", { name: "Add sale" }));
    await waitFor(() => expect(calls(api, "POST", "/api/sales-desk/")).toHaveLength(1));
    expect(body(calls(api, "POST", "/api/sales-desk/")[0]).plot_number).toBe("A-3");
  });
});

describe("freshness: the board and the panel are asked for again every time", () => {
  it("re-reads the board when the estate page is opened again, despite the 30 second default", async () => {
    const api = mockApi(routes());
    renderAppLikeMain(`/estates/${estate.id}`);
    await screen.findByRole("heading", { name: estate.name });
    expect(calls(api, "GET", "/api/plot-board/")).toHaveLength(1);
    await userEvent.click(screen.getByRole("link", { name: "← All estates" }));
    await userEvent.click(await screen.findByRole("link", { name: "Green Valley Estate" }));
    await screen.findByRole("heading", { name: estate.name });
    await waitFor(() => expect(calls(api, "GET", "/api/plot-board/")).toHaveLength(2));
  });

  it("re-reads the sale each time the panel is opened", async () => {
    const api = mockApi(routes());
    renderAppLikeMain(`/estates/${estate.id}`);
    await screen.findByRole("heading", { name: estate.name });
    const saleCalls = () => calls(api, "GET", `/api/sales-desk/${saleDetail.id}/`).length;
    await pick("A-2");
    await screen.findByText("Bayo Lawal");
    expect(saleCalls()).toBe(1);
    await userEvent.click(screen.getByRole("button", { name: "Close plot details" }));
    await pick("A-2");
    await screen.findByText("Bayo Lawal");
    await waitFor(() => expect(saleCalls()).toBe(2));
  });
});
