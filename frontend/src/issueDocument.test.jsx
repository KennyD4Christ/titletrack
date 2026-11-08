import { beforeEach, describe, expect, it } from "vitest";
import { fireEvent, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { baseRoutes, json, mockApi, renderApp } from "./test/helpers.jsx";
import { todayIso } from "./utils/sales.js";

import saleDetail from "./test/fixtures/sale_detail.json";
import saleDocuments from "./test/fixtures/sale_documents.json";

const id = saleDetail.id;
const body = (call) => JSON.parse(call.init.body);
const issueUrl = (docId) => `/api/sale-documents/${docId}/issue/`;
const posts = (api) => api.calls.filter((c) => c.method === "POST");

const COMPLETES = "If you issue this document, its open preparation work will also be marked as completed.";
const CORRECTION = "If the issuance details need correction later, an authorised user can correct them with a reason.";

// Contract of Sale (3003) is "ready to issue" in the fixture; this is what the API returns once it is issued.
const contract = saleDocuments.results.find((d) => d.name === "Contract of Sale");
const issuedContract = {
  ...contract, state: "issued", display_status: "issued", issued_on: "2026-11-02", reference: "TRF 1",
  issued_by_name: "Emeka Okafor", issued_at: "2026-11-03T10:00:00+01:00",
  can: { issue: false, mark_not_applicable: false, correct_issuance: true },
};

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

const openIssue = async (name) => {
  const card = await loaded();
  await userEvent.click(within(card).getByRole("button", { name: `Issue ${name}` }));
  const dialog = await screen.findByRole("dialog", { name: "Issue document" });
  return { card, dialog };
};

const documentGets = (api) => api.urls().filter((u) => u.startsWith("/api/sale-documents/?")).length;

describe("issue a document", () => {
  beforeEach(() => {
    document.cookie = "csrftoken=abc; path=/";
  });

  it("opens for the chosen document with the date empty and Confirm switched off", async () => {
    await openSale();
    const { dialog } = await openIssue("Survey Plan");
    expect(within(dialog).getByText("Survey Plan")).toBeInTheDocument();
    expect(within(dialog).getByLabelText("Issue date")).toHaveValue("");
    expect(within(dialog).getByLabelText("Reference (optional)")).toHaveValue("");
    expect(within(dialog).getByRole("button", { name: "Confirm issuance" })).toBeDisabled();
  });

  it("fills in today's date on request, and offers no later date in the picker", async () => {
    await openSale();
    const { dialog } = await openIssue("Survey Plan");
    const date = within(dialog).getByLabelText("Issue date");
    expect(date).toHaveAttribute("max", todayIso());
    await userEvent.click(within(dialog).getByRole("button", { name: "Use today's date" }));
    expect(date).toHaveValue(todayIso());
    expect(within(dialog).getByRole("button", { name: "Confirm issuance" })).toBeEnabled();
  });

  it("sends the date alone when no reference is typed", async () => {
    const api = await openSale({ [`POST ${issueUrl(3003)}`]: () => json(issuedContract) });
    const { dialog } = await openIssue("Contract of Sale");
    fireEvent.change(within(dialog).getByLabelText("Issue date"), { target: { value: "2026-11-02" } });
    await userEvent.click(within(dialog).getByRole("button", { name: "Confirm issuance" }));
    await screen.findByText(/marked as issued/);
    const [call] = posts(api);
    expect(call.url).toBe(issueUrl(3003));
    expect(body(call)).toEqual({ issued_on: "2026-11-02" });
  });

  it("sends the reference, trimmed, when one is typed", async () => {
    const api = await openSale({ [`POST ${issueUrl(3003)}`]: () => json(issuedContract) });
    const { dialog } = await openIssue("Contract of Sale");
    fireEvent.change(within(dialog).getByLabelText("Issue date"), { target: { value: "2026-11-02" } });
    await userEvent.type(within(dialog).getByLabelText("Reference (optional)"), "  TRF-77 ");
    await userEvent.click(within(dialog).getByRole("button", { name: "Confirm issuance" }));
    await screen.findByText(/marked as issued/);
    expect(body(posts(api)[0])).toEqual({ issued_on: "2026-11-02", reference: "TRF-77" });
  });

  it("records the issuance, closes, says what happened, and reloads the documents and the sale", async () => {
    let current = saleDocuments;
    const api = await openSale({
      "GET /api/sale-documents/": () => current,
      [`POST ${issueUrl(3003)}`]: () => {
        current = { ...saleDocuments, results: saleDocuments.results.map((d) => (d.id === 3003 ? issuedContract : d)) };
        return json(issuedContract);
      },
    });
    const { card, dialog } = await openIssue("Contract of Sale");
    const saleGets = () => api.calls.filter((c) => c.method === "GET" && c.url === `/api/sales-desk/${id}/`).length;
    const documentsBefore = documentGets(api);
    const saleBefore = saleGets();
    fireEvent.change(within(dialog).getByLabelText("Issue date"), { target: { value: "2026-11-02" } });
    await userEvent.type(within(dialog).getByLabelText("Reference (optional)"), "TRF 1");
    await userEvent.click(within(dialog).getByRole("button", { name: "Confirm issuance" }));
    expect(await within(card).findByText("Contract of Sale marked as issued on 2 Nov 2026.")).toBeInTheDocument();
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
    await waitFor(() => expect(documentGets(api)).toBe(documentsBefore + 1));
    await waitFor(() => expect(saleGets()).toBe(saleBefore + 1));
    await waitFor(() => expect(within(rowOf(card, "Contract of Sale")).getByText("Issued 2 Nov 2026 · TRF 1")).toBeInTheDocument());
    // Issued now: Issue and Not applicable are gone, and only the correction action remains (can.correct_issuance).
    expect(within(rowOf(card, "Contract of Sale")).getAllByRole("button").map((b) => b.getAttribute("aria-label")))
      .toEqual(["Correct issuance of Contract of Sale"]);
  });

  it("still sends a date the picker would not offer, and shows the server's own message beside the field", async () => {
    // Wording follows _checked_date in tracker/services.py; the earliest year is not asserted here.
    const message = "Issue date is implausible (before the earliest accepted year or in the future).";
    const api = await openSale({ [`POST ${issueUrl(3001)}`]: () => json({ issued_on: [message] }, 400) });
    const { dialog } = await openIssue("Survey Plan");
    const date = within(dialog).getByLabelText("Issue date");
    fireEvent.change(date, { target: { value: "2099-01-01" } });
    const confirm = within(dialog).getByRole("button", { name: "Confirm issuance" });
    await userEvent.click(confirm);
    const alert = await within(dialog).findByRole("alert");
    expect(alert).toHaveTextContent(message);
    expect(alert.closest("label")).toContainElement(date);
    expect(within(dialog).getAllByRole("alert")).toHaveLength(1);        // beside the field only, not repeated above
    expect(body(posts(api)[0])).toEqual({ issued_on: "2099-01-01" });
    expect(screen.getByRole("dialog")).toBeInTheDocument();
    expect(confirm).toBeEnabled();
  });

  it("shows a reference problem beside the reference field", async () => {
    const message = "Reference is too long (maximum 120 characters).";
    await openSale({ [`POST ${issueUrl(3001)}`]: () => json({ reference: [message] }, 400) });
    const { dialog } = await openIssue("Survey Plan");
    const reference = within(dialog).getByLabelText("Reference (optional)");
    fireEvent.change(within(dialog).getByLabelText("Issue date"), { target: { value: "2026-11-02" } });
    await userEvent.type(reference, "REF");
    await userEvent.click(within(dialog).getByRole("button", { name: "Confirm issuance" }));
    const alert = await within(dialog).findByRole("alert");
    expect(alert).toHaveTextContent(message);
    expect(alert.closest("label")).toContainElement(reference);
  });

  it("explains a refusal that is not about a field, and reloads the documents in case someone else changed it", async () => {
    const api = await openSale({
      [`POST ${issueUrl(3001)}`]: () => json({ detail: "This document has already been issued." }, 409),
    });
    const { dialog } = await openIssue("Survey Plan");
    const before = documentGets(api);
    fireEvent.change(within(dialog).getByLabelText("Issue date"), { target: { value: "2026-11-02" } });
    const confirm = within(dialog).getByRole("button", { name: "Confirm issuance" });
    await userEvent.click(confirm);
    expect(await within(dialog).findByRole("alert")).toHaveTextContent("This document has already been issued.");
    await waitFor(() => expect(documentGets(api)).toBe(before + 1));
    expect(screen.getByRole("dialog")).toBeInTheDocument();
    expect(confirm).toBeEnabled();
  });

  it("says the open preparation work will be completed only when there is open work", async () => {
    await openSale();
    for (const name of ["Survey Plan", "Deed of Assignment"]) {                 // outstanding, in progress
      const { dialog } = await openIssue(name);
      expect(within(dialog).getByText(`${COMPLETES} ${CORRECTION}`)).toBeInTheDocument();
      await userEvent.click(within(dialog).getByRole("button", { name: "Cancel" }));
    }
    const { dialog } = await openIssue("Contract of Sale");                      // ready to issue: work already complete
    expect(within(dialog).getByText(CORRECTION)).toBeInTheDocument();
    expect(within(dialog).queryByText(/open preparation work/)).not.toBeInTheDocument();
  });

  it("closes without sending anything on Cancel or Escape", async () => {
    const api = await openSale();
    const first = await openIssue("Survey Plan");
    await userEvent.click(within(first.dialog).getByRole("button", { name: "Cancel" }));
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
    await openIssue("Survey Plan");
    await userEvent.keyboard("{Escape}");
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
    expect(posts(api)).toHaveLength(0);
  });

  it("sends one request however many times Confirm is pressed while it is working", async () => {
    let release;
    const gate = new Promise((resolve) => { release = resolve; });
    const api = await openSale({
      [`POST ${issueUrl(3003)}`]: async () => { await gate; return json(issuedContract); },
    });
    const { dialog } = await openIssue("Contract of Sale");
    fireEvent.change(within(dialog).getByLabelText("Issue date"), { target: { value: "2026-11-02" } });
    const confirm = within(dialog).getByRole("button", { name: "Confirm issuance" });
    await userEvent.click(confirm);
    await waitFor(() => expect(confirm).toBeDisabled());
    expect(confirm).toHaveTextContent("Recording…");
    await userEvent.click(confirm);
    release();
    expect(await screen.findByText("Contract of Sale marked as issued on 2 Nov 2026.")).toBeInTheDocument();
    expect(posts(api)).toHaveLength(1);
  });
});
