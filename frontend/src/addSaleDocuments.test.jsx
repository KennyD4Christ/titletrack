import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { MemoryRouter } from "react-router-dom";
import { baseRoutes, fixtures, json, mockApi } from "./test/helpers.jsx";
import { AuthProvider, useAuth } from "./context/AuthContext.jsx";
import AddSaleModal from "./components/sales/AddSaleModal.jsx";
import { AUTOMATIC_NOTE, CHOOSE_HINT } from "./utils/newSaleDocuments.js";
import { todayIso } from "./utils/sales.js";
import { board } from "./test/inventoryHelpers.jsx";

import documentTypes from "./test/fixtures/document_types.json";

// Both real roles hold both document permissions today, so a missing permission is simulated here. The matrix
// itself is not touched: only what the form is told about this person.
const denied = vi.hoisted(() => new Set());
vi.mock("./utils/permissions.js", async (importOriginal) => {
  const real = await importOriginal();
  return { ...real, can: (user, action) => !denied.has(action) && real.can(user, action) };
});

const estate = fixtures.estates.results[0];
const body = (call) => JSON.parse(call.init.body);
const posts = (api) => api.calls.filter((c) => c.method === "POST");
const typeUrls = (api) => api.urls().filter((u) => u.startsWith("/api/document-types/"));
const emptyList = { count: 0, next: null, previous: null, results: [] };

const INACTIVE = "This document type is inactive, so it cannot be added to a sale.";
const BAD_DATE = "Issue date is implausible (before 1990 or in the future).";
const LONG_REFERENCE = "Reference is too long (maximum 120 characters).";
const BASE_BODY = { estate: estate.id, plot_number: "A-1", buyer_name: "Ada Obi", price: "1000", sale_date: todayIso() };

const BP = "Building Permit";              // id 50
const COO = "Certificate of Occupancy";    // id 48
const CONTRACT = "Contract of Sale";       // id 43
const DEED = "Deed of Sublease";           // id 49

function AuthProbe() {
  const { status } = useAuth();
  return <p>{`auth: ${status}`}</p>;
}

/** The modal on its own, as the add-document tests render their dialog. */
const open = async ({ role = "manager", extra = {} } = {}) => {
  document.cookie = "csrftoken=abc; path=/";
  const api = mockApi(baseRoutes(role, {
    "GET /api/document-types/": documentTypes,
    "GET /api/plot-board/": board(role),
    "POST /api/sales-desk/": () => json({ id: 612 }, 201),
    ...extra,
  }));
  const onCreated = vi.fn();
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  render(
    <QueryClientProvider client={client}>
      <MemoryRouter>
        <AuthProvider>
          <AuthProbe />
          <AddSaleModal estates={fixtures.estates.results} onClose={() => {}} onCreated={onCreated} />
        </AuthProvider>
      </MemoryRouter>
    </QueryClientProvider>,
  );
  await screen.findByText("auth: authenticated");
  const dialog = await screen.findByRole("dialog", { name: "Add a sale" });
  return { api, onCreated, dialog };
};

const fillSale = async (dialog) => {
  await userEvent.selectOptions(within(dialog).getByLabelText("Estate"), estate.name);
  await userEvent.selectOptions(
    within(dialog).getByLabelText("Registered available plot"),
    "A-1",
  );
  await userEvent.type(within(dialog).getByLabelText("Buyer name"), "Ada Obi");
  await userEvent.type(within(dialog).getByLabelText("Price"), "1000");
};
const submitOf = (dialog) => within(dialog).getByRole("button", { name: "Add sale" });
const rowOf = (dialog, name) => within(dialog).getByRole("group", { name });
const chooseMode = async (dialog) => {
  await userEvent.click(within(dialog).getByRole("radio", { name: "Choose the documents for this sale" }));
  await within(dialog).findByRole("group", { name: BP });                  // the catalogue has loaded
};
const tick = (dialog, name) => userEvent.click(within(rowOf(dialog, name)).getByRole("checkbox", { name }));
const markIssued = (dialog, name) => userEvent.click(within(rowOf(dialog, name)).getByRole("radio", { name: "Already issued" }));
// A message beside a field sits inside its label and changes the label's text, so the inputs are found by their
// type and placeholder, which an error never changes.
const dateOf = (dialog, name) => rowOf(dialog, name).querySelector('input[type="date"]');
const referenceOf = (dialog, name) => within(rowOf(dialog, name)).getByPlaceholderText("e.g. document or certificate number");
const setDate = (dialog, name, value) => fireEvent.change(dateOf(dialog, name), { target: { value } });

