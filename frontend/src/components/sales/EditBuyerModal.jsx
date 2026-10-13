import { useState } from "react";
import { editBuyer } from "../../api/sales.js";
import Modal from "../common/Modal.jsx";

/** Sends only the fields that changed. */
export default function EditBuyerModal({ sale, onClose, onDone }) {
  const buyer = sale.buyer_detail;
  const [form, setForm] = useState({ full_name: buyer.full_name, phone: buyer.phone || "", email: buyer.email || "" });
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState(null);
  const set = (field) => (event) => setForm((f) => ({ ...f, [field]: event.target.value }));

  const payload = {};
  for (const field of ["full_name", "phone", "email"]) {
    if (form[field].trim() !== (buyer[field] || "")) payload[field] = form[field].trim();
  }
  const dirty = Object.keys(payload).length > 0;
  const fieldErrors = error?.fieldErrors || {};
  const fieldError = (name) => (fieldErrors[name] ? <span className="field-error" role="alert">{fieldErrors[name][0]}</span> : null);

  async function submit(event) {
    event.preventDefault();
    if (!dirty) return;
    setBusy(true);
    setError(null);
    try {
      const result = await editBuyer(sale.id, payload);
      await onDone(result);
    } catch (err) {
      setError(err);
    } finally {
      setBusy(false);
    }
  }

  return (
    <Modal title="Edit buyer details" onClose={onClose}>
      <form className="edit-form" onSubmit={submit}>
        <p className="muted">These details belong to the buyer, so every sale of this buyer shows the change in its history.</p>
        {error && !Object.keys(fieldErrors).length ? <p className="form-error" role="alert">{error.message}</p> : null}
        <div className="form-grid">
          <label>
            Full name
            <input value={form.full_name} maxLength={200} onChange={set("full_name")} />
            {fieldError("full_name")}
          </label>
          <label>
            Phone
            <input value={form.phone} maxLength={40} onChange={set("phone")} />
            {fieldError("phone")}
          </label>
          <label>
            Email
            <input type="email" value={form.email} onChange={set("email")} />
            {fieldError("email")}
          </label>
        </div>
        <div className="form-actions">
          <button type="submit" className="btn btn-primary" disabled={!dirty || busy}>{busy ? "Saving…" : "Save changes"}</button>
          <button type="button" className="btn" onClick={onClose}>Cancel</button>
        </div>
      </form>
    </Modal>
  );
}
