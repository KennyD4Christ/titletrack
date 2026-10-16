import { api } from "./client.js";

export const listStaff = (params) => api.get("/api/staff/", params);
export const getStaffOptions = () => api.get("/api/staff/options/");
export const listDepartments = async () => (await api.get("/api/departments/", { page_size: 200 })).results;
export const createStaff = (payload) => api.post("/api/staff/", payload);
export const updateStaff = (id, payload) => api.patch(`/api/staff/${id}/`, payload);
export const deactivateStaff = (id, reason) => api.post(`/api/staff/${id}/deactivate/`, reason ? { reason } : {});
export const reactivateStaff = (id) => api.post(`/api/staff/${id}/reactivate/`);
export const getOpenObligations = (id) => api.get(`/api/staff/${id}/open-obligations/`);
export const getStaffHistory = (id, params) => api.get(`/api/staff/${id}/history/`, params);
export const issueSetupLink = (id, revealIfEmailFails = false) =>
  api.post(`/api/staff/${id}/setup-link/`, { reveal_if_email_fails: revealIfEmailFails });
export const issueResetLink = (id, revealIfEmailFails = false) =>
  api.post(`/api/staff/${id}/reset-link/`, { reveal_if_email_fails: revealIfEmailFails });