describe("the automatic documents (the default)", () => {
  beforeEach(() => denied.clear());
  afterEach(() => denied.clear());

  it("offers the choice, reads no catalogue, and sends no documents field", async () => {
    const { api, dialog, onCreated } = await open();
    expect(within(dialog).getByRole("radio", { name: "Use the automatic documents" })).toBeChecked();
    expect(within(dialog).getByText(AUTOMATIC_NOTE)).toBeInTheDocument();
    await fillSale(dialog);
    await userEvent.click(submitOf(dialog));
    await waitFor(() => expect(onCreated).toHaveBeenCalledWith({ id: 612 }));
    expect(body(posts(api)[0])).toEqual(BASE_BODY);
    expect(body(posts(api)[0])).not.toHaveProperty("documents");
    expect(typeUrls(api)).toHaveLength(0);
  });

  it("keeps the paid-in-full hint, and swaps it for the right one once documents are chosen", async () => {
    const { dialog } = await open();
    await userEvent.type(within(dialog).getByLabelText("Price"), "1000");
    await userEvent.type(within(dialog).getByLabelText("Amount paid so far (optional)"), "1000");
    const paid = "Paid in full: the documents due on full payment are created straight away.";
    expect(within(dialog).getByText(paid)).toBeInTheDocument();
    await chooseMode(dialog);
    expect(within(dialog).queryByText(paid)).not.toBeInTheDocument();
    expect(within(dialog).getByText(CHOOSE_HINT)).toBeInTheDocument();
  });

  it("does not offer the choice to a person without manage_sale_documents, and adds the sale as before", async () => {
    denied.add("manage_sale_documents");
    const { api, dialog, onCreated } = await open();
    expect(within(dialog).queryByRole("group", { name: "Documents" })).not.toBeInTheDocument();
    expect(within(dialog).queryByRole("radio", { name: /documents/i })).not.toBeInTheDocument();
    await fillSale(dialog);
    await userEvent.click(submitOf(dialog));
    await waitFor(() => expect(onCreated).toHaveBeenCalled());
    expect(body(posts(api)[0])).toEqual(BASE_BODY);
    expect(typeUrls(api)).toHaveLength(0);
  });
});

