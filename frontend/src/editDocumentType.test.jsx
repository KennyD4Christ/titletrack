import { beforeEach, describe, expect, it } from "vitest";
import { fireEvent, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { baseRoutes, json, mockApi, renderApp } from "./test/helpers.jsx";

import documentTypes from "./test/fixtures/document_types.json";
import departments from "./test/fixtures/departments.json";

const PATH = "/settings/document-catalogue";
const LIST_URL = "/api/document-types/?page=1&page_size=200";
const PATCH_SITE = "PATCH /api/document-types/41/";

const INTRO = "Use this to change the details of this document type. To change its name, use Rename.";
const UNCHANGED = "Change at least one detail to save.";
const WORK_NOTE =
  "Changing the preparation time or default department affects work created from now on. Existing obligations keep their due dates.";
const LEGACY_NOTE =
  "This document type also drives automatic generation on existing sales, so the change applies to those obligations too.";
const NO_WORK_TYPE =
  "This document type has no current work type, so its preparation time and default department cannot be changed.";

// Site Survey Plan (41): category Survey, 45 days, department Legal (7), sort order 10, no description, not automatic.
const site = documentTypes.results.find((d) => d.name === "Site Survey Plan");
const listWith = (row) => ({ ...documentTypes, results: documentTypes.results.map((d) => (d.id === row.id ? row : d)) });

const body = (call) => JSON.parse(call.init.body);
const patches = (api) => api.calls.filter((c) => c.method === "PATCH");
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

const openEdit = async (name = "Site Survey Plan") => {
  await userEvent.click(within(screen.getByRole("table")).getByRole("button", { name: `Edit details of ${name}` }));
  const dialog = await screen.findByRole("dialog", { name: `Edit ${name}` });
  await within(dialog).findByRole("option", { name: "Legal" });                        // the departments have loaded
  return dialog;
};

/** Taken once, before any error appears: a message inside a label changes that label's text. */
const fieldsOf = (dialog) => ({
  category: within(dialog).getByLabelText("Category (optional)"),
  description: within(dialog).getByLabelText("Description (optional)"),
  sla: within(dialog).getByLabelText("Preparation time in days"),
  department: within(dialog).getByLabelText("Default department (optional)"),
  sort: within(dialog).getByLabelText("Sort order"),
  save: within(dialog).getByRole("button", { name: "Save changes" }),
});

describe("edit a document type's details", () => {
  beforeEach(() => {
    document.cookie = "csrftoken=abc; path=/";
  });

  it("opens with the current values, the name shown but not editable, and Save switched off", async () => {
    await openPage();
    const dialog = await openEdit();
    const f = fieldsOf(dialog);
    expect(within(dialog).getByText(INTRO)).toBeInTheDocument();
    expect(within(dialog).getByText("Site Survey Plan")).toBeInTheDocument();
    expect(within(dialog).getByText("The name has its own action, Rename.")).toBeInTheDocument();
    expect(f.category).toHaveValue("Survey");
    expect(f.description).toHaveValue("");
    expect(f.sla.value).toBe("45");
    expect(f.department).toHaveValue("7");
    expect(f.sort.value).toBe("10");
    expect(within(f.department).getAllByRole("option").map((o) => o.textContent)).toEqual(["No department", "Legal", "Operations"]);
    expect(f.save).toBeDisabled();
    expect(within(dialog).getByText(UNCHANGED)).toBeInTheDocument();
  });

  it("needs a real change: reverting turns Save off, and a difference in spacing alone is not a change", async () => {
    await openPage();
    const dialog = await openEdit();
    const f = fieldsOf(dialog);
    setValue(f.category, "Planning");
    expect(f.save).toBeEnabled();
    expect(within(dialog).queryByText(UNCHANGED)).not.toBeInTheDocument();
    setValue(f.category, "Survey");
    expect(f.save).toBeDisabled();
    setValue(f.category, "  Survey  ");
    expect(f.save).toBeDisabled();
    setValue(f.description, "   ");                                // blank, and it was blank
    expect(f.save).toBeDisabled();
    setValue(f.category, "Sur  vey");                              // different text once tidied
    expect(f.save).toBeEnabled();
  });

  it("sends only the field that changed, with numbers as numbers and the department as its id", async () => {
    const api = await openPage({ [PATCH_SITE]: () => json(site) });
    const cases = [
      [(f) => setValue(f.category, "Planning"), { category: "Planning" }],
      [(f) => setValue(f.description, "Surveyed by the council"), { description: "Surveyed by the council" }],
      [(f) => setValue(f.sla, "60"), { sla_days: 60 }],
      [(f) => userEvent.selectOptions(f.department, "Operations"), { default_department: 8 }],
      [(f) => setValue(f.sort, "20"), { sort_order: 20 }],
    ];
    for (const [change, expected] of cases) {
      const dialog = await openEdit();
      const f = fieldsOf(dialog);
      await change(f);
      await userEvent.click(f.save);
      await waitFor(() => expect(screen.queryByRole("dialog")).not.toBeInTheDocument());
      expect(body(patches(api).at(-1))).toEqual(expected);
    }
    expect(patches(api)).toHaveLength(5);
    expect(patches(api)[0].url).toBe("/api/document-types/41/");
  });

  it("sends several changes together", async () => {
    const api = await openPage({ [PATCH_SITE]: () => json(site) });
    const dialog = await openEdit();
    const f = fieldsOf(dialog);
    setValue(f.category, "Planning");
    setValue(f.sla, "60");
    await userEvent.selectOptions(f.department, "Operations");
    await userEvent.click(f.save);
    await screen.findByText("Site Survey Plan updated.");
    expect(body(patches(api)[0])).toEqual({ category: "Planning", sla_days: 60, default_department: 8 });
  });

  it("clears a category, a description and a department by sending '' and null", async () => {
    const withDescription = { ...site, description: "Old description" };
    const api = await openPage({ "GET /api/document-types/": listWith(withDescription), [PATCH_SITE]: () => json(site) });
    const dialog = await openEdit();
    const f = fieldsOf(dialog);
    setValue(f.category, "");
    setValue(f.description, "");
    await userEvent.selectOptions(f.department, "No department");
    await userEvent.click(f.save);
    await screen.findByText("Site Survey Plan updated.");
    expect(body(patches(api)[0])).toEqual({ category: "", description: "", default_department: null });
  });

  it("does not send a field that is blank both before and after", async () => {
    const noCategory = { ...site, category: "" };                                // and it has no description either
    const api = await openPage({ "GET /api/document-types/": listWith(noCategory), [PATCH_SITE]: () => json(site) });
    const dialog = await openEdit();
    const f = fieldsOf(dialog);
    setValue(f.sla, "60");
    await userEvent.click(f.save);
    await screen.findByText("Site Survey Plan updated.");
    expect(body(patches(api)[0])).toEqual({ sla_days: 60 });
  });

  it("keeps Save off when preparation time or sort order is emptied or is not a whole number, and says why", async () => {
    await openPage();
    const dialog = await openEdit();
    const f = fieldsOf(dialog);
    setValue(f.category, "Planning");                              // a real change, so only the numbers can stop it
    expect(f.save).toBeEnabled();
    setValue(f.sla, "");
    expect(f.save).toBeDisabled();
    expect(within(dialog).getByText("Preparation time is required.")).toBeInTheDocument();
    setValue(f.sla, "45.5");
    expect(f.save).toBeDisabled();
    expect(within(dialog).getByText("Preparation time must be a whole number.")).toBeInTheDocument();
    setValue(f.sla, "60");
    expect(f.save).toBeEnabled();
    setValue(f.sort, "");
    expect(f.save).toBeDisabled();
    expect(within(dialog).getByText("Sort order is required.")).toBeInTheDocument();
    setValue(f.sort, "2.5");
    expect(f.save).toBeDisabled();
    expect(within(dialog).getByText("Sort order must be a whole number.")).toBeInTheDocument();
    setValue(f.sort, "0");                                         // zero is a valid sort order
    expect(f.save).toBeEnabled();
  });

  it("warns about work created from now on only when time or department differs, and about automatic generation only for entries that drive it", async () => {
    await openPage();
    const first = await openEdit();
    let f = fieldsOf(first);
    setValue(f.category, "Planning");
    expect(within(first).queryByText(WORK_NOTE)).not.toBeInTheDocument();
    setValue(f.sla, "60");
    expect(within(first).getByText(WORK_NOTE)).toBeInTheDocument();
    expect(within(first).queryByText(LEGACY_NOTE)).not.toBeInTheDocument();
    setValue(f.sla, "45");
    expect(within(first).queryByText(WORK_NOTE)).not.toBeInTheDocument();
    await userEvent.selectOptions(f.department, "Operations");
    expect(within(first).getByText(WORK_NOTE)).toBeInTheDocument();
    await userEvent.click(within(first).getByRole("button", { name: "Cancel" }));

    const second = await openEdit("Deed of Assignment");           // drives automatic generation
    f = fieldsOf(second);
    setValue(f.category, "Contract");
    setValue(f.description, "A description");
    expect(within(second).queryByText(WORK_NOTE)).not.toBeInTheDocument();      // category and description alone: no warning
    expect(within(second).queryByText(LEGACY_NOTE)).not.toBeInTheDocument();
    setValue(f.sla, "60");
    expect(within(second).getByText(WORK_NOTE)).toBeInTheDocument();
    expect(within(second).getByText(LEGACY_NOTE)).toBeInTheDocument();
  });

  it("disables the preparation time and department when the entry has no current work type, and still saves the rest", async () => {
    const noWork = { ...site, work_type: null, sla_days: null, default_department: null };
    const api = await openPage({ "GET /api/document-types/": listWith(noWork), [PATCH_SITE]: () => json(noWork) });
    const dialog = await openEdit();
    const f = fieldsOf(dialog);
    expect(f.sla).toBeDisabled();
    expect(f.department).toBeDisabled();
    expect(f.sla.value).toBe("");
    expect(within(dialog).getByText(NO_WORK_TYPE)).toBeInTheDocument();
    expect(within(dialog).queryByText("Preparation time is required.")).not.toBeInTheDocument();
    setValue(f.category, "Planning");
    expect(f.save).toBeEnabled();
    await userEvent.click(f.save);
    await screen.findByText("Site Survey Plan updated.");
    expect(body(patches(api)[0])).toEqual({ category: "Planning" });
  });

  it("records it, closes, says what happened, and reloads the catalogue so the row shows the new values", async () => {
    let list = documentTypes;
    const updated = { ...site, category: "Planning", sla_days: 60 };
    const api = await openPage({
      "GET /api/document-types/": () => list,
      [PATCH_SITE]: () => {
        list = listWith(updated);
        return json(updated);
      },
    });
    const dialog = await openEdit();
    const before = listGets(api);
    const f = fieldsOf(dialog);
    setValue(f.category, "Planning");
    setValue(f.sla, "60");
    await userEvent.click(f.save);
    expect(await screen.findByText("Site Survey Plan updated.")).toBeInTheDocument();
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
    await waitFor(() => expect(listGets(api)).toBe(before + 1));
    await waitFor(() => {
      const row = within(screen.getByRole("table")).getByText("Site Survey Plan").closest("tr");
      expect(within(row).getByText("Planning")).toBeInTheDocument();
      expect(within(row).getByText("60 days")).toBeInTheDocument();
    });
  });

  it("shows each field's message beside that field, and not again above", async () => {
    const messages = {
      category: "Category is too long (maximum 60 characters).",
      description: "Description is too long (maximum 1000 characters).",
      sla_days: "SLA days must be a whole number from 1 to 3650.",
      default_department: "Department belongs to another company.",
      sort_order: "Sort order must be a whole number from 0 to 1000000.",
    };
    await openPage({
      [PATCH_SITE]: () => json(Object.fromEntries(Object.entries(messages).map(([k, v]) => [k, [v]])), 400),
    });
    const dialog = await openEdit();
    const f = fieldsOf(dialog);
    setValue(f.category, "Planning");
    await userEvent.click(f.save);
    const alerts = await within(dialog).findAllByRole("alert");
    expect(alerts).toHaveLength(5);
    const controls = {
      category: f.category, description: f.description, sla_days: f.sla,
      default_department: f.department, sort_order: f.sort,
    };
    for (const [key, message] of Object.entries(messages)) {
      const alert = alerts.find((a) => a.textContent === message);
      expect(alert, key).toBeDefined();
      expect(alert.closest("label"), key).toContainElement(controls[key]);
    }
  });

  it("shows 'Nothing to change.' in the form-level box, not beside a field", async () => {
    await openPage({ [PATCH_SITE]: () => json({ detail: "Nothing to change." }, 400) });
    const dialog = await openEdit();
    const f = fieldsOf(dialog);
    setValue(f.category, "Planning");
    await userEvent.click(f.save);
    const alert = await within(dialog).findByRole("alert");
    expect(alert).toHaveTextContent("Nothing to change.");
    expect(alert.closest("label")).toBeNull();
    expect(screen.getByRole("dialog")).toBeInTheDocument();
    expect(f.save).toBeEnabled();
  });

  it("shows a 409 in the form-level box and reloads the catalogue in case it changed", async () => {
    const message = "This document type has no current work type, so its SLA and department cannot be changed.";
    const api = await openPage({ [PATCH_SITE]: () => json({ detail: message }, 409) });
    const dialog = await openEdit();
    const before = listGets(api);
    const f = fieldsOf(dialog);
    setValue(f.sla, "60");
    await userEvent.click(f.save);
    expect(await within(dialog).findByRole("alert")).toHaveTextContent(message);
    await waitFor(() => expect(listGets(api)).toBe(before + 1));
    expect(screen.getByRole("dialog")).toBeInTheDocument();
  });

  it("closes without sending anything on Cancel or Escape", async () => {
    const api = await openPage();
    const first = await openEdit();
    await userEvent.click(within(first).getByRole("button", { name: "Cancel" }));
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
    await openEdit();
    await userEvent.keyboard("{Escape}");
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
    expect(patches(api)).toHaveLength(0);
  });

  it("sends one request however many times Save is pressed while it is working", async () => {
    let release;
    const gate = new Promise((resolve) => { release = resolve; });
    const api = await openPage({ [PATCH_SITE]: async () => { await gate; return json(site); } });
    const dialog = await openEdit();
    const f = fieldsOf(dialog);
    setValue(f.category, "Planning");
    await userEvent.click(f.save);
    await waitFor(() => expect(f.save).toBeDisabled());
    expect(f.save).toHaveTextContent("Saving…");
    await userEvent.click(f.save);
    release();
    expect(await screen.findByText("Site Survey Plan updated.")).toBeInTheDocument();
    expect(patches(api)).toHaveLength(1);
  });
});
