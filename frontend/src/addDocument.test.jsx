import { beforeEach, describe, expect, it } from "vitest";
import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { MemoryRouter } from "react-router-dom";
import { baseRoutes, json, mockApi, renderApp } from "./test/helpers.jsx";
import { AuthProvider, useAuth } from "./context/AuthContext.jsx";
import SaleDocumentsSection from "./components/sales/SaleDocumentsSection.jsx";
import { todayIso } from "./utils/sales.js";

import saleDetail from "./test/fixtures/sale_detail.json";
import saleDocuments from "./test/fixtures/sale_documents.json";
import documentTypes from "./test/fixtures/document_types.json";

const id = saleDetail.id;
const body = (call) => JSON.parse(call.init.body);
const posts = (api) => api.calls.filter((c) => c.method === "POST");
const emptyList = { count: 0, next: null, previous: null, results: [] };
const FOLLOWING = 4; // Node.DOCUMENT_POSITION_FOLLOWING

const DIALOG = "Add a document to this sale";
const OWED_HINT =
  "If this sale already has open work for this document, it will be linked to it. Otherwise new preparation work will be created for it.";
const ISSUED_HINT = "An already issued document creates no preparation work.";
const REFUSED = "Nothing was added: the sale or the catalogue changed while this form was open. ";
const INCOMPLETE =
  "Some records could not be loaded, so this list may be incomplete. The server will refuse a document this sale already has.";

// What the API returns once "Building Permit" (type 50, which this sale does not have) has been added as owed.
const surveyRow = saleDocuments.results.find((d) => d.name === "Survey Plan");
const addedBuilding = {
  ...surveyRow, id: 3008, document_type: 50, name: "Building Permit", catalogue_name: "Building Permit",
  obligation: 2008, due_date: "2027-01-15",
};

// The section's own list has no `page` in its address; the dialog's reads always do.
const SECTION_LIST = `/api/sale-documents/?sale=${id}&page_size=200`;
const pageOf = (url) => new URL(url, "http://localhost").searchParams.get("page");
const sectionListGets = (api) => api.urls().filter((u) => u === SECTION_LIST).length;
const dialogDocumentUrls = (api) => api.urls().filter((u) => u.startsWith(`/api/sale-documents/?sale=${id}&page=`));
const typeUrls = (api) => api.urls().filter((u) => u.startsWith("/api/document-types/"));

const routes = (extra = {}, role = "manager") =>
  baseRoutes(role, {
    [`GET /api/sales-desk/${id}/`]: saleDetail,
    "GET /api/sale-documents/": saleDocuments,
    "GET /api/document-types/": documentTypes,
    ...extra,
  });

const openSale = async (extra, role) => {
  const api = mockApi(routes(extra, role));
  renderApp(`/sales/${id}`);
  await screen.findByRole("heading", { name: saleDetail.buyer_name });
  return api;
};

/** The "Sale documents" card, once its rows have loaded. */
const loaded = async () => {
  const heading = await screen.findByRole("heading", { name: "Sale documents" });
  const card = heading.closest("section");
  await within(card).findByText("Contract of Sale");
  return card;
};
const rowOf = (card, name) => within(card).getByText(name).closest("tr");

/** Opens the dialog without waiting for the picker, for the cases where it has no picker. */
const clickAdd = async () => {
  const card = await loaded();
  await userEvent.click(within(card).getByRole("button", { name: "Add document" }));
  const dialog = await screen.findByRole("dialog", { name: DIALOG });
  return { card, dialog };
};

const openAdd = async () => {
  const opened = await clickAdd();
  await within(opened.dialog).findByRole("option", { name: "Building Permit" });   // both reads have finished
  return opened;
};

/** Taken once, before any error appears: a message inside a label changes that label's text. */
const pickerOf = (dialog) => ({
  type: within(dialog).getByLabelText("Document type"),
  submit: within(dialog).getByRole("button", { name: "Add document" }),
});
const chooseIssued = (dialog) => userEvent.click(within(dialog).getByRole("radio", { name: "Already issued" }));

function AuthProbe() {
  const { status } = useAuth();
  return <p>{`auth: ${status}`}</p>;
}

