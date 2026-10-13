import { api } from "./client.js";

export const listSales = (params) => api.get("/api/sales-desk/", params);
export const getSale = (id) => api.get(`/api/sales-desk/${id}/`);
export const searchBuyers = (q) => api.get("/api/sales-desk/buyer-search/", { q });
export const createSale = (payload) => api.post("/api/sales-desk/", payload);
export const recordPayment = (id, payload) => api.post(`/api/sales-desk/${id}/record-payment/`, payload);
export const reversePayment = (id, paymentId, payload) =>
  api.post(`/api/sales-desk/${id}/payments/${paymentId}/reverse/`, payload);
export const editBuyer = (id, payload) => api.post(`/api/sales-desk/${id}/edit-buyer/`, payload);
export const cancelSale = (id, payload) => api.post(`/api/sales-desk/${id}/cancel/`, payload);
export const reinstateSale = (id, payload) => api.post(`/api/sales-desk/${id}/reinstate/`, payload);
