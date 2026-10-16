import { api } from "./client.js";

// The one-time token travels in the request body (never in a URL).
export const setPassword = (token, password) => api.post("/api/auth/set-password/", { token, password });
export const resetPassword = (token, password) => api.post("/api/auth/reset-password/", { token, password });
export const changePassword = (current_password, new_password) =>
  api.post("/api/auth/change-password/", { current_password, new_password });
