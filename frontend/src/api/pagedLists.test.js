import { describe, expect, it } from "vitest";
import { mockApi } from "../test/helpers.jsx";
import { listAllActiveDocumentTypes, listAllDocumentsOfSale, listAllDocumentTypes } from "./pagedLists.js";

const pageOf = (url) => new URL(url, "http://localhost").searchParams.get("page");
const row = (n) => ({ id: n });

describe("every page of a list", () => {
  it("follows the catalogue's pages until there is no next one", async () => {
    const { calls } = mockApi({
      "GET /api/document-types/": (url) => (pageOf(url) === "1"
        ? { count: 3, next: "http://localhost/api/document-types/?page=2", previous: null, results: [row(1), row(2)] }
        : { count: 3, next: null, previous: "http://localhost/api/document-types/?page=1", results: [row(3)] }),
    });
    const data = await listAllActiveDocumentTypes();
    expect(data).toEqual({ count: 3, results: [row(1), row(2), row(3)] });
    expect(calls.map((c) => c.url)).toEqual([
      "/api/document-types/?is_active=1&page=1&page_size=200",
      "/api/document-types/?is_active=1&page=2&page_size=200",
    ]);
  });

  it("follows one sale's documents the same way, naming the sale on every page", async () => {
    const { calls } = mockApi({
      "GET /api/sale-documents/": (url) => (pageOf(url) === "1"
        ? { count: 2, next: "http://localhost/api/sale-documents/?page=2", previous: null, results: [row(1)] }
        : { count: 2, next: null, previous: "http://localhost/api/sale-documents/?page=1", results: [row(2)] }),
    });
    const data = await listAllDocumentsOfSale(612);
    expect(data).toEqual({ count: 2, results: [row(1), row(2)] });
    expect(calls.map((c) => c.url)).toEqual([
      "/api/sale-documents/?sale=612&page=1&page_size=200",
      "/api/sale-documents/?sale=612&page=2&page_size=200",
    ]);
  });

  it("refuses to list without a sale id, instead of asking for every sale's documents", async () => {
    const { calls } = mockApi({ "GET /api/sale-documents/": { count: 0, next: null, previous: null, results: [] } });
    for (const missing of [undefined, null, ""]) {
      await expect(listAllDocumentsOfSale(missing)).rejects.toThrow("needs a sale id");
    }
    expect(calls).toHaveLength(0);
  });

  it("stops after a fixed number of pages and leaves count larger than the rows it returned", async () => {
    let n = 0;
    const { calls } = mockApi({
      "GET /api/document-types/": () => ({
        count: 9999, next: "http://localhost/api/document-types/?page=more", previous: null, results: [row(++n)],
      }),
    });
    const data = await listAllActiveDocumentTypes();
    expect(calls).toHaveLength(50);
    expect(data.results).toHaveLength(50);
    expect(data.count).toBe(9999);                                   // a caller can see it is incomplete
  });
  it("reads the whole catalogue, active or not, without a filter", async () => {
    const { calls } = mockApi({
      "GET /api/document-types/": (url) => (pageOf(url) === "1"
        ? { count: 2, next: "http://localhost/api/document-types/?page=2", previous: null, results: [row(1)] }
        : { count: 2, next: null, previous: "http://localhost/api/document-types/?page=1", results: [row(2)] }),
    });
    const data = await listAllDocumentTypes();
    expect(data).toEqual({ count: 2, results: [row(1), row(2)] });
    expect(calls.map((c) => c.url)).toEqual([
      "/api/document-types/?page=1&page_size=200",
      "/api/document-types/?page=2&page_size=200",
    ]);
  });
});
