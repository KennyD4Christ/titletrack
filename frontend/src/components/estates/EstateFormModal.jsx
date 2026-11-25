import { useState } from "react";
import { Link } from "react-router-dom";
import { createEstate, editEstate } from "../../api/inventory.js";
import Modal from "../common/Modal.jsx";

/** Create an estate, or (with `estate`) edit one. Editing sends only what changed. The server checks every rule. */
export default function EstateFormModal({ estate = null, onClose, onDone }) {
  const editing = Boolean(estate);
  const [form, setForm] = useState({ name: estate?.name ?? "", location: estate?.location ?? "", reason: "" });
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState(null);
  const set = (field) => (event) => setForm((f) => ({ ...f, [field]: event.target.value }));

  const payload = {};
  if (editing) {
    for (const field of ["name", "location"]) {
      if (form[field].trim() !== (estate[field] || "")) payload[field] = form[field].trim();
    }
    if (form.reason.trim()) payload.reason = form.reason.trim();
  } else {
    payload.name = form.name.trim();
    if (form.location.trim()) payload.location = form.location.trim();
  }
  const dirty = editing ? "name" in payload || "location" in payload : payload.name !== "";

  const fieldErrors = error?.fieldErrors || {};
  const fieldError = (name) => (fieldErrors[name] ? <span className="field-error" role="alert">{fieldErrors[name][0]}</span> : null);
  const existing = error?.data?.existing_estate;

  async function submit(event) {
    event.preventDefault();
    if (!dirty) return;
    setBusy(true);
    setError(null);
    try {
      const result = editing ? await editEstate(estate.id, payload) : await createEstate(payload);
      await onDone(result);
    } catch (err) {
      setError(err);
    } finally {
      setBusy(false);
    }
  }

  return (
    <Modal title={editing ? "Edit estate" : "Add an estate"} onClose={onClose}>
      <form className="edit-form" onSubmit={submit}>
        {editing ? (
          <p className="muted">Spreadsheet imports match estates by name, so renaming an estate changes how the next import is read.</p>
        ) : (
          <p className="muted">Register an estate before any plot is sold. Plots are added to it afterwards.</p>
        )}
        {error && !Object.keys(fieldErrors).length ? (
          <div className="form-error" role="alert">
            <p>{error.message}</p>
            {existing ? <p><Link to={`/estates/${existing.id}`}>Open {existing.name}</Link></p> : null}
          </div>
        ) : null}
        <div className="form-grid">
          <label>
            Estate name
            <input value={form.name} maxLength={200} onChange={set("name")} />
            {fieldError("name")}
          </label>
          <label>
            Location (optional)
            <input value={form.location} maxLength={200} onChange={set("location")} />
            {fieldError("location")}
          </label>
          {editing ? (
            <label className="wide">
              Reason (needed to rename an estate that has sales)
              <input value={form.reason} onChange={set("reason")} />
              {fieldError("reason")}
            </label>
          ) : null}
        </div>
        <div className="form-actions">
          <button type="submit" className="btn btn-primary" disabled={!dirty || busy}>
            {busy ? "Saving…" : editing ? "Save changes" : "Add estate"}
          </button>
          <button type="button" className="btn" onClick={onClose}>Cancel</button>
        </div>
      </form>
    </Modal>
  );
}
