import { formatDate, formatNaira } from "./format.js";

export const PAYMENT_KIND_LABELS = {
  payment: "Payment",
  reversal: "Reversal",
  opening: "Opening balance",
  adjustment: "Adjustment",
};

export const SALE_STATUS_LABELS = { active: "Active", cancelled: "Cancelled" };

export const SALE_FIELD_LABELS = {
  price: "Price",
  amount_paid: "Amount paid",
  fully_paid_on: "Fully paid on",
  status: "Status",
  full_name: "Name",
  phone: "Phone",
  email: "Email",
};

const MONEY_FIELDS = new Set(["price", "amount_paid"]);

/** How a before/after value from the sale history is shown. */
export function changeValue(field, value) {
  if (value === null || value === undefined || value === "") return "—";
  if (MONEY_FIELDS.has(field)) return formatNaira(value);
  if (field === "fully_paid_on") return formatDate(value);
  if (field === "status") return SALE_STATUS_LABELS[value] || value;
  return String(value);
}

/** One key per opening of a payment form: a retry of the same form can never record a second payment. */
export const newRequestKey = () =>
  globalThis.crypto?.randomUUID
    ? globalThis.crypto.randomUUID()
    : `${Date.now()}-${Math.random().toString(36).slice(2, 12)}`;

const pad = (n) => String(n).padStart(2, "0");

/** Today's date on this computer, as YYYY-MM-DD. */
export function todayIso() {
  const d = new Date();
  return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}`;
}

/** "₦1,500,000" or "1,500,000.50" as a number; NaN when it is not a number. */
export const toAmount = (text) => {
  const cleaned = String(text ?? "").replace(/[₦,\s]/g, "");
  return cleaned === "" ? NaN : Number(cleaned);
};
