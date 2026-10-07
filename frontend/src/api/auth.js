import { api } from "./client.js";

export const fetchMe = () => api.get("/api/auth/me/");
export const login = (username, password) => api.post("/api/auth/login/", { username, password });
export const logout = () => api.post("/api/auth/logout/");
