import { useState } from "react";
import Modal from "../common/Modal.jsx";

/** One small dialog for the three actions that need a reason or note: reverse a payment, cancel, reinstate. */
export default function ReasonModal({
  title, intro, label, placeholder = "", required = true, submitLabel, danger = false, onSubmit, onClose,
}) {
  const [text, setText] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState(null);
  const ok = !required || text.trim().length >= 3;

  async function submit(event) {
    event.preventDefault();
    if (!ok) return;
    setBusy(true);
    setError(null);
    try {
      await onSubmit(text.trim());
    } catch (err) {
      setError(err);
    } finally {
      setBusy(false);
    }
  }

  return (
    <Modal title={title} onClose={onClose}>
      <form className="edit-form" onSubmit={submit}>
        {intro ? <p className="muted">{intro}</p> : null}
        {error ? <p className="form-error" role="alert">{error.message}</p> : null}
        <label>
          {label}
          <textarea rows={3} value={text} placeholder={placeholder} onChange={(e) => setText(e.target.value)} />
        </label>
        <div className="form-actions">
          <button type="submit" className={`btn ${danger ? "btn-danger" : "btn-primary"}`} disabled={!ok || busy}>
            {busy ? "Working…" : submitLabel}
          </button>
          <button type="button" className="btn" onClick={onClose}>Not now</button>
        </div>
      </form>
    </Modal>
  );
}
