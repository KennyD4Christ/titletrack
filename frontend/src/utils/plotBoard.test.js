import { describe, expect, it } from "vitest";
import { filterPlots, hasPaymentDetail, naturalCompare, paymentLabel, sortPlots, statusLabel, summarise } from "./plotBoard.js";

const plot = (id, plot_number, status = "available", extra = {}) => ({ id, plot_number, status, ...extra });

describe("natural order", () => {
  it("puts A-2 before A-10 whatever the case, and breaks ties by id", () => {
    const sorted = sortPlots([plot(4, "a-10"), plot(1, "A-2"), plot(3, "A-1"), plot(2, "a-2")]);
    expect(sorted.map((p) => p.id)).toEqual([3, 1, 2, 4]);
    expect(naturalCompare("Block 9", "Block 10")).toBeLessThan(0);
    expect(naturalCompare("5", "05")).toBe(0);
  });
  it("does not change the list it is given", () => {
    const list = [plot(2, "B"), plot(1, "A")];
    sortPlots(list);
    expect(list.map((p) => p.id)).toEqual([2, 1]);
  });
});

describe("filtering", () => {
  const plots = [
    plot(1, "A-1", "available", { payment_state: null }),
    plot(2, "A-10", "sold", { payment_state: "fully_paid" }),
    plot(3, "A-2", "sold", { payment_state: "part_paid" }),
    plot(4, "b 7", "available", { payment_state: null }),
  ];
  it("searches the plot number ignoring case and spacing", () => {
    expect(filterPlots(plots, { q: "a-1" }).map((p) => p.id)).toEqual([1, 2]);
    expect(filterPlots(plots, { q: "  B   7 " }).map((p) => p.id)).toEqual([4]);
    expect(filterPlots(plots, { q: "zzz" })).toEqual([]);
  });
  it("filters by status and by payment state, and combines them", () => {
    expect(filterPlots(plots, { status: "sold" }).map((p) => p.id)).toEqual([2, 3]);
    expect(filterPlots(plots, { payment: "part_paid" }).map((p) => p.id)).toEqual([3]);
    expect(filterPlots(plots, { status: "sold", q: "10" }).map((p) => p.id)).toEqual([2]);
    expect(filterPlots(plots)).toHaveLength(4);
  });
});

describe("totals and labels", () => {
  it("counts each state", () => {
    const plots = [plot(1, "A", "available"), plot(2, "B", "sold", { payment_state: "fully_paid" }),
      plot(3, "C", "sold", { payment_state: "part_paid" }), plot(4, "D", "sold", { payment_state: "part_paid" })];
    expect(summarise(plots)).toEqual({ total: 4, available: 1, sold: 3, partPaid: 2, fullyPaid: 1 });
    expect(summarise([])).toEqual({ total: 0, available: 0, sold: 0, partPaid: 0, fullyPaid: 0 });
  });
  it("knows whether the caller was given payment detail at all", () => {
    expect(hasPaymentDetail([plot(1, "A")])).toBe(false);
    expect(hasPaymentDetail([plot(1, "A", "available", { payment_state: null })])).toBe(true);
    expect(hasPaymentDetail([])).toBe(false);
  });
  it("labels states in words", () => {
    expect([statusLabel("available"), statusLabel("sold"), paymentLabel("part_paid"), paymentLabel("fully_paid")])
      .toEqual(["Available", "Sold", "Part paid", "Fully paid"]);
  });
});
