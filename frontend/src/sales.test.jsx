import { describe, expect, it } from "vitest";
import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { baseRoutes, fixtures, json, mockApi, renderApp } from "./test/helpers.jsx";
import { todayIso } from "./utils/sales.js";

import salesList from "./test/fixtures/sales_list.json";
import saleDetail from "./test/fixtures/sale_detail.json";
import buyerSearch from "./test/fixtures/buyer_search.json";

const id = saleDetail.id;
const body = (c) => JSON.parse(c.init.body);
const calls = (api, method, end) => api.calls.filter((c) => c.method === method && c.url.split("?")[0].endsWith(end));
const lastListUrl = (api) => api.urls().filter((u) => u.startsWith("/api/sales-desk/?")).at(-1);
const estate = fixtures.estates.results[0];

const routes = (extra = {}, role = "manager") =>
  baseRoutes(role, {
    "GET /api/sales-desk/": salesList,
    [`GET /api/sales-desk/${id}/`]: saleDetail,
    "GET /api/sales-desk/buyer-search/": buyerSearch,
    ...extra,
  });

const openSale = async (extra, role) => {
  const api = mockApi(routes(extra, role));
  renderApp(`/sales/${id}`);
  await screen.findByRole("heading", { name: saleDetail.buyer_name });
  return api;
};

describe("sales list", () => {
  it("shows each sale with its money, documents and status", async () => {
    mockApi(routes());
    renderApp("/sales");
    const link = await screen.findByRole("link", { name: "Bayo Lawal" });
    expect(link).toHaveAttribute("href", `/sales/${id}`);
    const row = link.closest("tr");
    expect(within(row).getByText("₦1,000,000")).toBeInTheDocument();
    expect(within(row).getByText("₦400,000")).toBeInTheDocument();
    expect(within(row).getByText("₦600,000")).toBeInTheDocument();
    expect(within(row).getByText("1 open of 1")).toBeInTheDocument();
    const paid = screen.getByRole("link", { name: "Chidi Eze" }).closest("tr");
    expect(within(paid).getByText("Fully paid")).toBeInTheDocument();
    const cancelled = screen.getByRole("link", { name: "Ngozi Eke" }).closest("tr");
    expect(within(cancelled).getByText("Cancelled")).toBeInTheDocument();
    expect(cancelled).toHaveClass("row-cancelled");
  });

  it("turns the address into API filters", async () => {
    const api = mockApi(routes());
    renderApp("/sales?q=bayo&status=cancelled&estate=5&fully_paid=0&junk=1");
    await screen.findByRole("heading", { name: "Sales" });
    await waitFor(() => expect(lastListUrl(api)).toBeTruthy());
    expect(Object.fromEntries(new URL(lastListUrl(api), "http://x").searchParams)).toEqual({
      q: "bayo", status: "cancelled", estate: "5", fully_paid: "0", page: "1", page_size: "25",
    });
  });

  it("searches on Enter, filters from the drop-downs and clears everything", async () => {
    const api = mockApi(routes());
    renderApp("/sales");
    await screen.findByRole("heading", { name: "Sales" });
    await userEvent.type(screen.getByRole("searchbox"), "Bakare{Enter}");
    await waitFor(() => expect(lastListUrl(api)).toContain("q=Bakare"));
    await userEvent.selectOptions(screen.getByLabelText("Status"), "Cancelled");
    await waitFor(() => expect(lastListUrl(api)).toContain("status=cancelled"));
    await userEvent.selectOptions(screen.getByLabelText("Payment"), "Fully paid");
    await waitFor(() => expect(lastListUrl(api)).toContain("fully_paid=1"));
    await userEvent.click(screen.getByRole("button", { name: "Clear filters" }));
    await waitFor(() => expect(lastListUrl(api)).not.toContain("status="));
    expect(lastListUrl(api)).not.toContain("q=");
  });

  it("pages forwards", async () => {
    const api = mockApi(routes({ "GET /api/sales-desk/": { ...salesList, count: 80 } }));
    renderApp("/sales");
    await userEvent.click(await screen.findByRole("button", { name: "Next" }));
    await waitFor(() => expect(lastListUrl(api)).toContain("page=2"));
    expect(await screen.findByText("Page 2 of 4")).toBeInTheDocument();
  });

  it("says so when nothing matches", async () => {
    mockApi(routes({ "GET /api/sales-desk/": { count: 0, next: null, previous: null, results: [] } }));
    renderApp("/sales?q=zzz");
    expect(await screen.findByText("No sales match these filters.")).toBeInTheDocument();
  });

  it("is for MD and Manager only", async () => {
    for (const role of ["staff", "sales", "viewer"]) {
      mockApi(routes({}, role));
      const { unmount } = renderApp("/sales");
      expect(await screen.findByRole("heading", { name: "You do not have access to this page" })).toBeInTheDocument();
      unmount();
    }
    mockApi(routes({}, "md"));
    renderApp("/sales");
    expect(await screen.findByRole("heading", { name: "Sales" })).toBeInTheDocument();
  });
});

