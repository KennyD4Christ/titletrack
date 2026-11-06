import { beforeEach, describe, expect, it } from "vitest";
import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { baseRoutes, json, mockApi, renderApp } from "./test/helpers.jsx";

import documentTypes from "./test/fixtures/document_types.json";
import departments from "./test/fixtures/departments.json";

const PATH = "/settings/document-catalogue";
const LIST_URL = "/api/document-types/?page=1&page_size=200";
const DIALOG = "Add a document type";
const INTRO =
  "A new document type starts active and can then be added to sales. It does not change any existing sale or the automatic generation of documents.";
const SLA_HINT = "If left empty, the standard time is used.";
const SORT_HINT = "Entries are listed by sort order, then name. If left empty, 0 is used.";
const NO_DEPARTMENTS = "Departments could not be loaded. You can add the document type without one.";

const body = (call) => JSON.parse(call.init.body);
const posts = (api) => api.calls.filter((c) => c.method === "POST");

// What the API returns for a new entry: the 11 catalogue fields.
const created = {
  id: 52, name: "Building Inspection", description: "", category: "", is_active: true, sort_order: 0,
  created_at: "2026-11-05T09:00:00+01:00", work_type: 152, sla_days: 90, default_department: null,
  drives_legacy_generation: false,
};

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

/** Opens the dialog without waiting for the departments, for the cases where they never arrive. */
const clickAdd = async () => {
  await userEvent.click(screen.getByRole("button", { name: "Add document type" }));
  return screen.findByRole("dialog", { name: DIALOG });
};

const openAdd = async () => {
  const dialog = await clickAdd();
  await within(dialog).findByRole("option", { name: "Legal" });                       // the departments have loaded
  return dialog;
};

/** Taken once, before any error appears: a message inside a label changes that label's text. */
const fieldsOf = (dialog) => ({
  name: within(dialog).getByLabelText("Name"),
  category: within(dialog).getByLabelText("Category (optional)"),
  description: within(dialog).getByLabelText("Description (optional)"),
  sla: within(dialog).getByLabelText("Preparation time in days (optional)"),
  department: within(dialog).getByLabelText("Default department (optional)"),
  sort: within(dialog).getByLabelText("Sort order (optional)"),
  submit: within(dialog).getByRole("button", { name: "Add document type" }),
});

