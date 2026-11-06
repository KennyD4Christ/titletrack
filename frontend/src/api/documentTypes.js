import { api } from "./client.js";

/** Add a document type to the catalogue (POST /api/document-types/).
 *  Payload: {name, category?, description?, sla_days?, default_department?, sort_order?}. A field left out takes
 *  the server's default. The server also creates the entry's first work type. */
export const createDocumentType = (payload) => api.post("/api/document-types/", payload);

/** Change a document type's details (PATCH /api/document-types/{id}/). Send only what changed: description,
 *  category, sort_order, sla_days, default_department (null clears it). The name has its own action, rename. */
export const updateDocumentType = (id, payload) => api.patch(`/api/document-types/${id}/`, payload);

/** Rename a document type (POST /api/document-types/{id}/rename/). Payload: {name}. */
export const renameDocumentType = (id, payload) => api.post(`/api/document-types/${id}/rename/`, payload);

/** Offer a document type again when adding documents to sales (POST /api/document-types/{id}/activate/). No input. */
export const activateDocumentType = (id) => api.post(`/api/document-types/${id}/activate/`);

/** Stop offering a document type for new documents (POST /api/document-types/{id}/deactivate/). No input. It never
 *  changes documents already on sales, existing obligations, or automatic generation on existing sales. */
export const deactivateDocumentType = (id) => api.post(`/api/document-types/${id}/deactivate/`);
