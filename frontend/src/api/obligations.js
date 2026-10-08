import { api } from "./client.js";

export const listObligations = (params) => api.get("/api/obligations/", params);
export const getObligation = (id) => api.get(`/api/obligations/${id}/`);
export const getObligationEvents = (id) => api.get(`/api/obligations/${id}/events/`);
export const listEstates = async () => (await api.get("/api/estates/", { page_size: 200 })).results;
export const listObligationTypes = async () => (await api.get("/api/obligation-types/", { page_size: 200 })).results;
export const listUsers = async () => (await api.get("/api/users/", { page_size: 200 })).results;

export const updateObligation = (id, payload) => api.patch(`/api/obligations/${id}/`, payload);
export const bulkUpdateObligations = (payload) => api.post("/api/obligations/bulk-update/", payload);
