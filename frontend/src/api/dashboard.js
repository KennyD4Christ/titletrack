import { api } from "./client.js";

export const getHome = () => api.get("/api/dashboard/home/");
export const getMyQueue = () => api.get("/api/me/queue/");