describe("add a document type", () => {
  beforeEach(() => {
    document.cookie = "csrftoken=abc; path=/";
  });

  it("opens empty from the page header, with the introduction, the hints and Add switched off", async () => {
    await openPage();
    const dialog = await openAdd();
    const f = fieldsOf(dialog);
    expect(within(dialog).getByText(INTRO)).toBeInTheDocument();
    expect(within(dialog).getByText(SLA_HINT)).toBeInTheDocument();
    expect(within(dialog).getByText(SORT_HINT)).toBeInTheDocument();
    expect([f.name.value, f.category.value, f.description.value, f.sla.value, f.sort.value]).toEqual(["", "", "", "", ""]);
    expect(f.department).toHaveValue("");
    expect(within(f.department).getAllByRole("option").map((o) => o.textContent)).toEqual(["No department", "Legal", "Operations"]);
    expect(f.submit).toBeDisabled();
  });

  it("needs a name and nothing else", async () => {
    await openPage();
    const dialog = await openAdd();
    const { name, submit } = fieldsOf(dialog);
    await userEvent.type(name, "   ");
    expect(submit).toBeDisabled();
    await userEvent.type(name, "Building Inspection");
    expect(submit).toBeEnabled();
  });

  it("sends only the name when nothing else is filled in", async () => {
    const api = await openPage({ "POST /api/document-types/": () => json(created, 201) });
    const dialog = await openAdd();
    const { name, submit } = fieldsOf(dialog);
    await userEvent.type(name, "Building Inspection");
    await userEvent.click(submit);
    await screen.findByText("Building Inspection added to the catalogue.");
    const [call] = posts(api);
    expect(call.url).toBe("/api/document-types/");
    expect(body(call)).toEqual({ name: "Building Inspection" });
  });

  it("sends every filled field, with the numbers as numbers and the department as its id", async () => {
    const api = await openPage({ "POST /api/document-types/": () => json(created, 201) });
    const dialog = await openAdd();
    const f = fieldsOf(dialog);
    await userEvent.type(f.name, "Building Inspection");
    await userEvent.type(f.category, "Planning");
    await userEvent.type(f.description, "Inspection before occupancy");
    await userEvent.type(f.sla, "45");
    await userEvent.selectOptions(f.department, "Legal");
    await userEvent.type(f.sort, "5");
    await userEvent.click(f.submit);
    await screen.findByText(/added to the catalogue/);
    expect(body(posts(api)[0])).toEqual({
      name: "Building Inspection", category: "Planning", description: "Inspection before occupancy",
      sla_days: 45, default_department: 7, sort_order: 5,
    });
  });

  it("trims text, leaves out empty optionals and 'No department', and still sends a sort order of zero", async () => {
    const api = await openPage({ "POST /api/document-types/": () => json(created, 201) });
    const dialog = await openAdd();
    const f = fieldsOf(dialog);
    await userEvent.type(f.name, "  Building Inspection  ");
    await userEvent.type(f.category, "  Planning  ");
    await userEvent.type(f.description, "   ");
    await userEvent.type(f.sort, "0");
    await userEvent.click(f.submit);
    await screen.findByText(/added to the catalogue/);
    expect(body(posts(api)[0])).toEqual({ name: "Building Inspection", category: "Planning", sort_order: 0 });
  });

  it("records it, closes, says what happened, and reloads the catalogue so the new entry is listed", async () => {
    let list = documentTypes;
    const api = await openPage({
      "GET /api/document-types/": () => list,
      "POST /api/document-types/": () => {
        list = { ...documentTypes, count: 12, results: [created, ...documentTypes.results] };
        return json(created, 201);
      },
    });
    const listGets = () => api.urls().filter((u) => u === LIST_URL).length;
    const dialog = await openAdd();
    const before = listGets();
    const { name, submit } = fieldsOf(dialog);
    await userEvent.type(name, "Building Inspection");
    await userEvent.click(submit);
    expect(await screen.findByText("Building Inspection added to the catalogue.")).toBeInTheDocument();
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
    await waitFor(() => expect(listGets()).toBe(before + 1));
    await waitFor(() => expect(within(screen.getByRole("table")).getByText("Building Inspection")).toBeInTheDocument());
  });

  it("shows a name that is already taken beside the name, and keeps the dialog open", async () => {
    const message = "A document type with this name already exists.";
    await openPage({ "POST /api/document-types/": () => json({ name: [message] }, 400) });
    const dialog = await openAdd();
    const { name, submit } = fieldsOf(dialog);
    await userEvent.type(name, "Survey Plan");
    await userEvent.click(submit);
    const alert = await within(dialog).findByRole("alert");
    expect(alert).toHaveTextContent(message);
    expect(alert.closest("label")).toContainElement(name);
    expect(within(dialog).getAllByRole("alert")).toHaveLength(1);        // beside the field only, not repeated above
    expect(screen.getByRole("dialog")).toBeInTheDocument();
    expect(submit).toBeEnabled();
  });

  it("shows each of the other fields' messages beside that field, and not again above", async () => {
    const messages = {
      category: "Category is too long (maximum 60 characters).",
      description: "Description is too long (maximum 1000 characters).",
      sla_days: "SLA days must be a whole number from 1 to 3650.",
      default_department: "Department belongs to another company.",
      sort_order: "Sort order must be a whole number from 0 to 1000000.",
    };
    await openPage({
      "POST /api/document-types/": () => json(Object.fromEntries(Object.entries(messages).map(([k, v]) => [k, [v]])), 400),
    });
    const dialog = await openAdd();
    const f = fieldsOf(dialog);
    await userEvent.type(f.name, "Building Inspection");
    await userEvent.click(f.submit);
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

  it("shows a refusal that is not about a field in the form-level box", async () => {
    await openPage({ "POST /api/document-types/": () => json({ detail: "Something is wrong." }, 400) });
    const dialog = await openAdd();
    const { name, submit } = fieldsOf(dialog);
    await userEvent.type(name, "Building Inspection");
    await userEvent.click(submit);
    const alert = await within(dialog).findByRole("alert");
    expect(alert).toHaveTextContent("Something is wrong.");
    expect(alert.closest("label")).toBeNull();
    expect(screen.getByRole("dialog")).toBeInTheDocument();
  });

  it("disables the department choice and says why when the departments cannot be loaded, and still adds", async () => {
    const api = await openPage({
      "GET /api/departments/": () => json({ detail: "Departments are unavailable." }, 500),
      "POST /api/document-types/": () => json(created, 201),
    });
    const dialog = await clickAdd();
    expect(await within(dialog).findByText(NO_DEPARTMENTS)).toBeInTheDocument();
    const { name, department, submit } = fieldsOf(dialog);
    expect(department).toBeDisabled();
    await userEvent.type(name, "Building Inspection");
    await userEvent.click(submit);
    await screen.findByText("Building Inspection added to the catalogue.");
    expect(body(posts(api)[0])).toEqual({ name: "Building Inspection" });
  });

  it("closes without sending anything on Cancel or Escape", async () => {
    const api = await openPage();
    const first = await openAdd();
    await userEvent.click(within(first).getByRole("button", { name: "Cancel" }));
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
    await openAdd();
    await userEvent.keyboard("{Escape}");
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
    expect(posts(api)).toHaveLength(0);
  });

  it("sends one request however many times Add is pressed while it is working", async () => {
    let release;
    const gate = new Promise((resolve) => { release = resolve; });
    const api = await openPage({ "POST /api/document-types/": async () => { await gate; return json(created, 201); } });
    const dialog = await openAdd();
    const { name, submit } = fieldsOf(dialog);
    await userEvent.type(name, "Building Inspection");
    await userEvent.click(submit);
    await waitFor(() => expect(submit).toBeDisabled());
    expect(submit).toHaveTextContent("Adding…");
    await userEvent.click(submit);
    release();
    expect(await screen.findByText("Building Inspection added to the catalogue.")).toBeInTheDocument();
    expect(posts(api)).toHaveLength(1);
  });
});
