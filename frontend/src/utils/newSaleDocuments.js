// Helpers for choosing the documents of a sale while it is added (the Add sale form). Kept apart from the
// component so they can be tested on their own. The server is the authority on every rule: these only prepare
// the request and read its answer (POST /api/sales-desk/, see tracker/sale_document_services.py).

export const AUTOMATIC_NOTE =
  "Documents are created automatically, as before: some when the sale is added, the rest when the buyer has paid in full.";
export const CHOOSE_HINT =
  "Only the documents you choose are created for this sale; the automatic documents are not added. Preparation for an owed document starts on the sale date.";

/** The active catalogue entries, sorted by name (the order the add-document dialog uses). */
export const sortedActiveTypes = (types = []) =>
  types.filter((t) => t.is_active).sort((a, b) => a.name.localeCompare(b.name));

/** What a document looks like the moment it is ticked: still owed, with nothing filled in. */
export const startingChoice = () => ({ state: "owed", issued_on: "", reference: "" });

/** The `documents` list of the request: one entry per ticked document, in the order the types are listed.
 *  Never an obligation or a company: the server finds or creates the work and uses its own company.
 *  `canIssue` false (no issue_document permission) sends every document as owed. */
export function buildDocuments(types, choices, canIssue = true) {
  const documents = [];
  for (const type of types) {
    const choice = choices[type.id];
    if (!choice) continue;
    const issued = canIssue && choice.state === "issued";
    const document = { document_type: type.id, state: issued ? "issued" : "owed" };
    if (issued) {
      document.issued_on = choice.issued_on;
      const reference = (choice.reference || "").trim();
      if (reference) document.reference = reference;
    }
    documents.push(document);
  }
  return documents;
}

/** Why the form cannot be submitted while documents are being chosen, or null when it can.
 *  `status` is the catalogue query's status: "pending", "error" or "success". */
export function documentsProblem({ status, types, choices }) {
  if (status === "error") return "The document types could not be loaded. Try again, or use the automatic documents.";
  if (status !== "success") return "The document types are still loading. Wait for them, or use the automatic documents.";
  if (types.length === 0) return "There are no active document types to choose. Use the automatic documents instead.";
  const chosen = types.filter((t) => choices[t.id]);
  if (chosen.length === 0) return "Choose at least one document, or use the automatic documents.";
  if (chosen.some((t) => choices[t.id].state === "issued" && !choices[t.id].issued_on)) {
    return "Enter the issue date of every document marked as already issued.";
  }
  return null;
}

const messages = (value) => {
  if (typeof value === "string") return [value];
  if (Array.isArray(value)) return value.flatMap(messages);
  if (value && typeof value === "object") return Object.values(value).flatMap(messages);
  return [];
};

/** Reads the server's answer about the documents: {"documents": {"<position>": {field: [message]}}} (the way DRF
 *  reports a bad item in a list: only the positions with a problem appear, and JSON turns them into text keys).
 *  A position is the place in the list that was SENT, so `sentTypeIds` (the ids in that order) says which
 *  document each one is. Returns {rows: {typeId: {field: [message]}}, general: [message]}, or null when the
 *  answer is not about the documents at all. A problem with the list as a whole ("non_field_errors", or a
 *  position this form never sent) goes into `general`. An older DRF answers with a list instead: handled too. */
export function documentFailure(error, sentTypeIds) {
  const raw = error?.data?.documents;
  if (!raw || typeof raw !== "object") return null;
  const entries = Array.isArray(raw) ? raw.map((value, index) => [String(index), value]) : Object.entries(raw);
  const rows = {};
  const general = [];
  for (const [key, value] of entries) {
    const typeId = /^\d+$/.test(key) ? sentTypeIds[Number(key)] : undefined;
    if (typeId !== undefined && value && typeof value === "object" && !Array.isArray(value)) {
      if (Object.keys(value).length > 0) rows[typeId] = value;      // {} means this document was fine
    } else {
      general.push(...messages(value));
    }
  }
  return Object.keys(rows).length > 0 || general.length > 0 ? { rows, general } : null;
}

/** The same failure without one document's message, once that document has been changed. */
export function withoutRow(failure, typeId) {
  if (!failure || !failure.rows[typeId]) return failure;
  const rows = { ...failure.rows };
  delete rows[typeId];
  return { ...failure, rows };
}
