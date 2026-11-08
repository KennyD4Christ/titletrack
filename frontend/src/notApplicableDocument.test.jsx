import { beforeEach, describe, expect, it } from "vitest";
import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { baseRoutes, json, mockApi, renderApp } from "./test/helpers.jsx";

import saleDetail from "./test/fixtures/sale_detail.json";
import saleDocuments from "./test/fixtures/sale_documents.json";

const id = saleDetail.id;
const body = (call) => JSON.parse(call.init.body);
const skipUrl = (docId) => `/api/sale-documents/${docId}/mark-not-applicable/`;
const posts = (api) => api.calls.filter((c) => c.method === "POST");

const INTRO =
  "Use this when the document turns out not to be required for this sale. The reason will be recorded in the sale's history. This action cannot be undone from this page.";
const OPEN_WORK = "Its open preparation work will also be marked as not applicable.";
const DONE_WORK = "Its preparation work is already completed and will remain completed.";
const REASON = "Buyer already holds the original";

// Survey Plan (3001) is outstanding in the fixture; this is what the API returns once it is marked not applicable.
const survey = saleDocuments.results.find((d) => d.name === "Survey Plan");
const skippedSurvey = {
  ...survey, state: "not_applicable", display_status: "not_required", obligation_status: "not_applicable",
  not_applicable_reason: REASON, not_applicable_by_name: "Emeka Okafor", not_applicable_at: "2026-11-03T10:00:00+01:00",
  can: { issue: false, mark_not_applicable: false, correct_issuance: false },
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

const openSkip = async (name) => {
  const card = await loaded();
  await userEvent.click(within(card).getByRole("button", { name: `Mark ${name} not applicable` }));
  const dialog = await screen.findByRole("dialog", { name: `Mark ${name} not applicable` });
  return { card, dialog };
};

const documentGets = (api) => api.urls().filter((u) => u.startsWith("/api/sale-documents/?")).length;

/** The fixture with Survey Plan's `can` flags changed. */
const withSurveyCan = (patch) => ({
  ...saleDocuments,
  results: saleDocuments.results.map((d) => (d.id === 3001 ? { ...d, can: { ...d.can, ...patch } } : d)),
});

describe("mark a document not applicable", () => {
  beforeEach(() => {
    document.cookie = "csrftoken=abc; path=/";
  });

  it("opens for the chosen document and needs a reason of at least three characters", async () => {
    await openSale();
    const { dialog } = await openSkip("Survey Plan");
    const reason = within(dialog).getByLabelText("Reason");
    const submit = within(dialog).getByRole("button", { name: "Mark not applicable" });
    expect(reason).toHaveValue("");
    expect(submit).toBeDisabled();
    await userEvent.type(reason, "  ab ");
    expect(submit).toBeDisabled();
    await userEvent.clear(reason);
    await userEvent.type(reason, "abc");
    expect(submit).toBeEnabled();
  });

  it("sends the reason, trimmed, to the document's own endpoint", async () => {
    const api = await openSale({ [`POST ${skipUrl(3001)}`]: () => json(skippedSurvey) });
    const { dialog } = await openSkip("Survey Plan");
    await userEvent.type(within(dialog).getByLabelText("Reason"), `  ${REASON}  `);
    await userEvent.click(within(dialog).getByRole("button", { name: "Mark not applicable" }));
    await screen.findByText(/marked as not applicable/);
    const [call] = posts(api);
    expect(call.method).toBe("POST");
    expect(call.url).toBe(skipUrl(3001));
    expect(body(call)).toEqual({ reason: REASON });
  });

  it("records it, closes, says what happened, and reloads the documents and the sale", async () => {
    let current = saleDocuments;
    const api = await openSale({
      "GET /api/sale-documents/": () => current,
      [`POST ${skipUrl(3001)}`]: () => {
        current = { ...saleDocuments, results: saleDocuments.results.map((d) => (d.id === 3001 ? skippedSurvey : d)) };
        return json(skippedSurvey);
      },
    });
    const { card, dialog } = await openSkip("Survey Plan");
    const saleGets = () => api.calls.filter((c) => c.method === "GET" && c.url === `/api/sales-desk/${id}/`).length;
    const documentsBefore = documentGets(api);
    const saleBefore = saleGets();
    await userEvent.type(within(dialog).getByLabelText("Reason"), REASON);
    await userEvent.click(within(dialog).getByRole("button", { name: "Mark not applicable" }));
    expect(await within(card).findByText("Survey Plan marked as not applicable.")).toBeInTheDocument();
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
    await waitFor(() => expect(documentGets(api)).toBe(documentsBefore + 1));
    await waitFor(() => expect(saleGets()).toBe(saleBefore + 1));
    await waitFor(() => expect(within(rowOf(card, "Survey Plan")).getByText("Not required")).toBeInTheDocument());
    expect(within(rowOf(card, "Survey Plan")).getByText(`Reason: ${REASON}`)).toBeInTheDocument();
    expect(within(rowOf(card, "Survey Plan")).queryByRole("button")).not.toBeInTheDocument();
  });

  it("says what happens to the preparation work: open work is closed, completed work stays completed", async () => {
    await openSale();
    const cases = [
      ["Survey Plan", OPEN_WORK],             // outstanding
      ["Deed of Assignment", OPEN_WORK],      // in progress
      ["Contract of Sale", DONE_WORK],        // ready to issue: the work is already complete
    ];
    for (const [name, sentence] of cases) {
      const { dialog } = await openSkip(name);
      expect(within(dialog).getByText(`${INTRO} ${sentence}`)).toBeInTheDocument();
      await userEvent.click(within(dialog).getByRole("button", { name: "Not now" }));
    }
  });

  it("shows the server's message when the reason is refused, keeps the dialog open, and can be retried", async () => {
    let attempt = 0;
    const api = await openSale({
      [`POST ${skipUrl(3001)}`]: () => (++attempt === 1
        ? json({ reason: ["Ensure this field has no more than 1000 characters."] }, 400)
        : json(skippedSurvey)),
    });
    const { dialog } = await openSkip("Survey Plan");
    await userEvent.type(within(dialog).getByLabelText("Reason"), REASON);
    const submit = within(dialog).getByRole("button", { name: "Mark not applicable" });
    await userEvent.click(submit);
    expect(await within(dialog).findByRole("alert")).toHaveTextContent("Ensure this field has no more than 1000 characters.");
    expect(screen.getByRole("dialog")).toBeInTheDocument();
    expect(submit).toBeEnabled();
    await userEvent.click(submit);
    expect(await screen.findByText("Survey Plan marked as not applicable.")).toBeInTheDocument();
    expect(posts(api)).toHaveLength(2);
  });

  it("explains a refusal because of the document's state, and reloads the documents in case someone else changed it", async () => {
    const api = await openSale({
      [`POST ${skipUrl(3001)}`]: () => json({ detail: "This document has already been issued." }, 409),
    });
    const { dialog } = await openSkip("Survey Plan");
    const before = documentGets(api);
    await userEvent.type(within(dialog).getByLabelText("Reason"), REASON);
    const submit = within(dialog).getByRole("button", { name: "Mark not applicable" });
    await userEvent.click(submit);
    expect(await within(dialog).findByRole("alert")).toHaveTextContent("This document has already been issued.");
    await waitFor(() => expect(documentGets(api)).toBe(before + 1));
    expect(screen.getByRole("dialog")).toBeInTheDocument();
    expect(submit).toBeEnabled();
  });

  it("closes without sending anything on Not now or Escape", async () => {
    const api = await openSale();
    const first = await openSkip("Survey Plan");
    await userEvent.click(within(first.dialog).getByRole("button", { name: "Not now" }));
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
    await openSkip("Survey Plan");
    await userEvent.keyboard("{Escape}");
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
    expect(posts(api)).toHaveLength(0);
  });

  it("sends one request however many times it is pressed while it is working", async () => {
    let release;
    const gate = new Promise((resolve) => { release = resolve; });
    const api = await openSale({
      [`POST ${skipUrl(3001)}`]: async () => { await gate; return json(skippedSurvey); },
    });
    const { dialog } = await openSkip("Survey Plan");
    await userEvent.type(within(dialog).getByLabelText("Reason"), REASON);
    const submit = within(dialog).getByRole("button", { name: "Mark not applicable" });
    await userEvent.click(submit);
    await waitFor(() => expect(submit).toBeDisabled());
    expect(submit).toHaveTextContent("Working…");
    await userEvent.click(submit);
    release();
    expect(await screen.findByText("Survey Plan marked as not applicable.")).toBeInTheDocument();
    expect(posts(api)).toHaveLength(1);
  });

  it("follows its own flag: Issue without Not applicable when only issuing is allowed", async () => {
    await openSale({ "GET /api/sale-documents/": withSurveyCan({ issue: true, mark_not_applicable: false }) });
    const row = rowOf(await loaded(), "Survey Plan");
    expect(within(row).getByRole("button", { name: "Issue Survey Plan" })).toBeInTheDocument();
    expect(within(row).queryByRole("button", { name: "Mark Survey Plan not applicable" })).not.toBeInTheDocument();
  });

  it("follows its own flag: Not applicable without Issue when only skipping is allowed", async () => {
    await openSale({ "GET /api/sale-documents/": withSurveyCan({ issue: false, mark_not_applicable: true }) });
    const row = rowOf(await loaded(), "Survey Plan");
    expect(within(row).getByRole("button", { name: "Mark Survey Plan not applicable" })).toBeInTheDocument();
    expect(within(row).queryByRole("button", { name: "Issue Survey Plan" })).not.toBeInTheDocument();
  });

  it("leaves the issue dialog working beside it", async () => {
    await openSale();
    const { dialog } = await openSkip("Survey Plan");
    await userEvent.click(within(dialog).getByRole("button", { name: "Not now" }));
    const card = await loaded();
    await userEvent.click(within(card).getByRole("button", { name: "Issue Survey Plan" }));
    expect(await screen.findByRole("dialog", { name: "Issue document" })).toBeInTheDocument();
  });
});
