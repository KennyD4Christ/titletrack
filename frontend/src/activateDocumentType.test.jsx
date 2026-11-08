import { beforeEach, describe, expect, it } from "vitest";
import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { baseRoutes, json, mockApi, renderApp } from "./test/helpers.jsx";

import documentTypes from "./test/fixtures/document_types.json";
import departments from "./test/fixtures/departments.json";

const PATH = "/settings/document-catalogue";
const LIST_URL = "/api/document-types/?page=1&page_size=200";
const DEACTIVATE_SITE = "POST /api/document-types/41/deactivate/";
const ACTIVATE_OLD = "POST /api/document-types/51/activate/";

const DEACTIVATE_MESSAGE =
  "Site Survey Plan will no longer be offered when adding documents to sales. Documents already on sales, existing obligations, and the automatic generation of documents on existing sales are not affected. You can activate it again at any time.";
const ACTIVATE_MESSAGE = "Old Transfer Form will be offered again when adding documents to sales.";

// Site Survey Plan (41) is active; Old Transfer Form (51) is the one inactive entry in the fixture.
const site = documentTypes.results.find((d) => d.name === "Site Survey Plan");
const old = documentTypes.results.find((d) => d.name === "Old Transfer Form");
const listWith = (row) => ({ ...documentTypes, results: documentTypes.results.map((d) => (d.id === row.id ? row : d)) });

const body = (call) => JSON.parse(call.init.body);
const posts = (api) => api.calls.filter((c) => c.method === "POST");
const listGets = (api) => api.urls().filter((u) => u === LIST_URL).length;

const routes = (extra = {}) =>
  baseRoutes("manager", {
    "GET /api/document-types/": documentTypes,
    "GET /api/departments/": departments,
    ...extra,
  });

const openPage = async (extra, path = PATH) => {
  const api = mockApi(routes(extra));
  renderApp(path);
  await screen.findByRole("table");
  return api;
};

const rowOf = (name) => within(screen.getByRole("table")).getByText(name).closest("tr");

const openDialog = async (buttonName, title) => {
  await userEvent.click(within(screen.getByRole("table")).getByRole("button", { name: buttonName }));
  return screen.findByRole("dialog", { name: title });
};
const openDeactivate = () => openDialog("Deactivate Site Survey Plan", "Deactivate Site Survey Plan");
const openActivate = () => openDialog("Activate Old Transfer Form", "Activate Old Transfer Form");

