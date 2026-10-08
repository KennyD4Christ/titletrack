import { describe, expect, it } from "vitest";
import { fireEvent, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { baseRoutes, fixtures, json, mockApi, renderApp } from "./test/helpers.jsx";

const d = fixtures.obligationDetail;
const ngozi = fixtures.users.results.find((u) => u.full_name === "Ngozi Eze");
const body = (call) => JSON.parse(call.init.body);
const call = (api, method, pathEnd) => api.calls.filter((c) => c.method === method && c.url.split("?")[0].endsWith(pathEnd));

const detailRoutes = (extra = {}, detail = d, role = "manager") =>
  baseRoutes(role, {
    [`GET /api/obligations/${d.id}/`]: detail,
    [`GET /api/obligations/${d.id}/events/`]: fixtures.obligationEvents,
    ...extra,
  });

describe("update form on the detail page", () => {
  it("sends only what changed, plus the note, and refreshes the history", async () => {
    document.cookie = "csrftoken=abc; path=/";
    const api = mockApi(detailRoutes({ [`PATCH /api/obligations/${d.id}/`]: { ...d, next_action: "Call the registrar" } }));
    renderApp(`/obligations/${d.id}`);
    const save = await screen.findByRole("button", { name: "Save changes" });
    expect(save).toBeDisabled();                                            // nothing changed yet
    const field = screen.getByLabelText("Next action");
    await userEvent.clear(field);
    await userEvent.type(field, "Call the registrar");
    await userEvent.type(screen.getByLabelText(/Note for the history/), "Spoke to the desk officer");
    expect(save).toBeEnabled();
    await userEvent.click(save);
    expect(await screen.findByText(/Saved\. The history below shows the change\./)).toBeInTheDocument();
    const [patch] = call(api, "PATCH", `/api/obligations/${d.id}/`);
    expect(body(patch)).toEqual({ next_action: "Call the registrar", note: "Spoke to the desk officer" });
    expect(patch.init.headers["X-CSRFToken"]).toBe("abc");
    await waitFor(() => expect(call(api, "GET", "/events/").length).toBeGreaterThanOrEqual(2));   // history reloaded
  });

  it("sends the owner as a number, and null for Unassigned", async () => {
    const owned = { ...d, owner: ngozi.id, owner_name: ngozi.full_name };
    const api = mockApi(detailRoutes({ [`PATCH /api/obligations/${d.id}/`]: owned }, owned));
    renderApp(`/obligations/${d.id}`);
    const owner = await screen.findByLabelText("Owner");
    expect(owner).toHaveValue(String(ngozi.id));
    await userEvent.selectOptions(owner, "Unassigned");
    await userEvent.click(screen.getByRole("button", { name: "Save changes" }));
    await waitFor(() => expect(call(api, "PATCH", `/api/obligations/${d.id}/`)).toHaveLength(1));
    expect(body(call(api, "PATCH", `/api/obligations/${d.id}/`)[0])).toEqual({ owner: null });
    const other = fixtures.users.results.find((u) => u.id !== ngozi.id);
    await userEvent.selectOptions(await screen.findByLabelText("Owner"), other.full_name);
    await userEvent.click(screen.getByRole("button", { name: "Save changes" }));
    await waitFor(() => expect(call(api, "PATCH", `/api/obligations/${d.id}/`)).toHaveLength(2));
    expect(body(call(api, "PATCH", `/api/obligations/${d.id}/`)[1]).owner).toBe(other.id);
  });

  it("changes the date and status", async () => {
    const api = mockApi(detailRoutes({ [`PATCH /api/obligations/${d.id}/`]: d }));
    renderApp(`/obligations/${d.id}`);
    await userEvent.selectOptions(await screen.findByLabelText("Status"), "In progress");
    fireEvent.change(screen.getByLabelText("Next review date"), { target: { value: "2026-11-20" } });
    await userEvent.click(screen.getByRole("button", { name: "Save changes" }));
    await waitFor(() => expect(call(api, "PATCH", `/api/obligations/${d.id}/`)).toHaveLength(1));
    expect(body(call(api, "PATCH", `/api/obligations/${d.id}/`)[0])).toEqual(
      d.status === "in_progress" ? { next_review_date: "2026-11-20" } : { status: "in_progress", next_review_date: "2026-11-20" });
  });

  it("shows the server's message under the field it concerns", async () => {
    mockApi(detailRoutes({ [`PATCH /api/obligations/${d.id}/`]: () => json({ next_review_date: ["Date has wrong format."] }, 400) }));
    renderApp(`/obligations/${d.id}`);
    fireEvent.change(await screen.findByLabelText("Next review date"), { target: { value: "2026-11-20" } });
    await userEvent.click(screen.getByRole("button", { name: "Save changes" }));
    expect(await screen.findByText("Date has wrong format.")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Save changes" })).toBeEnabled();      // the person can fix it and retry
  });

  it("explains a refused save in plain words", async () => {
    mockApi(detailRoutes({ [`PATCH /api/obligations/${d.id}/`]: () => json({ detail: "This sale is cancelled, so its obligations are read-only." }, 409) }));
    renderApp(`/obligations/${d.id}`);
    await userEvent.type(await screen.findByLabelText(/Note for the history/), "x");
    await userEvent.click(screen.getByRole("button", { name: "Save changes" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("This sale is cancelled, so its obligations are read-only.");
  });

  it("warns, without blocking, when an item would stay on the attention list", async () => {
    mockApi(detailRoutes({}, { ...d, owner: null, owner_name: null, next_action: "" }));
    renderApp(`/obligations/${d.id}`);
    expect(await screen.findByText(/Without an owner and a next action/)).toBeInTheDocument();
  });

  it("hides the form when the person may not edit", async () => {
    mockApi(detailRoutes({}, { ...d, can_edit: false }, "viewer"));
    renderApp(`/obligations/${d.id}`);
    await screen.findByRole("heading", { name: /Why is this overdue|Where this stands/ });
    expect(screen.queryByRole("heading", { name: "Update this item" })).not.toBeInTheDocument();
    expect(screen.getByText("You can view this item but not change it.")).toBeInTheDocument();
  });

  it("shows no form and no 'not allowed' message for a cancelled sale (the banner explains)", async () => {
    mockApi(detailRoutes({}, { ...d, can_edit: false, is_cancelled: true, is_open: false, sale_status: "cancelled", days_overdue: 0,
      timeline: { ...d.timeline, delay_days: 0 }, sale_summary: { ...d.sale_summary, status: "cancelled", cancel_reason: "Buyer defaulted" } }));
    renderApp(`/obligations/${d.id}`);
    expect(await screen.findByText(/This sale was cancelled: Buyer defaulted/)).toBeInTheDocument();
    expect(screen.queryByRole("heading", { name: "Update this item" })).not.toBeInTheDocument();
    expect(screen.queryByText("You can view this item but not change it.")).not.toBeInTheDocument();
  });
});

describe("quick update from My Queue", () => {
  const row = fixtures.queue.overdue[0];
  const open = async (api) => {
    renderApp("/queue");
    await userEvent.click(await screen.findByRole("button", { name: `Update ${row.buyer_name}, ${row.type_name}` }));
    return screen.findByRole("dialog");
  };

  it("opens a compact form, saves one change, closes and says so", async () => {
    document.cookie = "csrftoken=abc; path=/";
    const api = mockApi(baseRoutes("staff", { [`PATCH /api/obligations/${row.id}/`]: { ...row } }));
    const dialog = await open(api);
    expect(within(dialog).queryByLabelText(/Reason for any delay/)).not.toBeInTheDocument();    // compact: no long fields
    const next = within(dialog).getByLabelText("Next action");
    await userEvent.clear(next);
    await userEvent.type(next, "Chase the solicitor");
    await userEvent.click(within(dialog).getByRole("button", { name: "Save changes" }));
    await waitFor(() => expect(screen.queryByRole("dialog")).not.toBeInTheDocument());
    expect(body(call(api, "PATCH", `/api/obligations/${row.id}/`)[0])).toEqual({ next_action: "Chase the solicitor" });
    expect(await screen.findByText("Saved.")).toBeInTheDocument();
  });

  it("closes with Escape or Cancel and saves nothing", async () => {
    const api = mockApi(baseRoutes("staff"));
    await open(api);
    await userEvent.keyboard("{Escape}");
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: `Update ${row.buyer_name}, ${row.type_name}` }));
    await userEvent.click(within(await screen.findByRole("dialog")).getByRole("button", { name: "Cancel" }));
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
    expect(call(api, "PATCH", "/")).toHaveLength(0);
  });
});

describe("bulk update from the list", () => {
  const rows = fixtures.obligationsList.results;
  const label = (r) => `Select ${r.buyer_name}, ${r.type_name}`;

  it("selects rows, shows what will change, and sends one request", async () => {
    document.cookie = "csrftoken=abc; path=/";
    const api = mockApi(baseRoutes("manager", { "POST /api/obligations/bulk-update/": { updated: 2, ids: [rows[0].id, rows[1].id] } }));
    renderApp("/obligations");
    await userEvent.click(await screen.findByRole("checkbox", { name: label(rows[0]) }));
    await userEvent.click(screen.getByRole("checkbox", { name: label(rows[1]) }));
    expect(screen.getByText("2 selected")).toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: "Update selected" }));
    const dialog = await screen.findByRole("dialog", { name: "Update 2 obligations" });
    const apply = within(dialog).getByRole("button", { name: "Apply to 2 obligations" });
    expect(apply).toBeDisabled();                                                       // nothing chosen yet
    await userEvent.selectOptions(within(dialog).getByLabelText("Owner"), ngozi.full_name);
    await userEvent.type(within(dialog).getByLabelText("Next action"), "Pick up and review");
    await userEvent.type(within(dialog).getByLabelText(/Note for the history/), "Reallocated after Monday review");
    await userEvent.click(apply);
    expect(await screen.findByText("Updated 2 obligations.")).toBeInTheDocument();
    expect(body(call(api, "POST", "/bulk-update/")[0])).toEqual({
      ids: [rows[0].id, rows[1].id], owner: ngozi.id, next_action: "Pick up and review", note: "Reallocated after Monday review",
    });
    expect(screen.queryByText(/selected$/)).not.toBeInTheDocument();                    // selection cleared
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
  });

  it("can clear the owner for many at once", async () => {
    const api = mockApi(baseRoutes("manager", { "POST /api/obligations/bulk-update/": { updated: 1, ids: [rows[0].id] } }));
    renderApp("/obligations");
    await userEvent.click(await screen.findByRole("checkbox", { name: label(rows[0]) }));
    await userEvent.click(screen.getByRole("button", { name: "Update selected" }));
    const dialog = await screen.findByRole("dialog");
    await userEvent.selectOptions(within(dialog).getByLabelText("Owner"), "Unassigned");
    await userEvent.click(within(dialog).getByRole("button", { name: "Apply to 1 obligation" }));
    await waitFor(() => expect(call(api, "POST", "/bulk-update/")).toHaveLength(1));
    expect(body(call(api, "POST", "/bulk-update/")[0])).toEqual({ ids: [rows[0].id], owner: null });
  });

  it("selects everything on the page", async () => {
    mockApi(baseRoutes("manager"));
    renderApp("/obligations");
    await userEvent.click(await screen.findByRole("checkbox", { name: "Select all on this page" }));
    expect(screen.getByText(`${rows.length} selected`)).toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: "Clear selection" }));
    expect(screen.queryByText(/ selected$/)).not.toBeInTheDocument();
  });

  it("names the items that stopped the batch and keeps the dialog open", async () => {
    mockApi(baseRoutes("manager", { "POST /api/obligations/bulk-update/": () => json({
      detail: "Some of the selected obligations cannot be changed, so nothing was changed.",
      problems: [{ id: rows[1].id, error: "The sale is cancelled." }],
    }, 409) }));
    renderApp("/obligations");
    await userEvent.click(await screen.findByRole("checkbox", { name: label(rows[0]) }));
    await userEvent.click(screen.getByRole("checkbox", { name: label(rows[1]) }));
    await userEvent.click(screen.getByRole("button", { name: "Update selected" }));
    const dialog = await screen.findByRole("dialog");
    await userEvent.type(within(dialog).getByLabelText("Next action"), "x");
    await userEvent.click(within(dialog).getByRole("button", { name: /Apply to/ }));
    const alert = await within(dialog).findByRole("alert");
    expect(alert).toHaveTextContent("nothing was changed");
    expect(alert).toHaveTextContent(`${rows[1].buyer_name}, ${rows[1].type_name}: The sale is cancelled.`);
    expect(screen.getByText("2 selected")).toBeInTheDocument();                         // selection kept so they can adjust it
  });

  it("gives staff Update buttons only on rows they may edit, and no bulk checkboxes", async () => {
    const mixed = rows.map((r, i) => ({ ...r, can_edit: i === 0 }));
    mockApi(baseRoutes("staff", { "GET /api/obligations/": { ...fixtures.obligationsList, results: mixed } }));
    renderApp("/obligations");
    await screen.findByRole("link", { name: rows[0].buyer_name });
    expect(screen.queryByRole("checkbox", { name: "Select all on this page" })).not.toBeInTheDocument();
    expect(screen.getAllByRole("button", { name: /^Update / })).toHaveLength(1);
    expect(screen.getByRole("button", { name: `Update ${rows[0].buyer_name}, ${rows[0].type_name}` })).toBeInTheDocument();
  });
});
