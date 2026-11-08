import { describe, expect, it } from "vitest";
import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { baseRoutes, json, mockApi, renderApp } from "./test/helpers.jsx";

import saleDetail from "./test/fixtures/sale_detail.json";
import saleDocuments from "./test/fixtures/sale_documents.json";

const id = saleDetail.id;
const emptyList = { count: 0, next: null, previous: null, results: [] };
const FOLLOWING = 4; // Node.DOCUMENT_POSITION_FOLLOWING

const routes = (extra = {}) =>
  baseRoutes("manager", {
    [`GET /api/sales-desk/${id}/`]: saleDetail,
    "GET /api/sale-documents/": saleDocuments,
    ...extra,
  });

const openSale = async (extra) => {
  const api = mockApi(routes(extra));
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

describe("sale documents section", () => {
  it("asks for this sale's documents only and sits above the existing obligations table", async () => {
    const api = await openSale();
    const card = await loaded();
    expect(within(card).queryByText(
      "This sale is cancelled. These documents are retained for audit purposes."
    )).not.toBeInTheDocument();
    expect(api.urls()).toContain(`/api/sale-documents/?sale=${id}&page_size=200`);
    const owed = screen.getByRole("heading", { name: "Documents owed" }).closest("section");
    expect(card.compareDocumentPosition(owed) & FOLLOWING).toBeTruthy();
    expect(within(owed).getByRole("link", { name: "Allocation Letter" })).toHaveAttribute("href", "/obligations/1901");
  });

  it("keeps the document's status apart from the status of its preparation work", async () => {
    await openSale();
    const card = await loaded();
    const ready = rowOf(card, "Contract of Sale");
    expect(within(ready).getByText("Ready to issue")).toHaveClass("badge-doc-ready_to_issue");
    expect(within(ready).getByText("Completed")).toBeInTheDocument();          // the obligation, not the document
    expect(within(ready).queryByText("Issued")).not.toBeInTheDocument();
    const deed = rowOf(card, "Deed of Assignment");
    const labels = within(deed).getAllByText("In progress");                    // one for each status
    expect(labels).toHaveLength(2);
    expect(labels.some((el) => el.classList.contains("badge-doc-in_progress"))).toBe(true);
    expect(labels.some((el) => el.classList.contains("badge-in_progress"))).toBe(true);
  });

  it("flags only the overdue document, and shows its due date in red", async () => {
    await openSale();
    const card = await loaded();
    const flags = within(card).getAllByText("Overdue");
    expect(flags).toHaveLength(1);
    const row = rowOf(card, "Layout Approval");
    expect(within(row).getByText("Overdue")).toBe(flags[0]);
    expect(within(row).getByText("15 Oct 2026")).toHaveClass("text-danger");
    expect(within(rowOf(card, "Survey Plan")).getByText("15 Dec 2026")).not.toHaveClass("text-danger");
  });

  it("shows an issued document's date and reference, and no preparation work when it has none", async () => {
    await openSale();
    const card = await loaded();
    const row = rowOf(card, "Power of Attorney");
    expect(within(row).getByText("Issued")).toHaveClass("badge-doc-issued");
    expect(within(row).getByText("Issued 20 Oct 2026 · REF-1042")).toBeInTheDocument();
    expect(within(row).queryByRole("link")).not.toBeInTheDocument();
    expect(within(row).queryByText("Unassigned")).not.toBeInTheDocument();
    expect(within(row).getAllByText("—")).toHaveLength(3);                      // preparation, owner, due
  });

  it("shows why a document is not required", async () => {
    await openSale();
    const card = await loaded();
    const row = rowOf(card, "Site Plan");
    expect(within(row).getByText("Not required")).toHaveClass("badge-doc-not_required");
    expect(within(row).getByText("Reason: Buyer already holds the original")).toBeInTheDocument();
    expect(within(row).getByRole("link", { name: "Not applicable" })).toHaveAttribute("href", "/obligations/2005");
    expect(within(row).queryByText("Unassigned")).not.toBeInTheDocument();
  });

  it("marks a document that needs review, without calling its missing owner unassigned", async () => {
    await openSale();
    const card = await loaded();
    const row = rowOf(card, "Tax Clearance Certificate");
    expect(within(row).getByText("Needs review")).toHaveClass("badge-doc-needs_review");
    expect(within(row).getByRole("link", { name: "Not applicable" })).toBeInTheDocument();
    expect(within(row).queryByText("Unassigned")).not.toBeInTheDocument();
  });

  it("notes a catalogue entry that has been renamed since the document was added", async () => {
    await openSale();
    const card = await loaded();
    expect(within(rowOf(card, "Survey Plan")).getByText("Now called Site Survey Plan in the catalogue")).toBeInTheDocument();
    expect(within(rowOf(card, "Contract of Sale")).queryByText(/Now called/)).not.toBeInTheDocument();
  });

  it("shows the owner, or Unassigned where open work has none", async () => {
    await openSale();
    const card = await loaded();
    expect(within(rowOf(card, "Deed of Assignment")).getByText("Ifeanyi Obi")).toBeInTheDocument();
    expect(within(rowOf(card, "Survey Plan")).getByText("Unassigned")).toBeInTheDocument();
    expect(within(rowOf(card, "Layout Approval")).getByText("Unassigned")).toBeInTheDocument();
  });

  it("links the preparation status to the existing obligation page", async () => {
    await openSale();
    const card = await loaded();
    const row = rowOf(card, "Survey Plan");
    expect(within(row).getByRole("link", { name: "Not started" })).toHaveAttribute("href", "/obligations/2001");
  });

  it("offers Issue, Not applicable and Correct issuance only where the API allows them, and no other action", async () => {
    await openSale();
    const card = await loaded();
    const issuable = saleDocuments.results.filter((d) => d.can.issue).map((d) => `Issue ${d.name}`);
    const skippable = saleDocuments.results
      .filter((d) => d.can.mark_not_applicable)
      .map((d) => `Mark ${d.name} not applicable`);
    expect(issuable).toHaveLength(4);
    expect(skippable).toHaveLength(4);
    expect(within(card).getByRole("button", { name: "Add document" })).toBeInTheDocument();   // the section's own button
    const labels = within(card).getAllByRole("button")
      .filter((b) => b.getAttribute("aria-label"))                                            // every row action has an aria-label
      .map((b) => b.getAttribute("aria-label"));
    const correctable = saleDocuments.results
      .filter((d) => d.can.correct_issuance)
      .map((d) => `Correct issuance of ${d.name}`);
    expect(correctable).toEqual(["Correct issuance of Power of Attorney"]);
    expect(labels.sort()).toEqual([...issuable, ...skippable, ...correctable].sort());
    expect(within(rowOf(card, "Power of Attorney")).getAllByRole("button")).toHaveLength(1);
    expect(within(rowOf(card, "Site Plan")).queryByRole("button")).not.toBeInTheDocument();
    expect(within(rowOf(card, "Tax Clearance Certificate")).queryByRole("button")).not.toBeInTheDocument();
  });

  it("says so when the sale has no catalogue documents, and leaves the obligations table alone", async () => {
    await openSale({ "GET /api/sale-documents/": emptyList });
    expect(await screen.findByText("No catalogue documents have been added to this sale yet.")).toBeInTheDocument();
    const card = screen.getByRole("heading", { name: "Sale documents" }).closest("section");
    expect(card.querySelector("table")).toBeNull();
    expect(screen.getByRole("heading", { name: "Documents owed" })).toBeInTheDocument();
  });

  it("still lists a cancelled sale's documents as the record, with nothing overdue and no actions", async () => {
    const cancelledSale = {
      ...saleDetail, status: "cancelled", cancel_reason: "Buyer defaulted",
      can: { record_payment: false, reverse_payment: false, edit_buyer: true, cancel: false, reinstate: true },
    };
    // What the API returns for a cancelled sale's documents: still listed when the sale is asked for by id,
    // nothing overdue, no action allowed (get_is_overdue and get_can in api/serializers/sale_documents.py).
    const asCancelled = {
      ...saleDocuments,
      results: saleDocuments.results.map((d) => ({
        ...d, sale_status: "cancelled", is_overdue: false,
        can: { issue: false, mark_not_applicable: false, correct_issuance: false },
      })),
    };
    const api = await openSale({
      [`GET /api/sales-desk/${id}/`]: cancelledSale,
      "GET /api/sale-documents/": asCancelled,
    });
    expect(screen.getByText(/This sale was cancelled: Buyer defaulted/)).toBeInTheDocument();
    const card = await loaded();
    expect(within(card).getByText(
      "This sale is cancelled. These documents are retained for audit purposes."
    )).toBeInTheDocument();
    expect(api.urls()).toContain(`/api/sale-documents/?sale=${id}&page_size=200`);
    expect(api.urls().some((u) => u.includes("include_cancelled"))).toBe(false);
    expect(within(card).getAllByRole("row")).toHaveLength(asCancelled.results.length + 1);   // header + one per document
    expect(within(card).queryByText("Overdue")).not.toBeInTheDocument();
    expect(within(card).queryByText("No catalogue documents have been added to this sale yet.")).not.toBeInTheDocument();
    expect(within(card).queryAllByRole("button")).toHaveLength(0);
  });

  it("shows a failure inside its own card, leaves the rest of the sale page working, and can retry", async () => {
    let attempt = 0;
    await openSale({
      "GET /api/sale-documents/": () => (++attempt === 1 ? json({ detail: "Something went wrong." }, 500) : saleDocuments),
    });
    const card = (await screen.findByRole("heading", { name: "Sale documents" })).closest("section");
    expect(await within(card).findByRole("alert")).toHaveTextContent("Something went wrong.");
    expect(screen.getByRole("heading", { name: "The money" })).toBeInTheDocument();
    expect(screen.getByRole("heading", { name: "Documents owed" })).toBeInTheDocument();
    await userEvent.click(within(card).getByRole("button", { name: "Try again" }));
    expect(await within(card).findByText("Contract of Sale")).toBeInTheDocument();
  });

  it("says when there are more documents than the page returned", async () => {
    await openSale({ "GET /api/sale-documents/": { ...saleDocuments, count: 250 } });
    await loaded();
    expect(screen.getByText("Showing 7 of 250 documents.")).toBeInTheDocument();
  });

  it("reloads together with the rest of the sale page after a change", async () => {
    document.cookie = "csrftoken=abc; path=/";
    const api = await openSale({ [`POST /api/sales-desk/${id}/edit-buyer/`]: () => json(saleDetail) });
    await loaded();
    const documentCalls = () => api.urls().filter((u) => u.startsWith("/api/sale-documents/")).length;
    const before = documentCalls();
    expect(before).toBe(1);
    await userEvent.click(screen.getByRole("button", { name: "Edit buyer" }));
    const dialog = await screen.findByRole("dialog", { name: "Edit buyer details" });
    const phone = within(dialog).getByLabelText("Phone");
    await userEvent.clear(phone);
    await userEvent.type(phone, "0803 999 0000");
    await userEvent.click(within(dialog).getByRole("button", { name: "Save changes" }));
    expect(await screen.findByText("Buyer details saved.")).toBeInTheDocument();
    await waitFor(() => expect(documentCalls()).toBe(before + 1));
  });
});
