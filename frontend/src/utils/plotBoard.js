// Pure helpers for the plot board: ordering, filtering and counting. No React, so they are easy to test.

const collator = new Intl.Collator("en", { numeric: true, sensitivity: "base" });

/** Natural order: A-2 before A-10, whatever the case. The server sends plots in key order, where 10 sorts before 2. */
export const naturalCompare = (a, b) => collator.compare(String(a), String(b));

export const sortPlots = (plots) =>
  [...plots].sort((a, b) => naturalCompare(a.plot_number, b.plot_number) || a.id - b.id);

export const STATUS_LABELS = { available: "Available", sold: "Sold" };
export const PAYMENT_LABELS = { part_paid: "Part paid", fully_paid: "Fully paid" };
export const statusLabel = (status) => STATUS_LABELS[status] || status;
export const paymentLabel = (state) => PAYMENT_LABELS[state] || state;

/** Only MD and Manager receive payment_state; for every other role the key does not exist. */
export const hasPaymentDetail = (plots) => plots.some((plot) => "payment_state" in plot);

const squash = (text) => String(text || "").replace(/\s+/g, " ").trim().toLowerCase();

/** `q` is a case-insensitive, spacing-insensitive match on the plot number. `status` and `payment` are exact. */
export function filterPlots(plots, { q = "", status = "", payment = "" } = {}) {
  const needle = squash(q);
  return plots.filter((plot) => {
    if (needle && !squash(plot.plot_number).includes(needle)) return false;
    if (status && plot.status !== status) return false;
    if (payment && plot.payment_state !== payment) return false;
    return true;
  });
}

export function summarise(plots) {
  const count = (test) => plots.filter(test).length;
  return {
    total: plots.length,
    available: count((p) => p.status === "available"),
    sold: count((p) => p.status === "sold"),
    partPaid: count((p) => p.payment_state === "part_paid"),
    fullyPaid: count((p) => p.payment_state === "fully_paid"),
  };
}