describe("add a sale", () => {
  const openAdd = async (extra) => {
    const api = mockApi(routes(extra));
    renderApp("/sales");
    await userEvent.click(await screen.findByRole("button", { name: "Add sale" }));
    const dialog = await screen.findByRole("dialog", { name: "Add a sale" });
    await within(dialog).findByRole("option", { name: estate.name });
    return { api, dialog };
  };

  it("adds a sale for a new estate and a new buyer, then opens it", async () => {
    document.cookie = "csrftoken=abc; path=/";
    const { api, dialog } = await openAdd({ "POST /api/sales-desk/": () => json(saleDetail, 201) });
    const submit = within(dialog).getByRole("button", { name: "Add sale" });
    expect(submit).toBeDisabled();
    await userEvent.selectOptions(within(dialog).getByLabelText("Estate"), "New estate…");
    await userEvent.type(within(dialog).getByLabelText("New estate name"), "Lakeside Meadows");
    await userEvent.type(within(dialog).getByLabelText("Plot number"), "N-1");
    await userEvent.type(within(dialog).getByLabelText("Buyer name"), "Ada Obi");
    await userEvent.type(within(dialog).getByLabelText("Phone (optional)"), "0803 111 2222");
    await userEvent.type(within(dialog).getByLabelText("Price"), "1500000");
    await userEvent.type(within(dialog).getByLabelText("Amount paid so far (optional)"), "500000");
    expect(submit).toBeEnabled();
    await userEvent.click(submit);
    expect(await screen.findByText("Sale added.")).toBeInTheDocument();
    expect(body(calls(api, "POST", "/api/sales-desk/")[0])).toEqual({
      estate_name: "Lakeside Meadows", plot_number: "N-1", buyer_name: "Ada Obi", phone: "0803 111 2222",
      price: "1500000", amount_paid: "500000", sale_date: todayIso(),
    });
    expect(await screen.findByRole("heading", { name: saleDetail.buyer_name })).toBeInTheDocument();
  });

  it("reuses an existing buyer found by search", async () => {
    document.cookie = "csrftoken=abc; path=/";
    const { api, dialog } = await openAdd({ "POST /api/sales-desk/": () => json(saleDetail, 201) });
    await userEvent.selectOptions(within(dialog).getByLabelText("Estate"), estate.name);
    await userEvent.type(within(dialog).getByLabelText("Plot number"), "A-9");
    await userEvent.click(within(dialog).getByRole("radio", { name: "Existing buyer" }));
    await userEvent.type(within(dialog).getByLabelText("Find a buyer by name, phone or email"), "bayo");
    await userEvent.click(await within(dialog).findByRole("button", { name: /Bayo Lawal/ }));
    expect(within(dialog).getByText(/Chosen buyer: Bayo Lawal/)).toBeInTheDocument();
    await userEvent.type(within(dialog).getByLabelText("Price"), "1000");
    await userEvent.click(within(dialog).getByRole("button", { name: "Add sale" }));
    await screen.findByText("Sale added.");
    expect(body(calls(api, "POST", "/api/sales-desk/")[0])).toEqual({
      estate: estate.id, plot_number: "A-9", buyer: 701, price: "1000", sale_date: todayIso(),
    });
  });

  it("names the current buyer when the plot is already sold, with a link to that sale", async () => {
    const { dialog } = await openAdd({
      "POST /api/sales-desk/": () => json({
        detail: "Plot A-2 in Green Valley Estate already has an active sale to Bayo Lawal.",
        existing_sale: { id: 612, buyer: 701, buyer_name: "Bayo Lawal" },
      }, 409),
    });
    await userEvent.selectOptions(within(dialog).getByLabelText("Estate"), estate.name);
    await userEvent.type(within(dialog).getByLabelText("Plot number"), "A-2");
    await userEvent.type(within(dialog).getByLabelText("Buyer name"), "Someone Else");
    await userEvent.type(within(dialog).getByLabelText("Price"), "1000");
    await userEvent.click(within(dialog).getByRole("button", { name: "Add sale" }));
    const alert = await within(dialog).findByRole("alert");
    expect(alert).toHaveTextContent("already has an active sale to Bayo Lawal");
    expect(within(dialog).getByRole("link", { name: "View that sale" })).toHaveAttribute("href", "/sales/612");
    expect(within(dialog).getByRole("button", { name: "Add sale" })).toBeEnabled();      // can fix it and retry
  });

  it("shows the server's message under the field it concerns", async () => {
    const { dialog } = await openAdd({ "POST /api/sales-desk/": () => json({ price: ["A valid number is required."] }, 400) });
    await userEvent.selectOptions(within(dialog).getByLabelText("Estate"), estate.name);
    await userEvent.type(within(dialog).getByLabelText("Plot number"), "A-9");
    await userEvent.type(within(dialog).getByLabelText("Buyer name"), "Ada Obi");
    await userEvent.type(within(dialog).getByLabelText("Price"), "abc");
    await userEvent.click(within(dialog).getByRole("button", { name: "Add sale" }));
    expect(await within(dialog).findByText("A valid number is required.")).toBeInTheDocument();
  });
});

