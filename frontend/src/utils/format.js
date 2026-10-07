const naira = new Intl.NumberFormat("en-NG", {
  style: "currency",
  currency: "NGN",
  minimumFractionDigits: 0,
  maximumFractionDigits: 0,
});

export const toNumber = (value) => (value === null || value === undefined || value === "" ? 0 : Number(value));

/** ₦5,528,950,000 */
export const formatNaira = (value) => naira.format(toNumber(value));

const trim = (x) => String(Math.round(x * 100) / 100);

/** ₦5.53bn, ₦18.75m, ₦250k: for tiles and tables where space is tight. */
export function formatNairaCompact(value) {
  const n = toNumber(value);
  const abs = Math.abs(n);
  if (abs >= 1e9) return `₦${trim(n / 1e9)}bn`;
  if (abs >= 1e6) return `₦${trim(n / 1e6)}m`;
  if (abs >= 1e3) return `₦${trim(n / 1e3)}k`;
  return `₦${Math.round(n)}`;
}

const dateOnly = (iso) => (iso.length <= 10 ? new Date(`${iso}T00:00:00`) : new Date(iso));

/** 5 Oct 2026 */
export function formatDate(iso) {
  if (!iso) return "—";
  return dateOnly(iso).toLocaleDateString("en-GB", { day: "numeric", month: "short", year: "numeric" });
}

/** 5 Oct 2026, 14:30 */
export function formatDateTime(iso) {
  if (!iso) return "—";
  const d = new Date(iso);
  return `${formatDate(iso)}, ${d.toLocaleTimeString("en-GB", { hour: "2-digit", minute: "2-digit" })}`;
}

export const plural = (n, word) => `${n} ${word}${n === 1 ? "" : "s"}`;
export const days = (n) => (n === null || n === undefined ? "—" : plural(n, "day"));
export const formatNumber = (n) => new Intl.NumberFormat("en-NG").format(toNumber(n));
