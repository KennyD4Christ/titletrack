// What a person sees for one sale document. These are the values the API's `display_status` can take
// (api/serializers/sale_documents.py); the frontend adds no statuses of its own.
// A document's display status is NOT the status of its obligation: obligation statuses are in utils/labels.js.
export const DOCUMENT_STATUS_LABELS = {
  outstanding: "Outstanding",
  in_progress: "In progress",
  ready_to_issue: "Ready to issue",
  issued: "Issued",
  not_required: "Not required",
  needs_review: "Needs review",
};

export const OVERDUE_LABEL = "Overdue";
/** The existing overdue badge style from app.css. */
export const OVERDUE_BADGE_CLASS = "badge badge-overdue";

export const documentStatusLabel = (status) => DOCUMENT_STATUS_LABELS[status] || status;

/** The class prefix is "badge-doc-" so a document status never shares a style with an obligation status
 *  (both have an "in_progress"). A value the API might add later gets the plain badge and its raw text. */
export const documentBadgeClass = (status) =>
  DOCUMENT_STATUS_LABELS[status] ? `badge badge-doc-${status}` : "badge";

/** True when the catalogue entry has been renamed since the document was added to the sale. */
export const catalogueNameDiffers = (doc) => Boolean(doc.catalogue_name) && doc.catalogue_name !== doc.name;
