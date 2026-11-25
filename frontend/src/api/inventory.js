import { api } from "./client.js";

/** Every plot of one estate with its derived state. MD and Manager also get payment_state and active_sale_id;
 *  every other role gets only id, plot_number and status. Live data: always read it fresh. */
export const getPlotBoard = (estateId) => api.get("/api/plot-board/", { estate: estateId });

export const createEstate = (payload) => api.post("/api/inventory/estates/", payload);
/** Send only the fields that changed, plus `reason` (needed to rename an estate that has sales). */
export const editEstate = (id, payload) => api.patch(`/api/inventory/estates/${id}/`, payload);

export const registerPlot = (payload) => api.post("/api/inventory/plots/", payload);
/** {plot_number, reason?}: a plot that has ever had a sale needs a reason. */
export const renamePlot = (id, payload) => api.patch(`/api/inventory/plots/${id}/`, payload);
/** {reason}: only a plot that never had a sale (cancelled sales count) can be removed. */
export const removePlot = (id, payload) => api.post(`/api/inventory/plots/${id}/remove/`, payload);

/** {estate, entries} -> the outcome of every label and two fingerprints. Writes nothing. */
export const previewBulk = (payload) => api.post("/api/inventory/plots/bulk-preview/", payload);
/** {estate, entries, input_fingerprint, preview_fingerprint}: the entries must be exactly the previewed ones.
 *  409 means the estate changed since the preview; the answer carries the fresh preview in `preview`. */
export const commitBulk = (payload) => api.post("/api/inventory/plots/bulk-commit/", payload);
