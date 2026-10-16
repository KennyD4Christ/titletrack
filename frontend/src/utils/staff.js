import { ROLE_LABELS } from "./labels.js";

/** How long each kind of link works (settled in the backend; shown here so people know). */
export const LINK_LIFETIME = { setup: "3 days", reset: "1 day" };

export function formatWhen(iso) {
  if (!iso) return "—";
  const date = new Date(iso);
  if (Number.isNaN(date.getTime())) return String(iso);
  return date.toLocaleString("en-GB", { day: "numeric", month: "short", year: "numeric", hour: "2-digit", minute: "2-digit" });
}

const FIELD_LABELS = {
  role: "Role", first_name: "First name", last_name: "Last name", department: "Department",
  is_active: "Active", expires_at: "Link expires",
};

function show(field, value) {
  if (value === null || value === undefined || value === "") return "—";
  if (field === "role") return ROLE_LABELS[value] || value;
  if (field === "is_active") return value ? "Yes" : "No";
  if (field === "expires_at") return formatWhen(value);
  return String(value);
}

/** {"role": ["sales", "viewer"]} -> ["Role: Sales → Viewer"]. A first value of null means "set to". */
export function describeChanges(changes = {}) {
  return Object.entries(changes).map(([field, pair]) => {
    const [before, after] = Array.isArray(pair) ? pair : [null, pair];
    const label = FIELD_LABELS[field] || field;
    return before === null || before === undefined
      ? `${label}: ${show(field, after)}`
      : `${label}: ${show(field, before)} → ${show(field, after)}`;
  });
}

export const plural = (count, word) => `${count} ${word}${count === 1 ? "" : "s"}`;

/** A friendly message for any failed call (the rate limit gets its own wording). */
export const errorMessage = (err) =>
  err?.status === 429 ? "Too many attempts. Please wait a while and try again." : err?.message || "Something went wrong.";