/** The section on its own: a role that could never open the sale page can be tried, and the app's retry default can be set. */
const renderSection = (role, { extra = {}, queryDefaults = {} } = {}) => {
  const api = mockApi(baseRoutes(role, { "GET /api/sale-documents/": saleDocuments, ...extra }));
  const client = new QueryClient({ defaultOptions: { queries: { retry: false, ...queryDefaults } } });
  const view = render(
    <QueryClientProvider client={client}>
      <MemoryRouter>
        <AuthProvider>
          <AuthProbe />
          <SaleDocumentsSection saleId="612" />
        </AuthProvider>
      </MemoryRouter>
    </QueryClientProvider>,
  );
  return { api, ...view };
};

describe("add a document to a sale", () => {
  beforeEach(() => {
    document.cookie = "csrftoken=abc; path=/";
  });

  it("offers Add document above the table to a manager once the list has loaded", async () => {
    await openSale();
    const card = await loaded();
    const button = within(card).getByRole("button", { name: "Add document" });
    expect(button.compareDocumentPosition(card.querySelector("table")) & FOLLOWING).toBeTruthy();
  });

  it("offers it to an MD too", async () => {
    await openSale(undefined, "md");
    const card = await loaded();
    expect(within(card).getByRole("button", { name: "Add document" })).toBeInTheDocument();
  });

  it("offers it when the sale has no documents yet", async () => {
    await openSale({ "GET /api/sale-documents/": emptyList });
    expect(await screen.findByText("No catalogue documents have been added to this sale yet.")).toBeInTheDocument();
    const card = screen.getByRole("heading", { name: "Sale documents" }).closest("section");
    expect(within(card).getByRole("button", { name: "Add document" })).toBeInTheDocument();
  });

  it("hides it on a cancelled sale", async () => {
    const cancelledSale = {
      ...saleDetail, status: "cancelled", cancel_reason: "Buyer defaulted",
      can: { record_payment: false, reverse_payment: false, edit_buyer: true, cancel: false, reinstate: true },
    };
    await openSale({ [`GET /api/sales-desk/${id}/`]: cancelledSale });
    const card = await loaded();
    expect(within(card).queryByRole("button", { name: "Add document" })).not.toBeInTheDocument();
  });

  it("hides it from a role without the manage_sale_documents permission", async () => {
    const first = renderSection("manager");                          // control: the same screen does offer it to a manager
    await screen.findByText("auth: authenticated");
    let card = (await screen.findByRole("heading", { name: "Sale documents" })).closest("section");
    await within(card).findByText("Contract of Sale");
    expect(within(card).getByRole("button", { name: "Add document" })).toBeInTheDocument();
    first.unmount();

    renderSection("sales");
    await screen.findByText("auth: authenticated");
    card = (await screen.findByRole("heading", { name: "Sale documents" })).closest("section");
    await within(card).findByText("Contract of Sale");
    expect(within(card).queryByRole("button", { name: "Add document" })).not.toBeInTheDocument();
  });

  it("reads the catalogue and the sale's documents only when the dialog opens", async () => {
    const api = await openSale();
    const card = await loaded();
    expect(typeUrls(api)).toHaveLength(0);
    expect(dialogDocumentUrls(api)).toHaveLength(0);
    await userEvent.click(within(card).getByRole("button", { name: "Add document" }));
    await screen.findByRole("dialog", { name: DIALOG });
    await waitFor(() => {
      expect(typeUrls(api)).toEqual(["/api/document-types/?is_active=1&page=1&page_size=200"]);
      expect(dialogDocumentUrls(api)).toEqual([`/api/sale-documents/?sale=${id}&page=1&page_size=200`]);
    });
  });

  it("offers only active types the sale does not already have, sorted by name", async () => {
    await openSale();
    const { dialog } = await openAdd();
    const { type } = pickerOf(dialog);
    expect(within(type).getAllByRole("option").map((o) => o.textContent)).toEqual([
      "Choose a document type…", "Building Permit", "Certificate of Occupancy", "Deed of Sublease",
    ]);
    expect(within(dialog).queryByText(INCOMPLETE)).not.toBeInTheDocument();
  });

  it("says so, and offers nothing to submit, when every active type is already on the sale", async () => {
    const onSaleOnly = { ...documentTypes, count: 7, results: documentTypes.results.filter((t) => t.id >= 41 && t.id <= 47) };
    const api = await openSale({ "GET /api/document-types/": onSaleOnly });
    const { dialog } = await clickAdd();
    expect(await within(dialog).findByText("Every active document type is already on this sale.")).toBeInTheDocument();
    expect(within(dialog).queryByLabelText("Document type")).not.toBeInTheDocument();
    expect(within(dialog).queryByRole("button", { name: "Add document" })).not.toBeInTheDocument();
    await userEvent.click(within(dialog).getByRole("button", { name: "Cancel" }));
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
    expect(posts(api)).toHaveLength(0);
  });

  it("says so when the catalogue has no active types at all", async () => {
    await openSale({ "GET /api/document-types/": emptyList });
    const { dialog } = await clickAdd();
    expect(await within(dialog).findByText("There are no active document types in the catalogue.")).toBeInTheDocument();
    expect(within(dialog).queryByRole("button", { name: "Add document" })).not.toBeInTheDocument();
  });

  it("loads every page of the catalogue, so a type on a later page is offered", async () => {
    const first = {
      count: 11, next: "http://localhost/api/document-types/?is_active=1&page=2&page_size=200", previous: null,
      results: documentTypes.results.slice(3),                         // the seven the sale has, and the inactive one
    };
    const second = {
      count: 11, next: null, previous: "http://localhost/api/document-types/?is_active=1&page=1&page_size=200",
      results: documentTypes.results.slice(0, 3),                      // the three it lacks
    };
    const api = await openSale({ "GET /api/document-types/": (url) => (pageOf(url) === "1" ? first : second) });
    const { dialog } = await openAdd();                                // waits for "Building Permit", which is on page 2
    const { type } = pickerOf(dialog);
    expect(within(type).getAllByRole("option").map((o) => o.textContent)).toEqual([
      "Choose a document type…", "Building Permit", "Certificate of Occupancy", "Deed of Sublease",
    ]);
    expect(typeUrls(api)).toEqual([
      "/api/document-types/?is_active=1&page=1&page_size=200",
      "/api/document-types/?is_active=1&page=2&page_size=200",
    ]);
  });

  it("loads every page of the sale's documents, so a type attached on a later page is left out", async () => {
    const rows = [...saleDocuments.results, addedBuilding];            // Building Permit is attached, on the second page
    const first = {
      count: 8, next: `http://localhost/api/sale-documents/?sale=${id}&page=2&page_size=200`, previous: null,
      results: rows.slice(0, 4),
    };
    const second = {
      count: 8, next: null, previous: `http://localhost/api/sale-documents/?sale=${id}&page=1&page_size=200`,
      results: rows.slice(4),
    };
    const api = await openSale({
      "GET /api/sale-documents/": (url) => {
        const page = pageOf(url);
        if (!page) return saleDocuments;                               // the section's own list
        return page === "1" ? first : second;
      },
    });
    const { dialog } = await clickAdd();
    await within(dialog).findByRole("option", { name: "Certificate of Occupancy" });
    const { type } = pickerOf(dialog);
    expect(within(type).getAllByRole("option").map((o) => o.textContent)).toEqual([
      "Choose a document type…", "Certificate of Occupancy", "Deed of Sublease",
    ]);
    expect(dialogDocumentUrls(api)).toEqual([
      `/api/sale-documents/?sale=${id}&page=1&page_size=200`,
      `/api/sale-documents/?sale=${id}&page=2&page_size=200`,
    ]);
  });

  it("says when the lists could not be loaded in full, instead of presenting them as complete", async () => {
    await openSale({ "GET /api/document-types/": { ...documentTypes, count: 250 } });
    const { dialog } = await openAdd();
    expect(within(dialog).getByText(INCOMPLETE)).toBeInTheDocument();
  });

  it("adds an owed document, sending only the sale, the type and the status", async () => {
    const api = await openSale({ "POST /api/sale-documents/": () => json(addedBuilding, 201) });
    const { dialog } = await openAdd();
    const { type, submit } = pickerOf(dialog);
    expect(submit).toBeDisabled();
    expect(within(dialog).getByRole("radio", { name: "Still owed" })).toBeChecked();
    await userEvent.selectOptions(type, "Building Permit");
    expect(submit).toBeEnabled();
    await userEvent.click(submit);
    await screen.findByText(/added to this sale/);
    const [call] = posts(api);
    expect(call.url).toBe("/api/sale-documents/");
    expect(body(call)).toEqual({ sale: 612, document_type: 50, state: "owed" });
    expect(typeof body(call).sale).toBe("number");
    expect(body(call)).not.toHaveProperty("obligation");
  });

  it("adds an already issued document with a date and a trimmed reference", async () => {
    const api = await openSale({ "POST /api/sale-documents/": () => json(addedBuilding, 201) });
    const { dialog } = await openAdd();
    const { type, submit } = pickerOf(dialog);
    await userEvent.selectOptions(type, "Certificate of Occupancy");
    await chooseIssued(dialog);
    const date = within(dialog).getByLabelText("Issue date");
    const reference = within(dialog).getByLabelText("Reference (optional)");
    expect(date).toHaveValue("");
    expect(date).toHaveAttribute("max", todayIso());
    expect(submit).toBeDisabled();                                    // an issued document needs its date
    await userEvent.click(within(dialog).getByRole("button", { name: "Use today's date" }));
    expect(date).toHaveValue(todayIso());
    fireEvent.change(date, { target: { value: "2026-11-02" } });
    await userEvent.type(reference, "  CofO-77 ");
    expect(submit).toBeEnabled();
    await userEvent.click(submit);
    await screen.findByText(/added to this sale/);
    expect(body(posts(api)[0])).toEqual({
      sale: 612, document_type: 48, state: "issued", issued_on: "2026-11-02", reference: "CofO-77",
    });
  });

  it("drops the issued details when the status goes back to still owed", async () => {
    const api = await openSale({ "POST /api/sale-documents/": () => json(addedBuilding, 201) });
    const { dialog } = await openAdd();
    const { type, submit } = pickerOf(dialog);
    await userEvent.selectOptions(type, "Building Permit");
    await chooseIssued(dialog);
    fireEvent.change(within(dialog).getByLabelText("Issue date"), { target: { value: "2026-11-02" } });
    await userEvent.type(within(dialog).getByLabelText("Reference (optional)"), "X");
    await userEvent.click(within(dialog).getByRole("radio", { name: "Still owed" }));
    expect(within(dialog).queryByLabelText("Issue date")).not.toBeInTheDocument();
    expect(within(dialog).queryByLabelText("Reference (optional)")).not.toBeInTheDocument();
    await userEvent.click(submit);
    await screen.findByText(/added to this sale/);
    expect(body(posts(api)[0])).toEqual({ sale: 612, document_type: 50, state: "owed" });
  });

  it("shows the hint that belongs to each status", async () => {
    await openSale();
    const { dialog } = await openAdd();
    expect(within(dialog).getByText(OWED_HINT)).toBeInTheDocument();
    await chooseIssued(dialog);
    expect(within(dialog).getByText(ISSUED_HINT)).toBeInTheDocument();
    expect(within(dialog).queryByText(OWED_HINT)).not.toBeInTheDocument();
  });

  it("records it, closes, says what happened, and reloads the documents and the sale", async () => {
    let docs = saleDocuments;
    const api = await openSale({
      "GET /api/sale-documents/": () => docs,
      "POST /api/sale-documents/": () => {
        docs = { ...saleDocuments, count: 8, results: [...saleDocuments.results, addedBuilding] };
        return json(addedBuilding, 201);
      },
    });
    const { card, dialog } = await openAdd();
    const saleGets = () => api.calls.filter((c) => c.method === "GET" && c.url === `/api/sales-desk/${id}/`).length;
    const listBefore = sectionListGets(api);
    const saleBefore = saleGets();
    const { type, submit } = pickerOf(dialog);
    await userEvent.selectOptions(type, "Building Permit");
    await userEvent.click(submit);
    expect(await within(card).findByText("Building Permit added to this sale.")).toBeInTheDocument();
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
    await waitFor(() => expect(sectionListGets(api)).toBe(listBefore + 1));
    await waitFor(() => expect(saleGets()).toBe(saleBefore + 1));
    await waitFor(() => expect(within(rowOf(card, "Building Permit")).getByText("Outstanding")).toBeInTheDocument());
  });

  it("shows a refusal about the document type beside the picker", async () => {
    await openSale({ "POST /api/sale-documents/": () => json({ document_type: ["This sale already has this document."] }, 409) });
    const { dialog } = await openAdd();
    const { type, submit } = pickerOf(dialog);
    await userEvent.selectOptions(type, "Building Permit");
    await userEvent.click(submit);
    const alert = await within(dialog).findByRole("alert");
    expect(alert).toHaveTextContent("This sale already has this document.");
    expect(alert.closest("label")).toContainElement(type);
    expect(within(dialog).getAllByRole("alert")).toHaveLength(1);        // beside the field only, not repeated above
  });

  it("shows a refusal about the status beside the status choice", async () => {
    const message = "This sale already has an obligation for this document. Add it as owed first, then record its issuance.";
    await openSale({ "POST /api/sale-documents/": () => json({ state: [message] }, 409) });
    const { dialog } = await openAdd();
    const { type, submit } = pickerOf(dialog);
    await userEvent.selectOptions(type, "Building Permit");
    await chooseIssued(dialog);
    fireEvent.change(within(dialog).getByLabelText("Issue date"), { target: { value: "2026-11-02" } });
    await userEvent.click(submit);
    const alert = await within(dialog).findByRole("alert");
    expect(alert).toHaveTextContent(message);
    expect(alert.closest("fieldset")).toContainElement(within(dialog).getByRole("radio", { name: "Already issued" }));
    expect(within(dialog).getAllByRole("alert")).toHaveLength(1);
  });

  it("shows a date problem beside the date, though the picker would not offer that date", async () => {
    // Wording follows _checked_date in tracker/services.py; the earliest year is not asserted here.
    const message = "Issue date is implausible (before the earliest accepted year or in the future).";
    const api = await openSale({ "POST /api/sale-documents/": () => json({ issued_on: [message] }, 400) });
    const { dialog } = await openAdd();
    const { type, submit } = pickerOf(dialog);
    await userEvent.selectOptions(type, "Building Permit");
    await chooseIssued(dialog);
    const date = within(dialog).getByLabelText("Issue date");
    fireEvent.change(date, { target: { value: "2099-01-01" } });
    await userEvent.click(submit);
    const alert = await within(dialog).findByRole("alert");
    expect(alert).toHaveTextContent(message);
    expect(alert.closest("label")).toContainElement(date);
    expect(within(dialog).getAllByRole("alert")).toHaveLength(1);
    expect(body(posts(api)[0]).issued_on).toBe("2099-01-01");
    expect(submit).toBeEnabled();
  });

  it("shows a reference problem beside the reference", async () => {
    const message = "Reference is too long (maximum 120 characters).";
    await openSale({ "POST /api/sale-documents/": () => json({ reference: [message] }, 400) });
    const { dialog } = await openAdd();
    const { type, submit } = pickerOf(dialog);
    await userEvent.selectOptions(type, "Building Permit");
    await chooseIssued(dialog);
    fireEvent.change(within(dialog).getByLabelText("Issue date"), { target: { value: "2026-11-02" } });
    const reference = within(dialog).getByLabelText("Reference (optional)");
    await userEvent.type(reference, "REF");
    await userEvent.click(submit);
    const alert = await within(dialog).findByRole("alert");
    expect(alert).toHaveTextContent(message);
    expect(alert.closest("label")).toContainElement(reference);
  });

  it("shows a refusal that is not about a field in the form-level box", async () => {
    await openSale({
      "POST /api/sale-documents/": () => json({ detail: "This sale is cancelled, so its documents cannot be changed." }, 409),
    });
    const { dialog } = await openAdd();
    const { type, submit } = pickerOf(dialog);
    await userEvent.selectOptions(type, "Building Permit");
    await userEvent.click(submit);
    const alert = await within(dialog).findByRole("alert");
    expect(alert).toHaveTextContent("This sale is cancelled, so its documents cannot be changed.");
    expect(alert.closest("label")).toBeNull();
    expect(alert.closest("fieldset")).toBeNull();
    expect(screen.getByRole("dialog")).toBeInTheDocument();
  });

  it("reloads the section's list, the sale's documents and the catalogue after a 409, and the picker drops a type that is now taken", async () => {
    let docs = saleDocuments;
    const api = await openSale({
      "GET /api/sale-documents/": () => docs,
      "POST /api/sale-documents/": () => {
        docs = { ...saleDocuments, count: 8, results: [...saleDocuments.results, addedBuilding] };   // someone got there first
        return json({ document_type: ["This sale already has this document."] }, 409);
      },
    });
    const { dialog } = await openAdd();
    const { type, submit } = pickerOf(dialog);
    await userEvent.selectOptions(type, "Building Permit");
    const listBefore = sectionListGets(api);
    const documentsBefore = dialogDocumentUrls(api).length;
    const typesBefore = typeUrls(api).length;
    await userEvent.click(submit);
    expect(await within(dialog).findByRole("alert")).toHaveTextContent("This sale already has this document.");
    await waitFor(() => expect(sectionListGets(api)).toBe(listBefore + 1));
    await waitFor(() => expect(dialogDocumentUrls(api)).toHaveLength(documentsBefore + 1));
    await waitFor(() => expect(typeUrls(api)).toHaveLength(typesBefore + 1));
    await waitFor(() => expect(within(type).queryByRole("option", { name: "Building Permit" })).not.toBeInTheDocument());
    expect(type).toHaveValue("");
    expect(submit).toBeDisabled();
  });

  it("explains why the form went away when a 409 shows that nothing is left to add", async () => {
    const asAdded = (n, typeId, name) => ({ ...addedBuilding, id: n, document_type: typeId, name, catalogue_name: name });
    const everything = [
      ...saleDocuments.results,
      asAdded(3008, 50, "Building Permit"), asAdded(3009, 48, "Certificate of Occupancy"), asAdded(3010, 49, "Deed of Sublease"),
    ];
    let docs = saleDocuments;
    await openSale({
      "GET /api/sale-documents/": () => docs,
      "POST /api/sale-documents/": () => {
        docs = { ...saleDocuments, count: everything.length, results: everything };   // the last three were added elsewhere
        return json({ document_type: ["This sale already has this document."] }, 409);
      },
    });
    const { dialog } = await openAdd();
    const { type, submit } = pickerOf(dialog);
    await userEvent.selectOptions(type, "Building Permit");
    await userEvent.click(submit);
    expect(await within(dialog).findByText(`${REFUSED}Every active document type is already on this sale.`)).toBeInTheDocument();
    expect(within(dialog).queryByLabelText("Document type")).not.toBeInTheDocument();
    expect(within(dialog).queryByRole("button", { name: "Add document" })).not.toBeInTheDocument();
    await userEvent.click(within(dialog).getByRole("button", { name: "Cancel" }));
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
  });

  it("shows a catalogue failure after one attempt even when the app retries by default, and the retry button works", async () => {
    let failing = true;
    const { api } = renderSection("manager", {
      extra: { "GET /api/document-types/": () => (failing ? json({ detail: "Something went wrong." }, 500) : documentTypes) },
      queryDefaults: { retry: 3, retryDelay: 1 },                      // the app-wide setting the dialog must override
    });
    await screen.findByText("auth: authenticated");
    const card = (await screen.findByRole("heading", { name: "Sale documents" })).closest("section");
    await within(card).findByText("Contract of Sale");
    await userEvent.click(within(card).getByRole("button", { name: "Add document" }));
    const dialog = await screen.findByRole("dialog", { name: DIALOG });
    expect(await within(dialog).findByRole("alert")).toHaveTextContent("Something went wrong.");
    expect(typeUrls(api)).toHaveLength(1);                             // one attempt, not four
    expect(within(dialog).queryByLabelText("Document type")).not.toBeInTheDocument();
    failing = false;
    await userEvent.click(within(dialog).getByRole("button", { name: "Try again" }));
    expect(await within(dialog).findByRole("option", { name: "Building Permit" })).toBeInTheDocument();
  });

  it("shows a failure to load this sale's documents the same way, and the retry button works", async () => {
    let failing = true;
    await openSale({
      "GET /api/sale-documents/": (url) => (pageOf(url) && failing ? json({ detail: "Could not load documents." }, 500) : saleDocuments),
    });
    const { dialog } = await clickAdd();
    expect(await within(dialog).findByRole("alert")).toHaveTextContent("Could not load documents.");
    expect(within(dialog).queryByLabelText("Document type")).not.toBeInTheDocument();
    failing = false;
    await userEvent.click(within(dialog).getByRole("button", { name: "Try again" }));
    expect(await within(dialog).findByRole("option", { name: "Building Permit" })).toBeInTheDocument();
  });

  it("closes without sending anything on Cancel or Escape", async () => {
    const api = await openSale();
    const first = await openAdd();
    await userEvent.click(within(first.dialog).getByRole("button", { name: "Cancel" }));
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
    await openAdd();
    await userEvent.keyboard("{Escape}");
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
    expect(posts(api)).toHaveLength(0);
  });

  it("sends one request however many times Add document is pressed while it is working", async () => {
    let release;
    const gate = new Promise((resolve) => { release = resolve; });
    const api = await openSale({ "POST /api/sale-documents/": async () => { await gate; return json(addedBuilding, 201); } });
    const { dialog } = await openAdd();
    const { type, submit } = pickerOf(dialog);
    await userEvent.selectOptions(type, "Building Permit");
    await userEvent.click(submit);
    await waitFor(() => expect(submit).toBeDisabled());
    expect(submit).toHaveTextContent("Adding…");
    await userEvent.click(submit);
    release();
    expect(await screen.findByText("Building Permit added to this sale.")).toBeInTheDocument();
    expect(posts(api)).toHaveLength(1);
  });
});
