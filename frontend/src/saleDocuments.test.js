import { beforeEach, describe, expect, it } from "vitest";
import { json, mockApi } from "./test/helpers.jsx";
import {
  addSaleDocument, correctDocumentIssuance, issueDocument, listSaleDocuments, markDocumentNotApplicable,
} from "./api/saleDocuments.js";
import {
  DOCUMENT_STATUS_LABELS, OVERDUE_BADGE_CLASS, OVERDUE_LABEL, catalogueNameDiffers, documentBadgeClass,
  documentStatusLabel,
} from "./utils/documents.js";

import saleDocuments from "./test/fixtures/sale_documents.json";

// The fields api/serializers/sale_documents.py returns for one document, and the keys of its `can`.
const ROW_KEYS = [
  "id", "sale", "sale_status", "buyer_name", "plot_number", "estate_name", "document_type", "name",
  "catalogue_name", "state", "display_status", "is_overdue", "issued_on", "reference", "issued_by_name",
  "issued_at", "not_applicable_reason", "not_applicable_by_name", "not_applicable_at", "obligation",
  "obligation_status", "due_date", "owner_name", "added_by_name", "added_at", "can",
];
const CAN_KEYS = ["correct_issuance", "issue", "mark_not_applicable"];
const DISPLAY_STATUSES = ["issued", "not_required", "outstanding", "in_progress", "ready_to_issue", "needs_review"];

const body = (call) => JSON.parse(call.init.body);
const row = saleDocuments.results[0];

describe("sale documents API module", () => {
  beforeEach(() => {
    document.cookie = "csrftoken=abc; path=/";
  });

  it("lists one sale's documents, asking for the largest page", async () => {
    const { calls } = mockApi({ "GET /api/sale-documents/": saleDocuments });
    const data = await listSaleDocuments(612);
    expect(calls).toHaveLength(1);
    expect(calls[0].url).toBe("/api/sale-documents/?sale=612&page_size=200");
    expect(calls[0].init.credentials).toBe("include");
    expect(calls[0].init.headers["X-CSRFToken"]).toBeUndefined();
    expect(data).toEqual(saleDocuments);
  });

  it("refuses to list without a sale id, instead of asking for every sale's documents", async () => {
    const { calls } = mockApi({ "GET /api/sale-documents/": saleDocuments });
    for (const missing of [undefined, null, ""]) {
      await expect(listSaleDocuments(missing)).rejects.toThrow("needs a sale id");
    }
    expect(calls).toHaveLength(0);
  });

  it("adds an owed document with exactly the fields given, and no obligation", async () => {
    const { calls } = mockApi({ "POST /api/sale-documents/": () => json(row, 201) });
    await addSaleDocument({ sale: 612, document_type: 41, state: "owed" });
    expect(calls[0].url).toBe("/api/sale-documents/");
    expect(calls[0].init.headers["X-CSRFToken"]).toBe("abc");
    expect(body(calls[0])).toEqual({ sale: 612, document_type: 41, state: "owed" });
    expect(body(calls[0])).not.toHaveProperty("obligation");
  });

  it("adds an already-issued document with its date and reference", async () => {
    const { calls } = mockApi({ "POST /api/sale-documents/": () => json(row, 201) });
    await addSaleDocument({ sale: 612, document_type: 44, state: "issued", issued_on: "2026-10-20", reference: "REF-1042" });
    expect(body(calls[0])).toEqual({
      sale: 612, document_type: 44, state: "issued", issued_on: "2026-10-20", reference: "REF-1042",
    });
  });

  it("records issuance at the issue endpoint", async () => {
    const { calls } = mockApi({ "POST /api/sale-documents/3003/issue/": row });
    await issueDocument(3003, { issued_on: "2026-11-02", reference: "TRF 1" });
    expect(calls[0].method).toBe("POST");
    expect(calls[0].url).toBe("/api/sale-documents/3003/issue/");
    expect(body(calls[0])).toEqual({ issued_on: "2026-11-02", reference: "TRF 1" });
  });

  it("marks a document not applicable at its own endpoint, with the reason", async () => {
    const { calls } = mockApi({ "POST /api/sale-documents/3001/mark-not-applicable/": row });
    await markDocumentNotApplicable(3001, { reason: "Buyer already holds it" });
    expect(calls[0].url).toBe("/api/sale-documents/3001/mark-not-applicable/");
    expect(body(calls[0])).toEqual({ reason: "Buyer already holds it" });
  });

  it("corrects issuance, sending a cleared reference as an empty string", async () => {
    const { calls } = mockApi({ "POST /api/sale-documents/3004/correct-issuance/": row });
    await correctDocumentIssuance(3004, { reason: "No reference exists", reference: "" });
    expect(calls[0].url).toBe("/api/sale-documents/3004/correct-issuance/");
    expect(body(calls[0])).toEqual({ reason: "No reference exists", reference: "" });
  });

  it("keeps a refusal keyed by the field it concerns, so a form can show it beside that field", async () => {
    mockApi({
      "POST /api/sale-documents/": () => json({ document_type: ["This sale already has this document."] }, 409),
    });
    const error = await addSaleDocument({ sale: 612, document_type: 41, state: "owed" }).catch((e) => e);
    expect(error.status).toBe(409);
    expect(error.message).toBe("document_type: This sale already has this document.");
    expect(error.fieldErrors).toEqual({ document_type: ["This sale already has this document."] });
  });

  it("shows a message that is not tied to a field as the plain message", async () => {
    mockApi({ "POST /api/sale-documents/3004/correct-issuance/": () => json({ detail: "Nothing to change." }, 400) });
    const error = await correctDocumentIssuance(3004, { reason: "Checked it" }).catch((e) => e);
    expect(error.status).toBe(400);
    expect(error.message).toBe("Nothing to change.");
    expect(error.fieldErrors).toEqual({});
  });
});