describe("the sale page", () => {
  it("shows the money, the documents, the payments and the history", async () => {
    await openSale();
    const money = screen.getByRole("heading", { name: "The money" }).closest("section");
    expect(within(money).getByText("₦1,000,000")).toBeInTheDocument();
    expect(within(money).getByText("₦400,000")).toBeInTheDocument();
    expect(within(money).getByText("₦600,000")).toBeInTheDocument();
    const docs = screen.getByRole("heading", { name: "Documents owed" }).closest("section");
    expect(within(docs).getByRole("link", { name: "Allocation Letter" })).toHaveAttribute("href", "/obligations/1901");
    const payments = screen.getByRole("heading", { name: "Payments" }).closest("section");
    expect(within(payments).getAllByRole("row")).toHaveLength(saleDetail.payments.length + 1);
    expect(within(payments).getAllByText("Reversed")).toHaveLength(1);
    expect(within(payments).getByText("Reversal")).toBeInTheDocument();
    expect(within(payments).getAllByRole("button", { name: /^Reverse payment #/ })).toHaveLength(2);
    const history = screen.getByRole("heading", { name: "History" }).closest("section");
    const entries = within(history).getAllByRole("listitem").filter((li) => li.querySelector(".timeline-head"));
    expect(entries).toHaveLength(saleDetail.history.length);
    expect(entries[0]).toHaveTextContent("Payment reversed");
    expect(entries.at(-1)).toHaveTextContent("Sale created");
  });

  it("offers only what this person may do with this sale", async () => {
    const limited = { ...saleDetail, can: { record_payment: false, reverse_payment: false, edit_buyer: true, cancel: false, reinstate: false } };
    await openSale({ [`GET /api/sales-desk/${id}/`]: limited });
    expect(screen.queryByRole("button", { name: "Record payment" })).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /^Reverse payment/ })).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Cancel sale" })).not.toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Edit buyer" })).toBeInTheDocument();
  });

  it("explains a cancelled sale and offers to reinstate it", async () => {
    const cancelled = { ...saleDetail, status: "cancelled", cancel_reason: "Buyer defaulted",
      can: { record_payment: false, reverse_payment: false, edit_buyer: true, cancel: false, reinstate: true } };
    await openSale({ [`GET /api/sales-desk/${id}/`]: cancelled });
    expect(screen.getByText(/This sale was cancelled: Buyer defaulted/)).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Record payment" })).not.toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Reinstate sale" })).toBeInTheDocument();
  });

  it("shows an error for a sale that is not found", async () => {
    mockApi(routes({ "GET /api/sales-desk/999/": () => json({ detail: "Not found." }, 404) }));
    renderApp("/sales/999");
    expect(await screen.findByRole("alert")).toHaveTextContent("Not found.");
  });
});