describe("choosing the documents", () => {
  beforeEach(() => denied.clear());
  afterEach(() => denied.clear());

  it("reads the active catalogue once, only when asked, and lists the active types by name", async () => {
    const { api, dialog } = await open();
    expect(typeUrls(api)).toHaveLength(0);
    await chooseMode(dialog);
    expect(typeUrls(api)).toEqual(["/api/document-types/?is_active=1&page=1&page_size=200"]);
    const names = within(within(dialog).getByRole("group", { name: "Documents" }))
      .getAllByRole("checkbox").map((box) => box.closest("label").textContent);
    expect(names).toHaveLength(10);
    expect(names[0]).toBe(BP);
    expect(names).toEqual([...names].sort((a, b) => a.localeCompare(b)));
    expect(names).not.toContain("Old Transfer Form");
  });

  it("needs at least one document, and says so while the button is off", async () => {
    const { dialog } = await open();
    await fillSale(dialog);
    expect(submitOf(dialog)).toBeEnabled();
    await chooseMode(dialog);
    expect(submitOf(dialog)).toBeDisabled();
    expect(within(dialog).getByText("Choose at least one document, or use the automatic documents.")).toBeInTheDocument();
    await tick(dialog, BP);
    expect(submitOf(dialog)).toBeEnabled();
    expect(within(dialog).queryByText(/Choose at least one document/)).not.toBeInTheDocument();
  });

  it("sends the chosen documents in the order the types are listed, with issue details only where issued", async () => {
    const { api, dialog, onCreated } = await open();
    await fillSale(dialog);
    await chooseMode(dialog);
    await tick(dialog, DEED);                       // ticked in the opposite order to the list
    await tick(dialog, COO);
    await tick(dialog, BP);
    await markIssued(dialog, COO);
    setDate(dialog, COO, "2026-11-02");
    await userEvent.type(within(rowOf(dialog, COO)).getByLabelText("Reference (optional)"), "  CofO-77 ");
    await userEvent.click(submitOf(dialog));
    await waitFor(() => expect(onCreated).toHaveBeenCalledWith({ id: 612 }));
    expect(body(posts(api)[0])).toEqual({
      ...BASE_BODY,
      documents: [
        { document_type: 50, state: "owed" },
        { document_type: 48, state: "issued", issued_on: "2026-11-02", reference: "CofO-77" },
        { document_type: 49, state: "owed" },
      ],
    });
  });

  it("needs the issue date of an issued document, and can fill in today's date", async () => {
    const { dialog } = await open();
    await fillSale(dialog);
    await chooseMode(dialog);
    await tick(dialog, BP);
    await markIssued(dialog, BP);
    const date = within(rowOf(dialog, BP)).getByLabelText("Issue date");
    expect(date).toHaveValue("");
    expect(submitOf(dialog)).toBeDisabled();
    expect(within(dialog).getByText("Enter the issue date of every document marked as already issued.")).toBeInTheDocument();
    await userEvent.click(within(rowOf(dialog, BP)).getByRole("button", { name: "Use today's date" }));
    expect(date).toHaveValue(todayIso());
    expect(submitOf(dialog)).toBeEnabled();
  });

  it("drops the issue details when a document goes back to still owed", async () => {
    const { api, dialog } = await open();
    await fillSale(dialog);
    await chooseMode(dialog);
    await tick(dialog, BP);
    await markIssued(dialog, BP);
    setDate(dialog, BP, "2026-11-02");
    await userEvent.type(within(rowOf(dialog, BP)).getByLabelText("Reference (optional)"), "X");
    await userEvent.click(within(rowOf(dialog, BP)).getByRole("radio", { name: "Still owed" }));
    expect(within(rowOf(dialog, BP)).queryByLabelText("Issue date")).not.toBeInTheDocument();
    await userEvent.click(submitOf(dialog));
    await waitFor(() => expect(posts(api)).toHaveLength(1));
    expect(body(posts(api)[0]).documents).toEqual([{ document_type: 50, state: "owed" }]);
  });

  it("sends only what is still ticked", async () => {
    const { api, dialog } = await open();
    await fillSale(dialog);
    await chooseMode(dialog);
    await tick(dialog, BP);
    await tick(dialog, CONTRACT);
    await tick(dialog, CONTRACT);                   // ticked, then unticked again
    await userEvent.click(submitOf(dialog));
    await waitFor(() => expect(posts(api)).toHaveLength(1));
    expect(body(posts(api)[0]).documents).toEqual([{ document_type: 50, state: "owed" }]);
  });

  it("sends no documents field after going back to the automatic documents, and remembers the ticks", async () => {
    const { api, dialog } = await open();
    await fillSale(dialog);
    await chooseMode(dialog);
    await tick(dialog, BP);
    await userEvent.click(within(dialog).getByRole("radio", { name: "Use the automatic documents" }));
    expect(submitOf(dialog)).toBeEnabled();
    await userEvent.click(submitOf(dialog));
    await waitFor(() => expect(posts(api)).toHaveLength(1));
    expect(body(posts(api)[0])).toEqual(BASE_BODY);
  });

  it("keeps the ticks when the person goes back to choosing", async () => {
    const { dialog } = await open();
    await chooseMode(dialog);
    await tick(dialog, BP);
    await userEvent.click(within(dialog).getByRole("radio", { name: "Use the automatic documents" }));
    await userEvent.click(within(dialog).getByRole("radio", { name: "Choose the documents for this sale" }));
    expect(await within(rowOf(dialog, BP)).findByRole("checkbox", { name: BP })).toBeChecked();
  });

  it("offers only still owed to a person without issue_document, and sends it that way", async () => {
    denied.add("issue_document");
    const { api, dialog } = await open();
    await fillSale(dialog);
    await chooseMode(dialog);
    await tick(dialog, BP);
    expect(within(rowOf(dialog, BP)).queryByRole("radio", { name: "Already issued" })).not.toBeInTheDocument();
    expect(within(rowOf(dialog, BP)).getByText("This document will be recorded as still owed.")).toBeInTheDocument();
    await userEvent.click(submitOf(dialog));
    await waitFor(() => expect(posts(api)).toHaveLength(1));
    expect(body(posts(api)[0]).documents).toEqual([{ document_type: 50, state: "owed" }]);
  });

  it("explains a catalogue that cannot be loaded, offers Try again, and still lets the person use the automatic documents", async () => {
    let failing = true;
    const { api, dialog } = await open({
      extra: { "GET /api/document-types/": () => (failing ? json({ detail: "Something went wrong." }, 500) : documentTypes) },
    });
    await fillSale(dialog);
    await userEvent.click(within(dialog).getByRole("radio", { name: "Choose the documents for this sale" }));
    const alert = await within(dialog).findByText("Something went wrong.");
    expect(alert.closest("[role=alert]")).toBeInTheDocument();
    expect(typeUrls(api)).toHaveLength(1);                                  // one attempt, no automatic retries
    expect(submitOf(dialog)).toBeDisabled();
    expect(within(dialog).getByText(/could not be loaded/)).toBeInTheDocument();
    failing = false;
    await userEvent.click(within(dialog).getByRole("button", { name: "Try again" }));
    expect(await within(dialog).findByRole("group", { name: BP })).toBeInTheDocument();
    await userEvent.click(within(dialog).getByRole("radio", { name: "Use the automatic documents" }));
    expect(submitOf(dialog)).toBeEnabled();
  });

  it("says so when the catalogue has no active types", async () => {
    const { dialog } = await open({ extra: { "GET /api/document-types/": emptyList } });
    await fillSale(dialog);
    await userEvent.click(within(dialog).getByRole("radio", { name: "Choose the documents for this sale" }));
    expect(await within(dialog).findByText("There are no active document types in the catalogue.")).toBeInTheDocument();
    expect(submitOf(dialog)).toBeDisabled();
  });

  it("says when the list may be incomplete", async () => {
    const { dialog } = await open({ extra: { "GET /api/document-types/": { ...documentTypes, count: 250 } } });
    await chooseMode(dialog);
    expect(within(dialog).getByText("Some document types could not be loaded, so this list may be incomplete.")).toBeInTheDocument();
  });
});

