import { describe, expect, it } from "vitest";
import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { baseRoutes, json, mockApi, renderApp } from "./test/helpers.jsx";

import documentTypes from "./test/fixtures/document_types.json";
import departments from "./test/fixtures/departments.json";

const PATH = "/settings/document-catalogue";
const emptyList = { count: 0, next: null, previous: null, results: [] };
const pageOf = (url) => new URL(url, "http://localhost").searchParams.get("page");

const routes = (extra = {}, role = "manager") =>
  baseRoutes(role, {
    "GET /api/document-types/": documentTypes,
    "GET /api/departments/": departments,
    ...extra,
  });

const openPage = async (extra, role, path = PATH) => {
  const api = mockApi(routes(extra, role));
  renderApp(path);
  await screen.findByRole("heading", { name: "Document catalogue" });
  return api;
};

const rowOf = (table, name) => within(table).getByText(name).closest("tr");
const cellTexts = (row) => within(row).getAllByRole("cell").map((c) => c.textContent);
const rowNames = (table) => within(table).getAllByRole("row").slice(1).map((r) => within(r).getAllByRole("cell")[0].textContent);

describe("the sidebar's Settings group", () => {
  it("shows the heading, with the catalogue link beneath it and apart from the everyday pages, to MD and Manager", async () => {
    for (const role of ["md", "manager"]) {
      mockApi(routes({}, role));
      const { unmount } = renderApp("/");
      const nav = await screen.findByRole("navigation", { name: "Main" });
      const settings = await within(nav).findByRole("list", { name: "Settings" });
      expect(within(nav).getByText("Settings")).toBeInTheDocument();
      expect(within(settings).getByRole("link", { name: "Document catalogue" })).toHaveAttribute("href", PATH);
      const everyday = within(nav).getAllByRole("list")[0];
      expect(within(everyday).queryByRole("link", { name: "Document catalogue" })).not.toBeInTheDocument();
      expect(within(everyday).getByRole("link", { name: "Home" })).toBeInTheDocument();
      unmount();
    }
  });

  it("shows neither the heading nor the link to Staff, Sales or Viewer", async () => {
    for (const role of ["staff", "sales", "viewer"]) {
      mockApi(routes({}, role));
      const { unmount } = renderApp("/");
      const nav = await screen.findByRole("navigation", { name: "Main" });
      expect(within(nav).getByRole("link", { name: "Home" })).toBeInTheDocument();   // the nav has been built for this person
      expect(within(nav).queryByText("Settings")).not.toBeInTheDocument();
      expect(within(nav).queryByRole("link", { name: "Document catalogue" })).not.toBeInTheDocument();
      unmount();
    }
  });
});