describe("record a payment", () => {
  const completed = {
    ...saleDetail, amount_paid: "1000000.00", balance_due: "0.00", is_fully_paid: true, fully_paid_on: "2026-10-13",
    payment: { id: 9100, kind: "payment", amount: "600000.00", paid_on: "2026-10-13", reference: "TRF-77", note: "",
      recorded_by_name: "Emeka Okafor", created_at: "2026-10-13T09:00:00+01:00", reverses: null },
    obligations_created: [{ id: 1950, type_name: "Deed of Assignment", started_on: "2026-10-13", due_date: "2027-01-11" }],
    duplicate: false,
  };
  const paymentUrl = `POST /api/sales-desk/${id}/record-payment/`;
  const openForm = async () => {
    await userEvent.click(screen.getByRole("button", { name: "Record payment" }));
    return screen.findByRole("dialog", { name: "Record a payment" });
  };

  it("records a payment and says which documents became due", async () => {
    document.cookie = "csrftoken=abc; path=/";
    const api = await openSale({ [paymentUrl]: () => json(completed, 201) });
    const dialog = await openForm();
    expect(within(dialog).getByText("Balance due: ₦600,000")).toBeInTheDocument();
    const submit = within(dialog).getByRole("button", { name: "Record payment" });
    expect(submit).toBeDisabled();
    await userEvent.click(within(dialog).getByRole("button", { name: "Pay the full balance" }));
    expect(within(dialog).getByLabelText("Amount")).toHaveValue("600000.00");
    expect(within(dialog).getByText(/completes the price/)).toBeInTheDocument();
    await userEvent.type(within(dialog).getByLabelText("Reference (optional)"), "TRF-77");
    await userEvent.click(submit);
    expect(await screen.findByText(/Payment recorded\./)).toBeInTheDocument();
    expect(screen.getByText(/Deed of Assignment \(due 11 Jan 2027\)/)).toBeInTheDocument();
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
    expect(body(calls(api, "POST", "/record-payment/")[0])).toEqual({
      amount: "600000.00", paid_on: todayIso(), reference: "TRF-77", idempotency_key: expect.any(String),
    });
  });

  it("uses the same request key when the person retries after a failure", async () => {
    let attempt = 0;
    const api = await openSale({ [paymentUrl]: () => (++attempt === 1 ? json({ detail: "Something went wrong." }, 500) : json(completed, 201)) });
    const dialog = await openForm();
    await userEvent.type(within(dialog).getByLabelText("Amount"), "100000");
    await userEvent.click(within(dialog).getByRole("button", { name: "Record payment" }));
    expect(await within(dialog).findByRole("alert")).toHaveTextContent("Something went wrong.");
    await userEvent.click(within(dialog).getByRole("button", { name: "Record payment" }));
    await screen.findByText(/Payment recorded\./);
    const keys = calls(api, "POST", "/record-payment/").map((c) => body(c).idempotency_key);
    expect(keys).toHaveLength(2);
    expect(keys[0]).toBe(keys[1]);
  });

  it("says when the payment had already been recorded", async () => {
    await openSale({ [paymentUrl]: () => json({ ...completed, obligations_created: [], duplicate: true }, 200) });
    const dialog = await openForm();
    await userEvent.type(within(dialog).getByLabelText("Amount"), "600000");
    await userEvent.click(within(dialog).getByRole("button", { name: "Record payment" }));
    expect(await screen.findByText("This payment had already been recorded, so nothing new was added.")).toBeInTheDocument();
  });

  it("keeps the form open and explains a refused payment", async () => {
    await openSale({ [paymentUrl]: () => json({ detail: "This payment would take the total paid to 1100000.00, which is more than the price 1000000.00. The most that can still be recorded is 600000.00." }, 400) });
    const dialog = await openForm();
    await userEvent.type(within(dialog).getByLabelText("Amount"), "700000");
    expect(within(dialog).getByText(/more than the balance due/)).toBeInTheDocument();
    await userEvent.click(within(dialog).getByRole("button", { name: "Record payment" }));
    expect(await within(dialog).findByRole("alert")).toHaveTextContent("The most that can still be recorded is 600000.00.");
    expect(screen.getByRole("dialog")).toBeInTheDocument();
  });
});

describe("reverse a payment", () => {
  it("needs a reason, then adds a reversal", async () => {
    document.cookie = "csrftoken=abc; path=/";
    const url = `POST /api/sales-desk/${id}/payments/9002/reverse/`;
    const api = await openSale({ [url]: () => json({ ...saleDetail, reversal: { id: 9200 } }) });
    await userEvent.click(screen.getByRole("button", { name: "Reverse payment #9002" }));
    const dialog = await screen.findByRole("dialog", { name: "Reverse this payment" });
    const submit = within(dialog).getByRole("button", { name: "Reverse payment" });
    expect(submit).toBeDisabled();
    await userEvent.type(within(dialog).getByLabelText("Reason"), "Cheque bounced");
    await userEvent.click(submit);
    expect(await screen.findByText("Payment reversed.")).toBeInTheDocument();
    const [call] = calls(api, "POST", "/reverse/");
    expect(call.url).toBe(`/api/sales-desk/${id}/payments/9002/reverse/`);
    expect(body(call)).toEqual({ reason: "Cheque bounced" });
  });
});

