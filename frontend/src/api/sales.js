import { api } from "./client.js";

export const listSales = (params) => api.get("/api/sales-desk/", params);
export const getSale = (id) => api.get(`/api/sales-desk/${id}/`);
export const searchBuyers = (q) => api.get("/api/sales-desk/buyer-search/", { q });
/** Add a sale (POST /api/sales-desk/). Optional `documents`: leave it out (or send []) and the automatic
 *  documents are created as before; never send null, it is refused. A non-empty list is
 *  [{document_type, state: "owed" | "issued", issued_on?, reference?}] and creates the sale with exactly those
 *  documents, in one transaction. A problem with one document comes back as
 *  {"documents": {"<position in the list sent>": {field: [message]}}}: 400 for bad input, 409 for an inactive type. */
export const createSale = (payload) => api.post("/api/sales-desk/", payload);
export const recordPayment = (id, payload) => api.post(`/api/sales-desk/${id}/record-payment/`, payload);
export const reversePayment = (id, paymentId, payload) =>
  api.post(`/api/sales-desk/${id}/payments/${paymentId}/reverse/`, payload);
export const editBuyer = (id, payload) => api.post(`/api/sales-desk/${id}/edit-buyer/`, payload);
export const cancelSale = (id, payload) => api.post(`/api/sales-desk/${id}/cancel/`, payload);
export const reinstateSale = (id, payload) => api.post(`/api/sales-desk/${id}/reinstate/`, payload);
