import { useState } from "react";
import Modal from "../common/Modal.jsx";

/** A plain yes/no dialog for actions that need no extra input. */
export default function ConfirmModal({ title, message, confirmLabel, onConfirm, onClose }) {
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState(null);

  async function submit(event) {
    event.preventDefault();
    setBusy(true);
    setError(null);
    try {
      await onConfirm();
    } catch (err) {
      setError(err);
    } finally {
      setBusy(false);
    }
  }

  return (
    <Modal title={title} onClose={onClose}>
      <form className="edit-form" onSubmit={submit}>
        <p>{message}</p>
        {error ? <p className="form-error" role="alert">{error.message}</p> : null}
        <div className="form-actions">
          <button type="submit" className="btn btn-primary" disabled={busy}>{busy ? "Working…" : confirmLabel}</button>
          <button type="button" className="btn" onClick={onClose}>Not now</button>
        </div>
      </form>
    </Modal>
  );
}