describe("activate and deactivate a document type", () => {
  beforeEach(() => {
    document.cookie = "csrftoken=abc; path=/";
  });

  it("offers Deactivate on active rows and Activate on inactive ones", async () => {
    await openPage();
    const siteRow = rowOf("Site Survey Plan");
    expect(within(siteRow).getByRole("button", { name: "Deactivate Site Survey Plan" })).toBeInTheDocument();
    expect(within(siteRow).queryByRole("button", { name: "Activate Site Survey Plan" })).not.toBeInTheDocument();
    const oldRow = rowOf("Old Transfer Form");
    expect(within(oldRow).getByRole("button", { name: "Activate Old Transfer Form" })).toBeInTheDocument();
    expect(within(oldRow).queryByRole("button", { name: "Deactivate Old Transfer Form" })).not.toBeInTheDocument();
    const table = screen.getByRole("table");
    expect(within(table).getAllByRole("button", { name: /^Deactivate / })).toHaveLength(10);
    expect(within(table).getAllByRole("button", { name: /^Activate / })).toHaveLength(1);
  });

  it("asks before deactivating, and sends nothing until it is confirmed", async () => {
    const api = await openPage();
    const dialog = await openDeactivate();
    expect(within(dialog).getByText(DEACTIVATE_MESSAGE)).toBeInTheDocument();
    expect(within(dialog).getByRole("button", { name: "Deactivate document type" })).toBeEnabled();
    expect(posts(api)).toHaveLength(0);
  });

  it("asks before activating, and sends nothing until it is confirmed", async () => {
    const api = await openPage();
    const dialog = await openActivate();
    expect(within(dialog).getByText(ACTIVATE_MESSAGE)).toBeInTheDocument();
    expect(within(dialog).getByRole("button", { name: "Activate document type" })).toBeEnabled();
    expect(posts(api)).toHaveLength(0);
  });

  it("closes without sending anything on Not now or Escape", async () => {
    const api = await openPage();
    const first = await openDeactivate();
    await userEvent.click(within(first).getByRole("button", { name: "Not now" }));
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
    await openActivate();
    await userEvent.keyboard("{Escape}");
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
    expect(posts(api)).toHaveLength(0);
  });

  it("sends an empty request to the deactivate address of that entry", async () => {
    const api = await openPage({ [DEACTIVATE_SITE]: () => json({ ...site, is_active: false }) });
    const dialog = await openDeactivate();
    await userEvent.click(within(dialog).getByRole("button", { name: "Deactivate document type" }));
    await screen.findByText("Site Survey Plan deactivated.");
    const [call] = posts(api);
    expect(call.url).toBe("/api/document-types/41/deactivate/");
    expect(body(call)).toEqual({});
  });

  it("deactivates: closes, says what happened, reloads the catalogue, and the row shows Inactive with an Activate button", async () => {
    let list = documentTypes;
    const deactivated = { ...site, is_active: false };
    const api = await openPage({
      "GET /api/document-types/": () => list,
      [DEACTIVATE_SITE]: () => {
        list = listWith(deactivated);
        return json(deactivated);
      },
    });
    const dialog = await openDeactivate();
    const before = listGets(api);
    await userEvent.click(within(dialog).getByRole("button", { name: "Deactivate document type" }));
    expect(await screen.findByText("Site Survey Plan deactivated.")).toBeInTheDocument();
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
    await waitFor(() => expect(listGets(api)).toBe(before + 1));
    await waitFor(() => {
      const row = rowOf("Site Survey Plan");
      expect(row).toHaveClass("row-cancelled");
      expect(within(row).getByText("Inactive")).toBeInTheDocument();
      expect(within(row).getByRole("button", { name: "Activate Site Survey Plan" })).toBeInTheDocument();
      expect(within(row).queryByRole("button", { name: "Deactivate Site Survey Plan" })).not.toBeInTheDocument();
    });
  });

  it("activates: closes, says what happened, reloads the catalogue, and the row shows Active with a Deactivate button", async () => {
    let list = documentTypes;
    const activated = { ...old, is_active: true };
    const api = await openPage({
      "GET /api/document-types/": () => list,
      [ACTIVATE_OLD]: () => {
        list = listWith(activated);
        return json(activated);
      },
    });
    const dialog = await openActivate();
    const before = listGets(api);
    await userEvent.click(within(dialog).getByRole("button", { name: "Activate document type" }));
    expect(await screen.findByText("Old Transfer Form activated.")).toBeInTheDocument();
    expect(posts(api)[0].url).toBe("/api/document-types/51/activate/");
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
    await waitFor(() => expect(listGets(api)).toBe(before + 1));
    await waitFor(() => {
      const row = rowOf("Old Transfer Form");
      expect(row).not.toHaveClass("row-cancelled");
      expect(within(row).getByText("Active")).toBeInTheDocument();
      expect(within(row).getByRole("button", { name: "Deactivate Old Transfer Form" })).toBeInTheDocument();
      expect(within(row).queryByRole("button", { name: "Activate Old Transfer Form" })).not.toBeInTheDocument();
    });
  });

  it("shows a 409 in the dialog and reloads the catalogue, so the row's button corrects itself", async () => {
    let list = documentTypes;
    const api = await openPage({
      "GET /api/document-types/": () => list,
      [DEACTIVATE_SITE]: () => {
        list = listWith({ ...site, is_active: false });                      // someone else got there first
        return json({ detail: "This document type is already inactive." }, 409);
      },
    });
    const dialog = await openDeactivate();
    const before = listGets(api);
    await userEvent.click(within(dialog).getByRole("button", { name: "Deactivate document type" }));
    expect(await within(dialog).findByRole("alert")).toHaveTextContent("This document type is already inactive.");
    await waitFor(() => expect(listGets(api)).toBe(before + 1));
    await waitFor(() => expect(within(screen.getByRole("table")).getByRole("button", { name: "Activate Site Survey Plan" })).toBeInTheDocument());
    expect(screen.getByRole("dialog")).toBeInTheDocument();
    expect(within(dialog).getByRole("button", { name: "Deactivate document type" })).toBeEnabled();
  });

  it("shows any other refusal in the dialog and keeps it open", async () => {
    await openPage({ [DEACTIVATE_SITE]: () => json({ detail: "You do not have permission to perform this action." }, 403) });
    const dialog = await openDeactivate();
    await userEvent.click(within(dialog).getByRole("button", { name: "Deactivate document type" }));
    expect(await within(dialog).findByRole("alert")).toHaveTextContent("You do not have permission to perform this action.");
    expect(screen.getByRole("dialog")).toBeInTheDocument();
    expect(screen.queryByText("Site Survey Plan deactivated.")).not.toBeInTheDocument();
  });

  it("sends one request however many times it is pressed while it is working", async () => {
    let release;
    const gate = new Promise((resolve) => { release = resolve; });
    const api = await openPage({ [DEACTIVATE_SITE]: async () => { await gate; return json({ ...site, is_active: false }); } });
    const dialog = await openDeactivate();
    const confirm = within(dialog).getByRole("button", { name: "Deactivate document type" });
    await userEvent.click(confirm);
    await waitFor(() => expect(confirm).toBeDisabled());
    expect(confirm).toHaveTextContent("Working…");
    await userEvent.click(confirm);
    release();
    expect(await screen.findByText("Site Survey Plan deactivated.")).toBeInTheDocument();
    expect(posts(api)).toHaveLength(1);
  });

  it("under the Active filter, a deactivated row leaves the list while the notice stays", async () => {
    let list = documentTypes;
    const deactivated = { ...site, is_active: false };
    await openPage({
      "GET /api/document-types/": () => list,
      [DEACTIVATE_SITE]: () => {
        list = listWith(deactivated);
        return json(deactivated);
      },
    }, `${PATH}?status=active`);
    const dialog = await openDeactivate();
    await userEvent.click(within(dialog).getByRole("button", { name: "Deactivate document type" }));
    expect(await screen.findByText("Site Survey Plan deactivated.")).toBeInTheDocument();
    await waitFor(() => expect(within(screen.getByRole("table")).queryByText("Site Survey Plan")).not.toBeInTheDocument());
    expect(screen.getByText("Site Survey Plan deactivated.")).toBeInTheDocument();
  });
});
