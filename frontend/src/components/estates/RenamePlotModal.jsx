import { useState } from "react";
import { renamePlot } from "../../api/inventory.js";
import Modal from "../common/Modal.jsx";

/** Change a plot's number. Sales stay attached to the plot. A plot that has ever had a sale needs a reason. */
export default function RenamePlotModal({ plot, onClose, onDone }) {
  const [number, setNumber] = useState(plot.plot_number);
  const [reason, setReason] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState(null);
  const changed = number.trim() !== "" && number.trim() !== plot.plot_number;
  const fieldErrors = error?.fieldErrors || {};

  async function submit(event) {
    event.preventDefault();
    if (!changed) return;
    setBusy(true);
    setError(null);
    try {
      const payload = { plot_number: number.trim() };
      if (reason.trim()) payload.reason = reason.trim();
      await onDone(await renamePlot(plot.id, payload));
    } catch (err) {
      setError(err);
    } finally {
      setBusy(false);
    }
  }

  return (
    <Modal title={`Rename plot ${plot.plot_number}`} onClose={onClose}>
      <form className="edit-form" onSubmit={submit}>
        <p className="muted">Any sale of this plot stays attached to it under the new number. Documents already printed keep the old number.</p>
        {error && !Object.keys(fieldErrors).length ? <p className="form-error" role="alert">{error.message}</p> : null}
        <div className="form-grid">
          <label>
            New plot number
            <input value={number} maxLength={50} onChange={(e) => setNumber(e.target.value)} />
            {fieldErrors.plot_number ? <span className="field-error" role="alert">{fieldErrors.plot_number[0]}</span> : null}
          </label>
          <label>
            Reason (needed once the plot has had a sale)
            <input value={reason} onChange={(e) => setReason(e.target.value)} />
          </label>
        </div>
        <div className="form-actions">
          <button type="submit" className="btn btn-primary" disabled={!changed || busy}>{busy ? "Saving…" : "Rename plot"}</button>
          <button type="button" className="btn" onClick={onClose}>Cancel</button>
        </div>
      </form>
    </Modal>
  );
}
