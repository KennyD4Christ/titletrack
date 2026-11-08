import { api } from "./client.js";

/** One sale's documents. The server pages at 50 by default and allows at most 200 per page; asking for the
 *  largest page means a sale's documents are not split. The caller should still compare `count` with the
 *  number of `results` returned. Returns the raw paginated response: {count, next, previous, results}.
 *
 *  A sale id is required. The request helper drops empty query values, and without `sale` the server would
 *  answer with documents from every active sale in the company, so a missing id is refused here instead. */
export const listSaleDocuments = (saleId) => {
  if (saleId === undefined || saleId === null || saleId === "") {
    return Promise.reject(new Error("listSaleDocuments needs a sale id."));
  }
  return api.get("/api/sale-documents/", { sale: saleId, page_size: 200 });
};

/** Add a document to a sale. Payload: {sale, document_type, state: "owed" | "issued", issued_on?, reference?}.
 *  There is deliberately no obligation field: the server finds or creates the right obligation. */
export const addSaleDocument = (payload) => api.post("/api/sale-documents/", payload);

/** Record issuance. Payload: {issued_on (required), reference?}. */
export const issueDocument = (id, payload) => api.post(`/api/sale-documents/${id}/issue/`, payload);

/** Mark a document not applicable. Payload: {reason}. */
export const markDocumentNotApplicable = (id, payload) =>
  api.post(`/api/sale-documents/${id}/mark-not-applicable/`, payload);

/** Correct an issued document. Payload: {reason, issued_on?, reference?}. Leave a field out to keep it;
 *  send reference "" to clear it. */
export const correctDocumentIssuance = (id, payload) =>
  api.post(`/api/sale-documents/${id}/correct-issuance/`, payload);