describe("sale documents fixture", () => {
  it("is a paginated response whose count matches its rows", () => {
    expect(Object.keys(saleDocuments).sort()).toEqual(["count", "next", "previous", "results"]);
    expect(saleDocuments.count).toBe(saleDocuments.results.length);
  });

  it("gives every document exactly the serializer's fields and the three `can` keys", () => {
    for (const doc of saleDocuments.results) {
      expect(Object.keys(doc).sort(), doc.name).toEqual([...ROW_KEYS].sort());
      expect(Object.keys(doc.can).sort(), doc.name).toEqual(CAN_KEYS);
    }
    expect(ROW_KEYS).toHaveLength(26);
  });

  it("covers every display status, an overdue document, and a document with no obligation", () => {
    const statuses = new Set(saleDocuments.results.map((d) => d.display_status));
    expect([...statuses].sort()).toEqual([...DISPLAY_STATUSES].sort());
    expect(saleDocuments.results.filter((d) => d.is_overdue)).toHaveLength(1);
    expect(saleDocuments.results.filter((d) => d.obligation === null)).toHaveLength(1);
  });
});

describe("document labels", () => {
  it("labels exactly the six display statuses the API can return", () => {
    expect(Object.keys(DOCUMENT_STATUS_LABELS).sort()).toEqual([...DISPLAY_STATUSES].sort());
    expect(documentStatusLabel("ready_to_issue")).toBe("Ready to issue");
    expect(documentStatusLabel("not_required")).toBe("Not required");
    expect(documentStatusLabel("needs_review")).toBe("Needs review");
    expect(documentStatusLabel("something_new")).toBe("something_new");
    expect(OVERDUE_LABEL).toBe("Overdue");
    expect(OVERDUE_BADGE_CLASS).toBe("badge badge-overdue");
  });

  it("keeps document badge styles apart from obligation badge styles", () => {
    expect(documentBadgeClass("in_progress")).toBe("badge badge-doc-in_progress");
    expect(documentBadgeClass("issued")).toBe("badge badge-doc-issued");
    expect(documentBadgeClass("something_new")).toBe("badge");
  });

  it("notices a catalogue entry renamed since the document was added", () => {
    const byName = Object.fromEntries(saleDocuments.results.map((d) => [d.name, d]));
    expect(catalogueNameDiffers(byName["Survey Plan"])).toBe(true);
    expect(catalogueNameDiffers(byName["Contract of Sale"])).toBe(false);
    expect(catalogueNameDiffers({ name: "X", catalogue_name: null })).toBe(false);
  });
});
