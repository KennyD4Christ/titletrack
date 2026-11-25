import { useState } from "react";
import { registerPlot } from "../../api/inventory.js";
import Modal from "../common/Modal.jsx";

/** Register one plot in an estate, with no sale. The server says plainly if the number is already taken. */
export default function RegisterPlotModal({ estate, onClose, onDone }) {
  const [number, setNumber] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState(null);
  const fieldErrors = error?.fieldErrors || {};

  async function submit(event) {
    event.preventDefault();
    if (!number.trim()) return;
    setBusy(true);
    setError(null);
    try {
      await onDone(await registerPlot({ estate: estate.id, plot_number: number.trim() }));
    } catch (err) {
      setError(err);
    } finally {
      setBusy(false);
    }
  }

  return (
    <Modal title="Register a plot" onClose={onClose}>
      <form className="edit-form" onSubmit={submit}>
        <p className="muted">{`Adds a plot to ${estate.name} that has not been sold. To add many at once, use "Register many plots".`}</p>
        {error && !Object.keys(fieldErrors).length ? <p className="form-error" role="alert">{error.message}</p> : null}
        <label>
          Plot number
          <input value={number} maxLength={50} onChange={(e) => setNumber(e.target.value)} />
          {fieldErrors.plot_number ? <span className="field-error" role="alert">{fieldErrors.plot_number[0]}</span> : null}
        </label>
        <div className="form-actions">
          <button type="submit" className="btn btn-primary" disabled={!number.trim() || busy}>{busy ? "Registering…" : "Register plot"}</button>
          <button type="button" className="btn" onClick={onClose}>Cancel</button>
        </div>
      </form>
    </Modal>
  );
}