describe("the document catalogue page", () => {
  it("refuses Staff, Sales and Viewer at the address, without asking for the catalogue", async () => {
    for (const role of ["staff", "sales", "viewer"]) {
      const api = mockApi(routes({}, role));
      const { unmount } = renderApp(PATH);
      expect(await screen.findByRole("heading", { name: "You do not have access to this page" })).toBeInTheDocument();
      expect(api.urls().some((u) => u.startsWith("/api/document-types/"))).toBe(false);
      unmount();
    }
  });

  it("opens for an MD, with the link highlighted in the sidebar", async () => {
    await openPage(undefined, "md");
    expect(await screen.findByRole("table")).toBeInTheDocument();
    const nav = screen.getByRole("navigation", { name: "Main" });
    expect(within(nav).getByRole("link", { name: "Document catalogue" })).toHaveClass("active");
  });

  it("lists every entry, active or not, in the order the server gives, says how many, and reads every page", async () => {
    const api = await openPage();
    const table = await screen.findByRole("table");
    expect(rowNames(table)).toEqual(documentTypes.results.map((t) => t.name));
    expect(screen.getByText("11 document types, listed by sort order and then name.")).toBeInTheDocument();
    expect(api.urls()).toContain("/api/document-types/?page=1&page_size=200");
    expect(api.urls()).toContain("/api/departments/?page_size=200");
  });

  it("shows a row's category, preparation time, default department, sort order and status", async () => {
    await openPage();
    const table = await screen.findByRole("table");
    const row = rowOf(table, "Site Survey Plan");
    await within(row).findByText("Legal");                           // the department name arrives with its own request
    expect(cellTexts(row).slice(0, 6)).toEqual(["Site Survey Plan", "Survey", "45 days", "Legal", "10", "Active"]);
  });

  it("marks entries that drive automatic generation, and mutes and labels inactive ones", async () => {
    await openPage();
    const table = await screen.findByRole("table");
    expect(within(table).getAllByText("Automatic generation")).toHaveLength(1);
    expect(within(rowOf(table, "Deed of Assignment")).getByText("Automatic generation")).toBeInTheDocument();
    const old = rowOf(table, "Old Transfer Form");
    expect(old).toHaveClass("row-cancelled");
    expect(within(old).getByText("Inactive")).toBeInTheDocument();
    expect(within(table).getAllByText("Inactive")).toHaveLength(1);
    expect(within(rowOf(table, "Site Plan")).getByText("Active")).toBeInTheDocument();
  });

  it("shows a dash when there is no default department, and the id when the department is unknown", async () => {
    const none = documentTypes.results[0];                                          // Certificate of Occupancy: no department
    const unknown = { ...documentTypes.results[3], default_department: 99 };        // Site Survey Plan
    await openPage({ "GET /api/document-types/": { ...documentTypes, count: 2, results: [none, unknown] } });
    const table = await screen.findByRole("table");
    expect(await within(rowOf(table, "Site Survey Plan")).findByText("Department #99")).toBeInTheDocument();
    expect(cellTexts(rowOf(table, "Certificate of Occupancy"))[3]).toBe("—");
  });

  it("still lists the entries when the departments cannot be loaded, and says so", async () => {
    await openPage({ "GET /api/departments/": () => json({ detail: "Departments are unavailable." }, 500) });
    const table = await screen.findByRole("table");
    expect(await screen.findByText("Department names could not be loaded.")).toBeInTheDocument();
    expect(within(rowOf(table, "Site Survey Plan")).getByText("Department #7")).toBeInTheDocument();
  });

  it("filters by status from the drop-down", async () => {
    await openPage();
    await screen.findByRole("table");
    const status = screen.getByLabelText("Status");
    await userEvent.selectOptions(status, "Inactive");
    await waitFor(() => expect(rowNames(screen.getByRole("table"))).toEqual(["Old Transfer Form"]));
    expect(screen.getByText("1 document type, listed by sort order and then name.")).toBeInTheDocument();
    await userEvent.selectOptions(status, "Active");
    await waitFor(() => expect(rowNames(screen.getByRole("table"))).toHaveLength(10));
    expect(within(screen.getByRole("table")).queryByText("Old Transfer Form")).not.toBeInTheDocument();
    await userEvent.selectOptions(status, "All");
    await waitFor(() => expect(rowNames(screen.getByRole("table"))).toHaveLength(11));
  });

  it("opens straight onto a status given in the address", async () => {
    await openPage(undefined, "manager", `${PATH}?status=inactive`);
    const table = await screen.findByRole("table");
    expect(rowNames(table)).toEqual(["Old Transfer Form"]);
    expect(screen.getByLabelText("Status")).toHaveValue("inactive");
  });

  it("says so when the catalogue is empty", async () => {
    await openPage({ "GET /api/document-types/": emptyList });
    expect(await screen.findByText("The catalogue is empty.")).toBeInTheDocument();
    expect(screen.queryByRole("table")).not.toBeInTheDocument();
  });

  it("says so when no entry matches the chosen status", async () => {
    const allActive = { ...documentTypes, count: 10, results: documentTypes.results.filter((t) => t.is_active) };
    await openPage({ "GET /api/document-types/": allActive }, "manager", `${PATH}?status=inactive`);
    expect(await screen.findByText("No document types match this filter.")).toBeInTheDocument();
    expect(screen.queryByRole("table")).not.toBeInTheDocument();
  });

  it("shows a failure with a retry, and recovers", async () => {
    let attempt = 0;
    await openPage({
      "GET /api/document-types/": () => (++attempt === 1 ? json({ detail: "Something went wrong." }, 500) : documentTypes),
    });
    expect(await screen.findByRole("alert")).toHaveTextContent("Something went wrong.");
    await userEvent.click(screen.getByRole("button", { name: "Try again" }));
    expect(await screen.findByRole("table")).toBeInTheDocument();
  });

  it("reads every page of the catalogue, so entries on a later page are listed", async () => {
    const first = {
      count: 11, next: "http://localhost/api/document-types/?page=2&page_size=200", previous: null,
      results: documentTypes.results.slice(0, 6),
    };
    const second = {
      count: 11, next: null, previous: "http://localhost/api/document-types/?page=1&page_size=200",
      results: documentTypes.results.slice(6),
    };
    const api = await openPage({ "GET /api/document-types/": (url) => (pageOf(url) === "1" ? first : second) });
    const table = await screen.findByRole("table");
    expect(rowNames(table)).toEqual(documentTypes.results.map((t) => t.name));
    expect(api.urls().filter((u) => u.startsWith("/api/document-types/"))).toEqual([
      "/api/document-types/?page=1&page_size=200",
      "/api/document-types/?page=2&page_size=200",
    ]);
  });

  it("says when the catalogue could not be read in full, instead of presenting it as complete", async () => {
    await openPage({ "GET /api/document-types/": { ...documentTypes, count: 250 } });
    expect(await screen.findByText("Showing 11 of 250 document types.")).toBeInTheDocument();
  });

  it("offers Edit details, Rename and Deactivate or Activate on every row, and nothing else", async () => {
    await openPage();
    const table = await screen.findByRole("table");
    const rows = within(table).getAllByRole("row").slice(1);
    expect(rows).toHaveLength(11);
    for (const row of rows) {
      const name = within(row).getAllByRole("cell")[0].textContent;
      const toggle = within(row).queryByText("Inactive") ? `Activate ${name}` : `Deactivate ${name}`;
      expect(within(row).getAllByRole("button").map((b) => b.getAttribute("aria-label")))
        .toEqual([`Edit details of ${name}`, `Rename ${name}`, toggle]);
    }
  });
});
