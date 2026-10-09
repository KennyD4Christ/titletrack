import { useState } from "react";
import { undoImport } from "../../api/imports.js";
import Modal from "../common/Modal.jsx";

/** Undo is only possible while nobody has edited what the import created; otherwise the server says why. */
export default function UndoButton({ importId, filename, onUndone }) {
  const [open, setOpen] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState(null);

  async function confirm() {
    setBusy(true);
    setError(null);
    try {
      await undoImport(importId);
      setOpen(false);
      await onUndone?.();
    } catch (err) {
      setError(err);
    } finally {
      setBusy(false);
    }
  }

  return (
    <>
      <button type="button" className="btn" onClick={() => setOpen(true)}>Undo this import</button>
      {open ? (
        <Modal title="Undo this import?" onClose={() => setOpen(false)}>
          <p>This removes everything <strong>{filename}</strong> created: its sales, buyers, plots, obligations and their history. It only works if nobody has edited those obligations since.</p>
          {error ? (
            <div className="form-error" role="alert">
              <p>{error.message}</p>
              {error.data?.reasons ? <ul>{error.data.reasons.map((r) => <li key={r}>{r}</li>)}</ul> : null}
            </div>
          ) : null}
          <div className="form-actions">
            <button type="button" className="btn btn-danger" onClick={confirm} disabled={busy}>{busy ? "Undoing…" : "Yes, undo it"}</button>
            <button type="button" className="btn" onClick={() => setOpen(false)}>Keep it</button>
          </div>
        </Modal>
      ) : null}
    </>
  );
}
