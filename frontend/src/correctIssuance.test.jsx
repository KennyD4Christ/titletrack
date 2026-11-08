import { beforeEach, describe, expect, it } from "vitest";
import { fireEvent, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { baseRoutes, json, mockApi, renderApp } from "./test/helpers.jsx";
import { todayIso } from "./utils/sales.js";

import saleDetail from "./test/fixtures/sale_detail.json";
import saleDocuments from "./test/fixtures/sale_documents.json";

const id = saleDetail.id;
const body = (call) => JSON.parse(call.init.body);
const posts = (api) => api.calls.filter((c) => c.method === "POST");

// Power of Attorney (3004) is issued 20 Oct 2026 with reference REF-1042 in the fixture, and is the only document
// whose can.correct_issuance is true.
const poa = saleDocuments.results.find((d) => d.name === "Power of Attorney");
const url = `/api/sale-documents/${poa.id}/correct-issuance/`;
const correctedPoa = { ...poa, issued_on: "2026-10-18", reference: "REF-2001" };
const REASON = "Typed the wrong date";

const INTRO =
  "Use this to correct the issue date or reference recorded for Power of Attorney. The original issuer and issue time are kept, and your reason will be recorded in the sale's history.";
const UNCHANGED = "Change the issue date or the reference to save a correction.";

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

const openCorrect = async () => {
  const card = await loaded();
  await userEvent.click(within(card).getByRole("button", { name: "Correct issuance of Power of Attorney" }));
  const dialog = await screen.findByRole("dialog", { name: "Correct issuance" });
  return { card, dialog };
};

/** Taken once, before any error appears: an error message inside a label changes that label's text. */
const fieldsOf = (dialog) => ({
  date: within(dialog).getByLabelText("Issue date"),
  reference: within(dialog).getByLabelText("Reference"),
  reason: within(dialog).getByLabelText("Reason for correction"),
  save: within(dialog).getByRole("button", { name: "Save correction" }),
});

const setDate = (field, value) => fireEvent.change(field, { target: { value } });
const documentGets = (api) => api.urls().filter((u) => u.startsWith("/api/sale-documents/?")).length;

describe("correct an issued document", () => {
  beforeEach(() => {
    document.cookie = "csrftoken=abc; path=/";
  });

  it("offers Correct issuance only where the API allows it", async () => {
    await openSale();
    const card = await loaded();
    const buttons = within(card).getAllByRole("button", { name: /^Correct issuance of / });
    expect(buttons).toHaveLength(1);
    expect(within(rowOf(card, "Power of Attorney")).getByRole("button", { name: "Correct issuance of Power of Attorney" })).toBe(buttons[0]);
    expect(within(rowOf(card, "Survey Plan")).queryByRole("button", { name: /^Correct issuance of / })).not.toBeInTheDocument();
  });

  it("offers nothing on a cancelled sale", async () => {
    const cancelledSale = {
      ...saleDetail, status: "cancelled", cancel_reason: "Buyer defaulted",
      can: { record_payment: false, reverse_payment: false, edit_buyer: true, cancel: false, reinstate: true },
    };
    // What the API returns for a cancelled sale's documents: no action allowed (get_can in api/serializers/sale_documents.py).
    const asCancelled = {
      ...saleDocuments,
      results: saleDocuments.results.map((d) => ({
        ...d, sale_status: "cancelled", is_overdue: false,
        can: { issue: false, mark_not_applicable: false, correct_issuance: false },
      })),
    };
    await openSale({ [`GET /api/sales-desk/${id}/`]: cancelledSale, "GET /api/sale-documents/": asCancelled });
    const card = await loaded();
    expect(within(card).queryByRole("button", { name: /^Correct issuance of / })).not.toBeInTheDocument();
    expect(within(card).queryAllByRole("button")).toHaveLength(0);
  });

  it("opens with the current date and reference, who first issued it, and Save switched off", async () => {
    await openSale();
    const { dialog } = await openCorrect();
    const { date, reference, reason, save } = fieldsOf(dialog);
    expect(within(dialog).getByText(INTRO)).toBeInTheDocument();
    expect(within(dialog).getByText(/^Originally issued by Emeka Okafor on 21 Oct 2026, \d{2}:\d{2}\.$/)).toBeInTheDocument();
    expect(date).toHaveValue("2026-10-20");
    expect(date).toHaveAttribute("max", todayIso());
    expect(reference).toHaveValue("REF-1042");
    expect(reason).toHaveValue("");
    expect(save).toBeDisabled();
    expect(within(dialog).getByText(UNCHANGED)).toBeInTheDocument();
  });

  it("needs a reason of at least three characters and a real change", async () => {
    await openSale();
    const { dialog } = await openCorrect();
    const { date, reference, reason, save } = fieldsOf(dialog);
    setDate(reason, REASON);
    expect(save).toBeDisabled();                                   // a reason alone is not a correction
    setDate(date, "2026-10-18");
    expect(save).toBeEnabled();
    expect(within(dialog).queryByText(UNCHANGED)).not.toBeInTheDocument();
    setDate(date, "2026-10-20");                                   // back to the record
    expect(save).toBeDisabled();
    setDate(reference, "  REF-1042   ");                           // only spaces differ: not a change
    expect(save).toBeDisabled();
    setDate(reference, "REF-2001");
    expect(save).toBeEnabled();
    setDate(reason, "  ab ");                                      // too short once trimmed
    expect(save).toBeDisabled();
    setDate(reason, "abc");
    expect(save).toBeEnabled();
    setDate(date, "");                                             // an issue date cannot be removed
    expect(save).toBeDisabled();
    expect(within(dialog).getByText("An issue date is required.")).toBeInTheDocument();
  });

  it("sends only the date when only the date changed", async () => {
    const api = await openSale({ [`POST ${url}`]: () => json(correctedPoa) });
    const { dialog } = await openCorrect();
    const { date, reason, save } = fieldsOf(dialog);
    setDate(date, "2026-10-18");
    setDate(reason, REASON);
    await userEvent.click(save);
    await screen.findByText(/issuance details corrected/);
    const [call] = posts(api);
    expect(call.url).toBe(url);
    expect(body(call)).toEqual({ reason: REASON, issued_on: "2026-10-18" });
    expect(body(call)).not.toHaveProperty("reference");
  });

  it("sends only the reference, tidied the way the server tidies it, when only the reference changed", async () => {
    const api = await openSale({ [`POST ${url}`]: () => json(correctedPoa) });
    const { dialog } = await openCorrect();
    const { reference, reason, save } = fieldsOf(dialog);
    setDate(reference, "  REF   2001 ");
    setDate(reason, "Wrong reference typed");
    await userEvent.click(save);
    await screen.findByText(/issuance details corrected/);
    expect(body(posts(api)[0])).toEqual({ reason: "Wrong reference typed", reference: "REF 2001" });
    expect(body(posts(api)[0])).not.toHaveProperty("issued_on");
  });

  it("sends both when both changed", async () => {
    const api = await openSale({ [`POST ${url}`]: () => json(correctedPoa) });
    const { dialog } = await openCorrect();
    const { date, reference, reason, save } = fieldsOf(dialog);
    setDate(date, "2026-10-18");
    setDate(reference, "REF-2001");
    setDate(reason, REASON);
    await userEvent.click(save);
    await screen.findByText(/issuance details corrected/);
    expect(body(posts(api)[0])).toEqual({ reason: REASON, issued_on: "2026-10-18", reference: "REF-2001" });
  });

  it("clears the reference by sending an empty one, and warns first", async () => {
    const api = await openSale({ [`POST ${url}`]: () => json({ ...poa, reference: "" }) });
    const { dialog } = await openCorrect();
    const { reference, reason, save } = fieldsOf(dialog);
    setDate(reference, "REF-2001");
    expect(within(dialog).queryByText("The reference will be removed.")).not.toBeInTheDocument();
    setDate(reference, "");
    expect(within(dialog).getByText("The reference will be removed.")).toBeInTheDocument();
    setDate(reason, "No reference exists");
    await userEvent.click(save);
    await screen.findByText(/issuance details corrected/);
    expect(body(posts(api)[0])).toEqual({ reason: "No reference exists", reference: "" });
  });

  it("sends the reason trimmed", async () => {
    const api = await openSale({ [`POST ${url}`]: () => json(correctedPoa) });
    const { dialog } = await openCorrect();
    const { date, reason, save } = fieldsOf(dialog);
    setDate(date, "2026-10-18");
    setDate(reason, `   ${REASON}   `);
    await userEvent.click(save);
    await screen.findByText(/issuance details corrected/);
    expect(body(posts(api)[0]).reason).toBe(REASON);
  });

  it("records the correction, closes, says what happened, reloads the documents and the sale, and updates the row", async () => {
    let current = saleDocuments;
    const api = await openSale({
      "GET /api/sale-documents/": () => current,
      [`POST ${url}`]: () => {
        current = { ...saleDocuments, results: saleDocuments.results.map((d) => (d.id === poa.id ? correctedPoa : d)) };
        return json(correctedPoa);
      },
    });
    const { card, dialog } = await openCorrect();
    const saleGets = () => api.calls.filter((c) => c.method === "GET" && c.url === `/api/sales-desk/${id}/`).length;
    const documentsBefore = documentGets(api);
    const saleBefore = saleGets();
    const { date, reference, reason, save } = fieldsOf(dialog);
    setDate(date, "2026-10-18");
    setDate(reference, "REF-2001");
    setDate(reason, REASON);
    await userEvent.click(save);
    expect(await within(card).findByText("Power of Attorney issuance details corrected.")).toBeInTheDocument();
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
    await waitFor(() => expect(documentGets(api)).toBe(documentsBefore + 1));
    await waitFor(() => expect(saleGets()).toBe(saleBefore + 1));
    await waitFor(() => expect(within(rowOf(card, "Power of Attorney")).getByText("Issued 18 Oct 2026 · REF-2001")).toBeInTheDocument());
    expect(within(rowOf(card, "Power of Attorney")).getByRole("button", { name: "Correct issuance of Power of Attorney" })).toBeInTheDocument();
  });

  it("still sends a date the picker would not offer, and shows the server's own message beside the date", async () => {
    // Wording follows _checked_date in tracker/services.py; the earliest year is not asserted here.
    const message = "Issue date is implausible (before the earliest accepted year or in the future).";
    const api = await openSale({ [`POST ${url}`]: () => json({ issued_on: [message] }, 400) });
    const { dialog } = await openCorrect();
    const { date, reason, save } = fieldsOf(dialog);
    setDate(date, "2099-01-01");
    setDate(reason, REASON);
    await userEvent.click(save);
    const alert = await within(dialog).findByRole("alert");
    expect(alert).toHaveTextContent(message);
    expect(alert.closest("label")).toContainElement(date);
    expect(within(dialog).getAllByRole("alert")).toHaveLength(1);          // beside the field only, not repeated above
    expect(body(posts(api)[0])).toEqual({ reason: REASON, issued_on: "2099-01-01" });
    expect(screen.getByRole("dialog")).toBeInTheDocument();
    expect(save).toBeEnabled();
  });

  it("shows a reference problem beside the reference", async () => {
    const message = "Reference is too long (maximum 120 characters).";
    await openSale({ [`POST ${url}`]: () => json({ reference: [message] }, 400) });
    const { dialog } = await openCorrect();
    const { reference, reason, save } = fieldsOf(dialog);
    setDate(reference, "REF-2001");
    setDate(reason, REASON);
    await userEvent.click(save);
    const alert = await within(dialog).findByRole("alert");
    expect(alert).toHaveTextContent(message);
    expect(alert.closest("label")).toContainElement(reference);
  });

  it("shows a reason problem beside the reason", async () => {
    const message = "A reason for the correction is required (at least 3 characters).";
    await openSale({ [`POST ${url}`]: () => json({ reason: [message] }, 400) });
    const { dialog } = await openCorrect();
    const { date, reason, save } = fieldsOf(dialog);
    setDate(date, "2026-10-18");
    setDate(reason, REASON);
    await userEvent.click(save);
    const alert = await within(dialog).findByRole("alert");
    expect(alert).toHaveTextContent(message);
    expect(alert.closest("label")).toContainElement(reason);
  });

  it("shows 'Nothing to change.' in the form-level box, not beside a field", async () => {
    await openSale({ [`POST ${url}`]: () => json({ detail: "Nothing to change." }, 400) });
    const { dialog } = await openCorrect();
    const { date, reason, save } = fieldsOf(dialog);
    setDate(date, "2026-10-18");
    setDate(reason, REASON);
    await userEvent.click(save);
    const alert = await within(dialog).findByRole("alert");
    expect(alert).toHaveTextContent("Nothing to change.");
    expect(alert.closest("label")).toBeNull();
    expect(screen.getByRole("dialog")).toBeInTheDocument();
    expect(save).toBeEnabled();
  });

  it("explains a refusal because of the document's state, and reloads the documents in case someone else changed it", async () => {
    const api = await openSale({
      [`POST ${url}`]: () => json({ detail: "Only an issued document can have its issuance corrected." }, 409),
    });
    const { dialog } = await openCorrect();
    const before = documentGets(api);
    const { date, reason, save } = fieldsOf(dialog);
    setDate(date, "2026-10-18");
    setDate(reason, REASON);
    await userEvent.click(save);
    expect(await within(dialog).findByRole("alert")).toHaveTextContent("Only an issued document can have its issuance corrected.");
    await waitFor(() => expect(documentGets(api)).toBe(before + 1));
    expect(screen.getByRole("dialog")).toBeInTheDocument();
    expect(save).toBeEnabled();
  });

  it("closes without sending anything on Cancel or Escape", async () => {
    const api = await openSale();
    const first = await openCorrect();
    await userEvent.click(within(first.dialog).getByRole("button", { name: "Cancel" }));
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
    await openCorrect();
    await userEvent.keyboard("{Escape}");
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
    expect(posts(api)).toHaveLength(0);
  });

  it("sends one request however many times Save is pressed while it is working", async () => {
    let release;
    const gate = new Promise((resolve) => { release = resolve; });
    const api = await openSale({ [`POST ${url}`]: async () => { await gate; return json(correctedPoa); } });
    const { dialog } = await openCorrect();
    const { date, reason, save } = fieldsOf(dialog);
    setDate(date, "2026-10-18");
    setDate(reason, REASON);
    await userEvent.click(save);
    await waitFor(() => expect(save).toBeDisabled());
    expect(save).toHaveTextContent("Saving…");
    await userEvent.click(save);
    release();
    expect(await screen.findByText("Power of Attorney issuance details corrected.")).toBeInTheDocument();
    expect(posts(api)).toHaveLength(1);
  });
});
