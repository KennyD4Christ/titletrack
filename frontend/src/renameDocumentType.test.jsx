import { beforeEach, describe, expect, it } from "vitest";
import { fireEvent, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { baseRoutes, json, mockApi, renderApp } from "./test/helpers.jsx";

import documentTypes from "./test/fixtures/document_types.json";
import departments from "./test/fixtures/departments.json";

const PATH = "/settings/document-catalogue";
const LIST_URL = "/api/document-types/?page=1&page_size=200";
const RENAME_SITE = "POST /api/document-types/41/rename/";
const INTRO =
  "The new name is used from now on. Obligations already created keep their old name, and documents already on sales keep the name they had when added.";

const site = documentTypes.results.find((d) => d.name === "Site Survey Plan");
const renamedSite = { ...site, name: "Land Survey" };
const listWith = (row) => ({ ...documentTypes, results: documentTypes.results.map((d) => (d.id === row.id ? row : d)) });

const body = (call) => JSON.parse(call.init.body);
const posts = (api) => api.calls.filter((c) => c.method === "POST");
const setValue = (field, value) => fireEvent.change(field, { target: { value } });
const listGets = (api) => api.urls().filter((u) => u === LIST_URL).length;

const routes = (extra = {}) =>
  baseRoutes("manager", {
    "GET /api/document-types/": documentTypes,
    "GET /api/departments/": departments,
    ...extra,
  });

const openPage = async (extra) => {
  const api = mockApi(routes(extra));
  renderApp(PATH);
  await screen.findByRole("table");
  return api;
};

const openRename = async (name = "Site Survey Plan") => {
  await userEvent.click(within(screen.getByRole("table")).getByRole("button", { name: `Rename ${name}` }));
  return screen.findByRole("dialog", { name: `Rename ${name}` });
};

/** Taken once, before any error appears: a message inside a label changes that label's text. */
const fieldsOf = (dialog) => ({
  name: within(dialog).getByLabelText("New name"),
  submit: within(dialog).getByRole("button", { name: "Rename document type" }),
});

describe("rename a document type", () => {
  beforeEach(() => {
    document.cookie = "csrftoken=abc; path=/";
  });

  it("opens with the current name, says that history is kept, and has Rename switched off", async () => {
    await openPage();
    const dialog = await openRename();
    const { name, submit } = fieldsOf(dialog);
    expect(within(dialog).getByText(INTRO)).toBeInTheDocument();
    expect(name).toHaveValue("Site Survey Plan");
    expect(submit).toBeDisabled();
  });

  it("allows a change of capital letters, and not a change of spacing or an empty name", async () => {
    await openPage();
    const dialog = await openRename();
    const { name, submit } = fieldsOf(dialog);
    setValue(name, "Site survey plan");
    expect(submit).toBeEnabled();
    setValue(name, "  Site   Survey Plan ");
    expect(submit).toBeDisabled();
    setValue(name, "");
    expect(submit).toBeDisabled();
    setValue(name, "   ");
    expect(submit).toBeDisabled();
  });

  it("sends only the trimmed name to the rename endpoint, and nothing that could touch history", async () => {
    const api = await openPage({ [RENAME_SITE]: () => json(renamedSite) });
    const dialog = await openRename();
    const { name, submit } = fieldsOf(dialog);
    setValue(name, "  Land Survey  ");
    await userEvent.click(submit);
    await screen.findByText(/renamed to/);
    const [call] = posts(api);
    expect(call.url).toBe("/api/document-types/41/rename/");
    expect(body(call)).toEqual({ name: "Land Survey" });
    expect(Object.keys(body(call))).toEqual(["name"]);
  });

  it("says what happened using the name from before, and reloads the catalogue so the row shows the new name", async () => {
    let list = documentTypes;
    const api = await openPage({
      "GET /api/document-types/": () => list,
      [RENAME_SITE]: () => {
        list = listWith(renamedSite);
        return json(renamedSite);
      },
    });
    const dialog = await openRename();
    const before = listGets(api);
    const { name, submit } = fieldsOf(dialog);
    setValue(name, "Land Survey");
    await userEvent.click(submit);
    expect(await screen.findByText("Site Survey Plan renamed to Land Survey.")).toBeInTheDocument();
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
    await waitFor(() => expect(listGets(api)).toBe(before + 1));
    await waitFor(() => expect(within(screen.getByRole("table")).getByText("Land Survey")).toBeInTheDocument());
    expect(within(screen.getByRole("table")).queryByText("Site Survey Plan")).not.toBeInTheDocument();
    expect(screen.getByText("Site Survey Plan renamed to Land Survey.")).toBeInTheDocument();      // still the old name
  });

  it("shows a name problem beside the name, and keeps the dialog open", async () => {
    const message = "A document type with this name already exists.";
    await openPage({ [RENAME_SITE]: () => json({ name: [message] }, 400) });
    const dialog = await openRename();
    const { name, submit } = fieldsOf(dialog);
    setValue(name, "Deed of Assignment");
    await userEvent.click(submit);
    const alert = await within(dialog).findByRole("alert");
    expect(alert).toHaveTextContent(message);
    expect(alert.closest("label")).toContainElement(name);
    expect(within(dialog).getAllByRole("alert")).toHaveLength(1);        // beside the field only, not repeated above
    expect(screen.getByRole("dialog")).toBeInTheDocument();
    expect(submit).toBeEnabled();
  });

  it("shows a 409 in the form-level box and reloads the catalogue in case it changed", async () => {
    const message = "This document type has no current work type, so it cannot be renamed.";
    const api = await openPage({ [RENAME_SITE]: () => json({ detail: message }, 409) });
    const dialog = await openRename();
    const before = listGets(api);
    const { name, submit } = fieldsOf(dialog);
    setValue(name, "Land Survey");
    await userEvent.click(submit);
    const alert = await within(dialog).findByRole("alert");
    expect(alert).toHaveTextContent(message);
    expect(alert.closest("label")).toBeNull();
    await waitFor(() => expect(listGets(api)).toBe(before + 1));
    expect(screen.getByRole("dialog")).toBeInTheDocument();
  });

  it("closes without sending anything on Cancel or Escape", async () => {
    const api = await openPage();
    const first = await openRename();
    await userEvent.click(within(first).getByRole("button", { name: "Cancel" }));
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
    await openRename();
    await userEvent.keyboard("{Escape}");
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
    expect(posts(api)).toHaveLength(0);
  });

  it("sends one request however many times Rename is pressed while it is working", async () => {
    let release;
    const gate = new Promise((resolve) => { release = resolve; });
    const api = await openPage({ [RENAME_SITE]: async () => { await gate; return json(renamedSite); } });
    const dialog = await openRename();
    const { name, submit } = fieldsOf(dialog);
    setValue(name, "Land Survey");
    await userEvent.click(submit);
    await waitFor(() => expect(submit).toBeDisabled());
    expect(submit).toHaveTextContent("Renaming…");
    await userEvent.click(submit);
    release();
    expect(await screen.findByText("Site Survey Plan renamed to Land Survey.")).toBeInTheDocument();
    expect(posts(api)).toHaveLength(1);
  });
});
