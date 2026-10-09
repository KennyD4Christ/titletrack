import { api } from "./client.js";

export const listImports = () => api.get("/api/imports/", { page_size: 50 });

export function uploadImport(file, { sheet, headerRow } = {}) {
  const form = new FormData();
  form.append("file", file);
  if (sheet) form.append("sheet", sheet);
  if (headerRow) form.append("header_row", String(headerRow));
  return api.post("/api/imports/", form);
}

export const saveMapping = (id, mapping) => api.post(`/api/imports/${id}/mapping/`, { mapping });
export const saveStatusMapping = (id, statusMapping) => api.post(`/api/imports/${id}/status-mapping/`, { status_mapping: statusMapping });
export const runDryRun = (id) => api.post(`/api/imports/${id}/dry-run/`);
export const getIssuesCsv = (id) => api.get(`/api/imports/${id}/errors.csv/`);
export const commitImport = (id) => api.post(`/api/imports/${id}/commit/`, { confirm: true });
export const undoImport = (id) => api.post(`/api/imports/${id}/undo/`);
