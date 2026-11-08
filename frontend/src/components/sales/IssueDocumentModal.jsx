import { useState } from "react";
import { issueDocument } from "../../api/saleDocuments.js";
import Modal from "../common/Modal.jsx";
import { todayIso } from "../../utils/sales.js";

// The inputs this form shows a server message beside. Any other refusal goes in the form-level box.
const FIELDS = ["issued_on", "reference"];
const COMPLETES_NOTE = "If you issue this document, its open preparation work will also be marked as completed.";
const CORRECTION_NOTE = "If the issuance details need correction later, an authorised user can correct them with a reason.";

/** Record that an owed document has been issued to the buyer (POST /api/sale-documents/{id}/issue/).
 *  The date is required and starts empty: the server never defaults it, and an issue date is an audit fact.
 *  The date input's `max` is only a convenience for the picker. The form has `noValidate` so the browser never
 *  blocks a submit with its own message: the server is the authority and its message is what is shown. */
export default function IssueDocumentModal({ doc, completesWork = false, onClose, onDone, onStale }) {
  const [form, setForm] = useState({ issued_on: "", reference: "" });
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState(null);
  const set = (field) => (event) => setForm((f) => ({ ...f, [field]: event.target.value }));

  const valid = form.issued_on !== "";
  const payload = { issued_on: form.issued_on };
  if (form.reference.trim()) payload.reference = form.reference.trim();

  const fieldErrors = error?.fieldErrors || {};
  const fieldError = (name) => (fieldErrors[name] ? <span className="field-error" role="alert">{fieldErrors[name][0]}</span> : null);
  const shownBesideField = FIELDS.some((name) => fieldErrors[name]);
  const hint = completesWork ? `${COMPLETES_NOTE} ${CORRECTION_NOTE}` : CORRECTION_NOTE;

  async function submit(event) {
    event.preventDefault();
    if (!valid || busy) return;
    setBusy(true);
    setError(null);
    try {
      const result = await issueDocument(doc.id, payload);
      await onDone(result);
    } catch (err) {
      setError(err);
      // 409 means the document or sale is no longer in a state that can be issued, often because someone
      // else changed it first: let the list reload so it shows the truth when this dialog is closed.
      if (err.status === 409 && onStale) onStale();
    } finally {
      setBusy(false);
    }
  }

  return (
    <Modal title="Issue document" onClose={onClose}>
      <form className="edit-form" onSubmit={submit} noValidate>
        <p className="muted">{doc.name}</p>
        {error && !shownBesideField ? <p className="form-error" role="alert">{error.message}</p> : null}
        <div className="form-grid">
          <label>
            Issue date
            <input type="date" value={form.issued_on} max={todayIso()} onChange={set("issued_on")} />
            {fieldError("issued_on")}
          </label>
          <label>
            Reference (optional)
            <input value={form.reference} maxLength={120} onChange={set("reference")} placeholder="e.g. document or certificate number" />
            {fieldError("reference")}
          </label>
        </div>
        <p>
          <button type="button" className="btn btn-quiet" onClick={() => setForm((f) => ({ ...f, issued_on: todayIso() }))}>
            Use today's date
          </button>
        </p>
        <p className="muted">{hint}</p>
        <div className="form-actions">
          <button type="submit" className="btn btn-primary" disabled={!valid || busy}>{busy ? "Recording…" : "Confirm issuance"}</button>
          <button type="button" className="btn" onClick={onClose}>Cancel</button>
        </div>
      </form>
    </Modal>
  );
}
