import { describe, expect, it } from "vitest";
import {
  buildDocuments, documentFailure, documentsProblem, sortedActiveTypes, startingChoice, withoutRow,
} from "./newSaleDocuments.js";

const types = [
  { id: 3, name: "Deed of Sublease", is_active: true },
  { id: 1, name: "Building Permit", is_active: true },
  { id: 9, name: "Old Transfer Form", is_active: false },
  { id: 2, name: "Contract of Sale", is_active: true },
];
const sorted = sortedActiveTypes(types);                       // Building Permit (1), Contract of Sale (2), Deed (3)
const failure = (data) => documentFailure({ data }, [1, 2, 3]);

describe("sortedActiveTypes", () => {
  it("drops inactive types, sorts by name and leaves the original list alone", () => {
    expect(sorted.map((t) => t.name)).toEqual(["Building Permit", "Contract of Sale", "Deed of Sublease"]);
    expect(types.map((t) => t.id)).toEqual([3, 1, 9, 2]);
  });

  it("copes with no list at all", () => {
    expect(sortedActiveTypes()).toEqual([]);
  });
});

describe("buildDocuments", () => {
  it("sends one entry per ticked document in the order the types are listed, not the order they were ticked", () => {
    const choices = { 3: startingChoice(), 1: startingChoice() };
    expect(buildDocuments(sorted, choices)).toEqual([
      { document_type: 1, state: "owed" },
      { document_type: 3, state: "owed" },
    ]);
  });

  it("sends an issued document with its date and a trimmed reference, and leaves out a blank reference", () => {
    const choices = {
      1: { state: "issued", issued_on: "2026-11-02", reference: "  CofO-77 " },
      2: { state: "issued", issued_on: "2026-11-03", reference: "   " },
    };
    expect(buildDocuments(sorted, choices)).toEqual([
      { document_type: 1, state: "issued", issued_on: "2026-11-02", reference: "CofO-77" },
      { document_type: 2, state: "issued", issued_on: "2026-11-03" },
    ]);
  });

  it("never sends issue details for an owed document, even ones typed before the status was switched back", () => {
    const choices = { 1: { state: "owed", issued_on: "2026-11-02", reference: "X" } };
    expect(buildDocuments(sorted, choices)).toEqual([{ document_type: 1, state: "owed" }]);
  });

  it("sends everything as owed when the person may not record issuance", () => {
    const choices = { 1: { state: "issued", issued_on: "2026-11-02", reference: "X" } };
    expect(buildDocuments(sorted, choices, false)).toEqual([{ document_type: 1, state: "owed" }]);
  });

  it("ignores a choice for a type that is no longer listed, and never names an obligation or a company", () => {
    const documents = buildDocuments(sorted, { 1: startingChoice(), 99: startingChoice() });
    expect(documents).toEqual([{ document_type: 1, state: "owed" }]);
    expect(Object.keys(documents[0])).toEqual(["document_type", "state"]);
  });

  it("sends nothing when nothing is ticked", () => {
    expect(buildDocuments(sorted, {})).toEqual([]);
  });
});

describe("documentsProblem", () => {
  const ok = { status: "success", types: sorted };

  it("explains a catalogue that is loading, failed or empty", () => {
    expect(documentsProblem({ ...ok, status: "pending", choices: {} })).toMatch(/still loading/);
    expect(documentsProblem({ ...ok, status: "error", choices: {} })).toMatch(/could not be loaded/);
    expect(documentsProblem({ status: "success", types: [], choices: {} })).toMatch(/no active document types/);
  });

  it("needs at least one ticked document", () => {
    expect(documentsProblem({ ...ok, choices: {} })).toMatch(/at least one document/);
    expect(documentsProblem({ ...ok, choices: { 99: startingChoice() } })).toMatch(/at least one document/);
  });

  it("needs an issue date for every issued document", () => {
    const choices = { 1: startingChoice(), 2: { state: "issued", issued_on: "", reference: "" } };
    expect(documentsProblem({ ...ok, choices })).toMatch(/issue date/);
  });

  it("is satisfied by owed documents and by issued ones with a date", () => {
    expect(documentsProblem({ ...ok, choices: { 1: startingChoice() } })).toBeNull();
    const choices = { 2: { state: "issued", issued_on: "2026-11-02", reference: "" } };
    expect(documentsProblem({ ...ok, choices })).toBeNull();
  });
});

describe("documentFailure", () => {
  it("maps each position of the server's answer to the document that was sent there", () => {
    const result = failure({
      documents: { 1: { document_type: ["This document type is inactive."] }, 2: { issued_on: ["Required."] } },
    });
    expect(result).toEqual({
      rows: { 2: { document_type: ["This document type is inactive."] }, 3: { issued_on: ["Required."] } },
      general: [],
    });
  });

  it("reads the list form of an older DRF, skipping the empty entries for documents that were fine", () => {
    const result = failure({ documents: [{}, { reference: ["Too long."] }, {}] });
    expect(result).toEqual({ rows: { 2: { reference: ["Too long."] } }, general: [] });
  });

  it("keeps a problem with the list as a whole, or with a position that was not sent, as a general message", () => {
    expect(failure({ documents: { non_field_errors: ["Ensure this field has no more than 50 elements."] } }))
      .toEqual({ rows: {}, general: ["Ensure this field has no more than 50 elements."] });
    expect(failure({ documents: { 7: { state: ["Nope."] } } })).toEqual({ rows: {}, general: ["Nope."] });
    expect(failure({ documents: ["This field may not be null."] }))
      .toEqual({ rows: {}, general: ["This field may not be null."] });
  });

  it("is null for an answer that is not about the documents", () => {
    expect(failure({ detail: "You do not have permission to add documents to a sale." })).toBeNull();
    expect(failure({ price: ["A valid number is required."] })).toBeNull();
    expect(failure({ documents: {} })).toBeNull();
    expect(failure({ documents: { 0: {} } })).toBeNull();
    expect(documentFailure(null, [1])).toBeNull();
    expect(documentFailure(new Error("x"), [1])).toBeNull();
  });
});

describe("withoutRow", () => {
  it("removes one document's message and keeps the rest", () => {
    const before = { rows: { 1: { state: ["a"] }, 2: { reference: ["b"] } }, general: ["g"] };
    expect(withoutRow(before, 1)).toEqual({ rows: { 2: { reference: ["b"] } }, general: ["g"] });
    expect(before.rows[1]).toBeDefined();                      // the original is not changed
  });

  it("returns the same failure when there is nothing to remove, and null for no failure", () => {
    const before = { rows: { 2: { reference: ["b"] } }, general: [] };
    expect(withoutRow(before, 5)).toBe(before);
    expect(withoutRow(null, 1)).toBeNull();
  });
});
