import { describe, expect, it } from "vitest";
import { renderHook, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { useRefreshAfterSave } from "./hooks/useRefreshAfterSave.js";
import { baseRoutes, json, mockApi } from "./test/helpers.jsx";
import { board, estate, renderAppLikeMain } from "./test/inventoryHelpers.jsx";

import saleDetail from "./test/fixtures/sale_detail.json";
import salesList from "./test/fixtures/sales_list.json";

const stale = (client, key) => client.getQueryState(key).isInvalidated;

describe("useRefreshAfterSave", () => {
  it("marks everything a save can change as stale, including plot boards and estate lists", async () => {
    const client = new QueryClient();
    const shown = [
      ["obligations"], ["obligation", "1"], ["queue"], ["dashboard"], ["sales", { page: 1 }], ["sale", "612"],
      ["plot-board", "5"], ["plot-board", "8"], ["estates"], ["estates", "all"],
    ];
    const untouched = [["buyer-search", "ada"], ["new-sale", "document-types"], ["users"], ["obligation-types"]];
    [...shown, ...untouched].forEach((key) => client.setQueryData(key, { cached: true }));
    const wrapper = ({ children }) => <QueryClientProvider client={client}>{children}</QueryClientProvider>;
    const { result } = renderHook(() => useRefreshAfterSave(), { wrapper });
    await result.current();
    for (const key of shown) expect(stale(client, key), JSON.stringify(key)).toBe(true);
    for (const key of untouched) expect(stale(client, key), JSON.stringify(key)).toBe(false);
  });
});

describe("adding a sale from the Sales page", () => {
  const routes = baseRoutes("md", {
    "GET /api/sales-desk/": salesList,
    [`GET /api/sales-desk/${saleDetail.id}/`]: saleDetail,
    "GET /api/sale-documents/": { count: 0, next: null, previous: null, results: [] },
    "GET /api/plot-board/": board("md"),
    "POST /api/sales-desk/": json(saleDetail, 201),
  });
  const openAddSale = async () => {
    const view = renderAppLikeMain("/sales");
    await userEvent.click(await screen.findByRole("button", { name: "Add sale" }));
    return { ...view, dialog: screen.getByRole("dialog") };
  };

  it("starts with no estate or plot selected and requires an existing estate", async () => {
    mockApi(routes);
    const { dialog } = await openAddSale();
    expect(within(dialog).getByLabelText("Estate")).toHaveValue("");
    expect(within(dialog).getByText(
      "Choose an existing estate to see its registered available plots.",
    )).toBeInTheDocument();
    expect(within(dialog).queryByRole("option", { name: "New estate…" })).not.toBeInTheDocument();
  });

  it("refreshes plot boards and estate lists after a sale", async () => {
    const api = mockApi(routes);
    const { client, dialog } = await openAddSale();
    client.setQueryData(["plot-board", String(estate.id)], board("md"));
    client.setQueryData(["estates", "all"], { count: 1, results: [estate] });
    expect(stale(client, ["plot-board", String(estate.id)])).toBe(false);

    await userEvent.selectOptions(within(dialog).getByLabelText("Estate"), String(estate.id));
    await userEvent.selectOptions(
      within(dialog).getByLabelText("Registered available plot"),
      "A-1",
    );
    await userEvent.type(within(dialog).getByLabelText("Buyer name"), "Ada Obi");
    await userEvent.type(within(dialog).getByLabelText("Price"), "1500000");
    await userEvent.click(within(dialog).getByRole("button", { name: "Add sale" }));

    expect(await screen.findByRole("heading", { name: saleDetail.buyer_name })).toBeInTheDocument();   // navigated to the sale
    await waitFor(() => {
      const boardRequests = api.calls.filter(
        (call) => call.method === "GET" && call.url.startsWith("/api/plot-board/"),
      );
      expect(boardRequests.length).toBeGreaterThan(1);
    });
  });
});