describe("what the server says about the documents", () => {
  beforeEach(() => denied.clear());
  afterEach(() => denied.clear());

  const choose = async (extra) => {
    const opened = await open({ extra });
    await fillSale(opened.dialog);
    await chooseMode(opened.dialog);
    return opened;
  };
  const alertsIn = (element) => within(element).queryAllByRole("alert");

  it("shows an inactive type beside its own document, keeps everything ticked, and no general banner", async () => {
    const { dialog } = await choose({
      "POST /api/sales-desk/": () => json({ documents: { 1: { document_type: [INACTIVE] } } }, 409),
    });
    await tick(dialog, BP);
    await tick(dialog, COO);
    await userEvent.click(submitOf(dialog));
    const message = await within(rowOf(dialog, COO)).findByText(INACTIVE);
    expect(message).toHaveAttribute("role", "alert");
    expect(alertsIn(rowOf(dialog, BP))).toHaveLength(0);
    expect(within(dialog).getAllByRole("alert")).toHaveLength(1);              // no "Request failed" banner as well
    expect(dialog.querySelector(".form-error")).toBeNull();
    expect(within(rowOf(dialog, BP)).getByRole("checkbox", { name: BP })).toBeChecked();
    expect(within(rowOf(dialog, COO)).getByRole("checkbox", { name: COO })).toBeChecked();
    expect(submitOf(dialog)).toBeEnabled();
  });

  it("shows each problem beside the control it concerns, at the right document", async () => {
    const { dialog } = await choose({
      "POST /api/sales-desk/": () => json({
        documents: { 0: { issued_on: [BAD_DATE] }, 2: { reference: [LONG_REFERENCE] } },
      }, 400),
    });
    await tick(dialog, BP);                                                     // position 0: issued
    await markIssued(dialog, BP);
    setDate(dialog, BP, "2099-01-01");
    await tick(dialog, CONTRACT);                                               // position 1: fine
    await tick(dialog, DEED);                                                   // position 2: issued
    await markIssued(dialog, DEED);
    setDate(dialog, DEED, "2026-11-02");
    await userEvent.click(submitOf(dialog));
    const dateMessage = await within(rowOf(dialog, BP)).findByText(BAD_DATE);
    expect(dateMessage.closest("label")).toContainElement(dateOf(dialog, BP));
    const referenceMessage = within(rowOf(dialog, DEED)).getByText(LONG_REFERENCE);
    expect(referenceMessage.closest("label")).toContainElement(referenceOf(dialog, DEED));
    expect(alertsIn(rowOf(dialog, CONTRACT))).toHaveLength(0);
    expect(dialog.querySelector(".form-error")).toBeNull();
  });

  it("clears only the message of the document the person changes", async () => {
    const { dialog } = await choose({
      "POST /api/sales-desk/": () => json({
        documents: { 0: { issued_on: [BAD_DATE] }, 1: { reference: [LONG_REFERENCE] } },
      }, 400),
    });
    await tick(dialog, BP);
    await markIssued(dialog, BP);
    setDate(dialog, BP, "2099-01-01");
    await tick(dialog, DEED);
    await markIssued(dialog, DEED);
    setDate(dialog, DEED, "2026-11-02");
    await userEvent.click(submitOf(dialog));
    await within(rowOf(dialog, BP)).findByText(BAD_DATE);
    setDate(dialog, BP, "2026-11-01");
    expect(within(rowOf(dialog, BP)).queryByText(BAD_DATE)).not.toBeInTheDocument();
    expect(within(rowOf(dialog, DEED)).getByText(LONG_REFERENCE)).toBeInTheDocument();
  });

  it("shows a problem with the list as a whole in the documents block, not as a banner", async () => {
    const message = "Ensure this field has no more than 50 elements.";
    const { dialog } = await choose({
      "POST /api/sales-desk/": () => json({ documents: { non_field_errors: [message] } }, 400),
    });
    await tick(dialog, BP);
    await userEvent.click(submitOf(dialog));
    const shown = await within(dialog).findByText(message);
    expect(shown.closest("fieldset")).toContainElement(within(dialog).getByRole("radio", { name: "Use the automatic documents" }));
    expect(dialog.querySelector(".form-error")).toBeNull();
  });

  it("uses the usual banner for a refusal that is not about a document, such as a missing permission", async () => {
    const detail = "You do not have permission to add documents to a sale.";
    const { dialog } = await choose({ "POST /api/sales-desk/": () => json({ detail }, 403) });
    await tick(dialog, BP);
    await userEvent.click(submitOf(dialog));
    const banner = await within(dialog).findByText(detail);
    expect(banner.closest(".form-error")).not.toBeNull();
    expect(alertsIn(rowOf(dialog, BP))).toHaveLength(0);
    expect(submitOf(dialog)).toBeEnabled();
  });

  it("names the current buyer when the plot is already sold, and keeps the chosen documents", async () => {
    const { dialog } = await choose({
      "POST /api/sales-desk/": () => json({
        detail: "Plot A-9 in Green Valley Estate already has an active sale to Bayo Lawal.",
        existing_sale: { id: 612, buyer: 701, buyer_name: "Bayo Lawal" },
      }, 409),
    });
    await tick(dialog, BP);
    await userEvent.click(submitOf(dialog));
    expect(await within(dialog).findByText(/already has an active sale to Bayo Lawal/)).toBeInTheDocument();
    expect(within(dialog).getByRole("link", { name: "View that sale" })).toHaveAttribute("href", "/sales/612");
    expect(within(rowOf(dialog, BP)).getByRole("checkbox", { name: BP })).toBeChecked();
  });

  it("can be retried after a refusal: the same documents are sent again and the old message goes", async () => {
    let attempt = 0;
    const { api, dialog, onCreated } = await choose({
      "POST /api/sales-desk/": () => (++attempt === 1
        ? json({ documents: { 0: { document_type: [INACTIVE] } } }, 409)
        : json({ id: 612 }, 201)),
    });
    await tick(dialog, BP);
    await userEvent.click(submitOf(dialog));
    await within(rowOf(dialog, BP)).findByText(INACTIVE);
    await userEvent.click(submitOf(dialog));
    await waitFor(() => expect(onCreated).toHaveBeenCalledWith({ id: 612 }));
    const [first, second] = posts(api);
    expect(body(second)).toEqual(body(first));
    expect(body(second).documents).toEqual([{ document_type: 50, state: "owed" }]);
  });
});
