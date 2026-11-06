import { useState } from "react";
import { renameDocumentType } from "../../api/documentTypes.js";
import Modal from "../common/Modal.jsx";

const INTRO =
  "The new name is used from now on. Obligations already created keep their old name, and documents already on sales keep the name they had when added.";

/** The server's own tidying of text (_clean_text in tracker/services.py). The server's "name is unchanged" check is exact,
 *  so a change of capital letters alone is a real change, and a difference in spacing alone is not. */
const clean = (text) => String(text ?? "").replace(/\s+/g, " ").trim();

/** Rename an entry (POST /api/document-types/{id}/rename/). Only the name is sent. The server never rewrites history:
 *  earlier obligations and the names recorded on sale documents stay as they were. */
export default function RenameDocumentTypeModal({ entry, onClose, onDone, onStale }) {
  const [name, setName] = useState(entry.name);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState(null);

  const cleaned = clean(name);
  const changed = cleaned !== "" && cleaned !== entry.name;

  const fieldErrors = error?.fieldErrors || {};
  const shownBesideField = Boolean(fieldErrors.name);

  async function submit(event) {
    event.preventDefault();
    if (!changed || busy) return;
    setBusy(true);
    setError(null);
    try {
      const result = await renameDocumentType(entry.id, { name: cleaned });
      await onDone(result);
    } catch (err) {
      setError(err);
      // 409 means the entry is no longer in a state that can be renamed: let the catalogue reload.
      if (err.status === 409 && onStale) onStale();
    } finally {
      setBusy(false);
    }
  }

  return (
    <Modal title={`Rename ${entry.name}`} onClose={onClose}>
      <form className="edit-form" onSubmit={submit} noValidate>
        <p className="muted">{INTRO}</p>
        {error && !shownBesideField ? <p className="form-error" role="alert">{error.message}</p> : null}
        <label>
          New name
          <input value={name} maxLength={120} onChange={(event) => setName(event.target.value)} />
          {fieldErrors.name ? <span className="field-error" role="alert">{fieldErrors.name[0]}</span> : null}
        </label>
        <div className="form-actions">
          <button type="submit" className="btn btn-primary" disabled={!changed || busy}>{busy ? "Renaming…" : "Rename document type"}</button>
          <button type="button" className="btn" onClick={onClose}>Cancel</button>
        </div>
      </form>
    </Modal>
  );
}
