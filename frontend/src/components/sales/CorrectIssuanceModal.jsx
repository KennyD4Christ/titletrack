import { useState } from "react";
import { correctDocumentIssuance } from "../../api/saleDocuments.js";
import Modal from "../common/Modal.jsx";
import { formatDateTime } from "../../utils/format.js";
import { todayIso } from "../../utils/sales.js";

// The inputs this form shows a server message beside. Any other refusal goes in the form-level box.
const FIELDS = ["issued_on", "reference", "reason"];
const MIN_REASON = 3;          // the server's own minimum (MIN_REASON in tracker/sale_document_services.py)
const REASON_LIMIT = 1000;     // and maximum (REASON_LIMIT)
const REFERENCE_LIMIT = 120;   // (REFERENCE_LIMIT)

/** The server's own tidying of text (_clean_text in tracker/services.py): runs of whitespace become one space
 *  and the ends are trimmed. Comparing the same way means "no real change" is spotted here as well. */
const clean = (text) => String(text ?? "").replace(/\s+/g, " ").trim();

/** Correct the issue date and/or reference of an issued document (POST /api/sale-documents/{id}/correct-issuance/).
 *  The form starts with the record's current values. It sends the reason, and only the fields that really changed:
 *  a field left out is kept by the server, and a reference of "" clears it. The date is never sent empty, because
 *  an issued document must keep one. The server keeps the original issuer and time and writes the history.
 *  The date input's `max` is only a convenience. The form has `noValidate` so the browser never blocks a submit
 *  with its own message: the server is the authority and its message is what is shown. */
export default function CorrectIssuanceModal({ doc, onClose, onDone, onStale }) {
  const [form, setForm] = useState({ issued_on: doc.issued_on || "", reference: doc.reference || "", reason: "" });
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState(null);
  const set = (field) => (event) => setForm((f) => ({ ...f, [field]: event.target.value }));

  const reason = form.reason.trim();
  const reference = clean(form.reference);
  const currentReference = clean(doc.reference);
  const dateMissing = form.issued_on === "";
  const dateChanged = !dateMissing && form.issued_on !== doc.issued_on;
  const referenceChanged = reference !== currentReference;
  const removingReference = referenceChanged && reference === "" && currentReference !== "";
  const changed = dateChanged || referenceChanged;
  const valid = !dateMissing && changed && reason.length >= MIN_REASON;

  const payload = { reason };
  if (dateChanged) payload.issued_on = form.issued_on;
  if (referenceChanged) payload.reference = reference;

  const fieldErrors = error?.fieldErrors || {};
  const fieldError = (name) => (fieldErrors[name] ? <span className="field-error" role="alert">{fieldErrors[name][0]}</span> : null);
  const shownBesideField = FIELDS.some((name) => fieldErrors[name]);
  const originalIssuer = doc.issued_by_name
    ? `Originally issued by ${doc.issued_by_name}${doc.issued_at ? ` on ${formatDateTime(doc.issued_at)}` : ""}.`
    : "";

  async function submit(event) {
    event.preventDefault();
    if (!valid || busy) return;
    setBusy(true);
    setError(null);
    try {
      const result = await correctDocumentIssuance(doc.id, payload);
      await onDone(result);
    } catch (err) {
      setError(err);
      // 409 means the document or sale is no longer in a state that can be corrected, often because someone
      // else changed it first: let the list reload so it shows the truth when this dialog is closed.
      if (err.status === 409 && onStale) onStale();
    } finally {
      setBusy(false);
    }
  }

  return (
    <Modal title="Correct issuance" onClose={onClose}>
      <form className="edit-form" onSubmit={submit} noValidate>
        <p className="muted">
          {`Use this to correct the issue date or reference recorded for ${doc.name}. The original issuer and issue time are kept, and your reason will be recorded in the sale's history.`}
        </p>
        {originalIssuer ? <p className="muted small">{originalIssuer}</p> : null}
        {error && !shownBesideField ? <p className="form-error" role="alert">{error.message}</p> : null}
        <div className="form-grid">
          <label>
            Issue date
            <input type="date" value={form.issued_on} max={todayIso()} onChange={set("issued_on")} />
            {fieldError("issued_on")}
          </label>
          <label>
            Reference
            <input value={form.reference} maxLength={REFERENCE_LIMIT} onChange={set("reference")} />
            {fieldError("reference")}
          </label>
          <label className="wide">
            Reason for correction
            <textarea rows={3} value={form.reason} maxLength={REASON_LIMIT} onChange={set("reason")} />
            {fieldError("reason")}
          </label>
        </div>
        {!dateMissing && !changed ? (
          <p className="muted small">Change the issue date or the reference to save a correction.</p>
        ) : null}
        <ul className="hints">
          {dateMissing ? <li>An issue date is required.</li> : null}
          {removingReference ? <li>The reference will be removed.</li> : null}
        </ul>
        <div className="form-actions">
          <button type="submit" className="btn btn-primary" disabled={!valid || busy}>{busy ? "Saving…" : "Save correction"}</button>
          <button type="button" className="btn" onClick={onClose}>Cancel</button>
        </div>
      </form>
    </Modal>
  );
}
