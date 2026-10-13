import { useState } from "react";
import { recordPayment } from "../../api/sales.js";
import Modal from "../common/Modal.jsx";
import { formatNaira } from "../../utils/format.js";
import { newRequestKey, toAmount, todayIso } from "../../utils/sales.js";

/** The key is made once when the form opens and reused on every retry, so a double click or a retry after a
 *  dropped connection can never record the same payment twice. */
export default function RecordPaymentModal({ sale, onClose, onDone }) {
  const [key] = useState(newRequestKey);
  const [form, setForm] = useState({ amount: "", paid_on: todayIso(), reference: "", note: "" });
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState(null);
  const set = (field) => (event) => setForm((f) => ({ ...f, [field]: event.target.value }));

  const amount = toAmount(form.amount);
  const balance = Number(sale.balance_due);
  const valid = amount > 0;
  const completes = valid && Math.abs(amount - balance) < 0.005;
  const tooMuch = valid && amount > balance + 0.005;

  const payload = { amount: form.amount.trim(), idempotency_key: key };
  if (form.paid_on) payload.paid_on = form.paid_on;
  if (form.reference.trim()) payload.reference = form.reference.trim();
  if (form.note.trim()) payload.note = form.note.trim();

  const fieldErrors = error?.fieldErrors || {};
  const fieldError = (name) => (fieldErrors[name] ? <span className="field-error" role="alert">{fieldErrors[name][0]}</span> : null);

  async function submit(event) {
    event.preventDefault();
    if (!valid) return;
    setBusy(true);
    setError(null);
    try {
      const result = await recordPayment(sale.id, payload);
      await onDone(result);
    } catch (err) {
      setError(err);
    } finally {
      setBusy(false);
    }
  }

  return (
    <Modal title="Record a payment" onClose={onClose}>
      <form className="edit-form" onSubmit={submit}>
        <p className="muted">{`Balance due: ${formatNaira(sale.balance_due)}`}</p>
        {error && !Object.keys(fieldErrors).length ? <p className="form-error" role="alert">{error.message}</p> : null}
        <div className="form-grid">
          <label>
            Amount
            <input inputMode="decimal" value={form.amount} onChange={set("amount")} placeholder="e.g. 500000" />
            {fieldError("amount")}
          </label>
          <label>
            Payment date
            <input type="date" value={form.paid_on} onChange={set("paid_on")} />
            {fieldError("paid_on")}
          </label>
          <label>
            Reference (optional)
            <input value={form.reference} maxLength={120} onChange={set("reference")} placeholder="e.g. bank transfer reference" />
            {fieldError("reference")}
          </label>
          <label className="wide">
            Note (optional)
            <textarea rows={2} value={form.note} onChange={set("note")} />
          </label>
        </div>
        <p>
          <button type="button" className="btn btn-quiet" onClick={() => setForm((f) => ({ ...f, amount: sale.balance_due }))}>
            Pay the full balance
          </button>
        </p>
        <ul className="hints">
          {completes ? <li>This completes the price: the documents due on full payment are created straight away.</li> : null}
          {tooMuch ? <li>This is more than the balance due, so it will be refused.</li> : null}
        </ul>
        <div className="form-actions">
          <button type="submit" className="btn btn-primary" disabled={!valid || busy}>{busy ? "Recording…" : "Record payment"}</button>
          <button type="button" className="btn" onClick={onClose}>Cancel</button>
        </div>
      </form>
    </Modal>
  );
}