describe("edit the buyer", () => {
  it("sends only what changed", async () => {
    const api = await openSale({ [`POST /api/sales-desk/${id}/edit-buyer/`]: () => json(saleDetail) });
    await userEvent.click(screen.getByRole("button", { name: "Edit buyer" }));
    const dialog = await screen.findByRole("dialog", { name: "Edit buyer details" });
    const save = within(dialog).getByRole("button", { name: "Save changes" });
    expect(save).toBeDisabled();
    expect(within(dialog).getByLabelText("Full name")).toHaveValue("Bayo Lawal");
    const phone = within(dialog).getByLabelText("Phone");
    await userEvent.clear(phone);
    await userEvent.type(phone, "0803 999 0000");
    await userEvent.click(save);
    expect(await screen.findByText("Buyer details saved.")).toBeInTheDocument();
    expect(body(calls(api, "POST", "/edit-buyer/")[0])).toEqual({ phone: "0803 999 0000" });
  });
});

describe("cancel and reinstate", () => {
  it("cancels with a reason, then reinstates and reports the documents it created", async () => {
    document.cookie = "csrftoken=abc; path=/";
    const cancelled = { ...saleDetail, status: "cancelled", cancel_reason: "Buyer defaulted",
      can: { record_payment: false, reverse_payment: false, edit_buyer: true, cancel: false, reinstate: true } };
    let current = saleDetail;
    const api = await openSale({
      [`GET /api/sales-desk/${id}/`]: () => current,
      [`POST /api/sales-desk/${id}/cancel/`]: () => { current = cancelled; return json(cancelled); },
      [`POST /api/sales-desk/${id}/reinstate/`]: () => {
        current = saleDetail;
        return json({ ...saleDetail, obligations_created: [{ id: 1950, type_name: "Deed of Assignment", started_on: "2026-10-13", due_date: "2027-01-11" }] });
      },
    });

    await userEvent.click(screen.getByRole("button", { name: "Cancel sale" }));
    const cancelDialog = await screen.findByRole("dialog", { name: "Cancel this sale" });
    const confirm = within(cancelDialog).getByRole("button", { name: "Cancel sale" });
    expect(confirm).toBeDisabled();
    await userEvent.type(within(cancelDialog).getByLabelText("Reason for cancelling"), "Buyer defaulted");
    await userEvent.click(confirm);
    expect(await screen.findByText("Sale cancelled.")).toBeInTheDocument();
    expect(body(calls(api, "POST", "/cancel/")[0])).toEqual({ reason: "Buyer defaulted" });
    expect(await screen.findByText(/This sale was cancelled: Buyer defaulted/)).toBeInTheDocument();

    await userEvent.click(screen.getByRole("button", { name: "Reinstate sale" }));
    const reinstateDialog = await screen.findByRole("dialog", { name: "Reinstate this sale" });
    await userEvent.type(within(reinstateDialog).getByLabelText("Note (optional)"), "Mistake");
    await userEvent.click(within(reinstateDialog).getByRole("button", { name: "Reinstate sale" }));
    expect(await screen.findByText(/Sale reinstated\. These documents were created: Deed of Assignment \(due 11 Jan 2027\)/)).toBeInTheDocument();
    expect(body(calls(api, "POST", "/reinstate/")[0])).toEqual({ note: "Mistake" });
  });
});

describe("from an obligation to its sale", () => {
  const d = fixtures.obligationDetail;
  const obligationRoutes = (role) => baseRoutes(role, {
    [`GET /api/obligations/${d.id}/`]: d,
    [`GET /api/obligations/${d.id}/events/`]: fixtures.obligationEvents,
  });

  it("links to the sale for people who may see sales", async () => {
    mockApi(obligationRoutes("manager"));
    renderApp(`/obligations/${d.id}`);
    expect(await screen.findByRole("link", { name: "View this sale" })).toHaveAttribute("href", `/sales/${d.sale}`);
  });

  it("shows no link to people who may not", async () => {
    mockApi(obligationRoutes("sales"));
    renderApp(`/obligations/${d.id}`);
    await screen.findByRole("heading", { name: "The sale" });
    expect(screen.queryByRole("link", { name: "View this sale" })).not.toBeInTheDocument();
  });
});
