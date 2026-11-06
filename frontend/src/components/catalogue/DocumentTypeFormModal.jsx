import { useState } from "react";
import { createDocumentType } from "../../api/documentTypes.js";
import Modal from "../common/Modal.jsx";

// The inputs this form shows a server message beside. Any other refusal goes in the form-level box.
const FIELDS = ["name", "category", "description", "sla_days", "default_department", "sort_order"];
const INTRO =
  "A new document type starts active and can then be added to sales. It does not change any existing sale or the automatic generation of documents.";
const SLA_HINT = "If left empty, the standard time is used.";
const SORT_HINT = "Entries are listed by sort order, then name. If left empty, 0 is used.";
const DEPARTMENT_HINTS = {
  pending: "Loading departments…",
  error: "Departments could not be loaded. You can add the document type without one.",
};

/** Add a document type (POST /api/document-types/). Only the name is required. Optional fields that are left
 *  empty are not sent, so the server's defaults apply; numbers are sent as numbers. The input limits are a
 *  convenience: the server checks everything and its message is shown beside the field it concerns. The form has
 *  `noValidate` so the browser never blocks a submit with its own message. */
export default function DocumentTypeFormModal({ departments, departmentsStatus, onClose, onDone }) {
  const [form, setForm] = useState({
    name: "", category: "", description: "", sla_days: "", department: "", sort_order: "",
  });
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState(null);
  const set = (field) => (event) => setForm((f) => ({ ...f, [field]: event.target.value }));

  const departmentsReady = departmentsStatus === "success";
  const departmentHint = DEPARTMENT_HINTS[departmentsStatus] || "";
  const valid = form.name.trim() !== "";

  const payload = { name: form.name.trim() };
  if (form.category.trim()) payload.category = form.category.trim();
  if (form.description.trim()) payload.description = form.description.trim();
  if (form.sla_days.trim() !== "") payload.sla_days = Number(form.sla_days);
  if (form.department !== "") payload.default_department = Number(form.department);
  if (form.sort_order.trim() !== "") payload.sort_order = Number(form.sort_order);

  const fieldErrors = error?.fieldErrors || {};
  const fieldError = (name) => (fieldErrors[name] ? <span className="field-error" role="alert">{fieldErrors[name][0]}</span> : null);
  const shownBesideField = FIELDS.some((name) => fieldErrors[name]);

  async function submit(event) {
    event.preventDefault();
    if (!valid || busy) return;
    setBusy(true);
    setError(null);
    try {
      const entry = await createDocumentType(payload);
      await onDone(entry);
    } catch (err) {
      setError(err);
    } finally {
      setBusy(false);
    }
  }

  return (
    <Modal title="Add a document type" onClose={onClose}>
      <form className="edit-form" onSubmit={submit} noValidate>
        <p className="muted">{INTRO}</p>
        {error && !shownBesideField ? <p className="form-error" role="alert">{error.message}</p> : null}
        <div className="form-grid">
          <label className="wide">
            Name
            <input value={form.name} maxLength={120} onChange={set("name")} />
            {fieldError("name")}
          </label>
          <label>
            Category (optional)
            <input value={form.category} maxLength={60} onChange={set("category")} />
            {fieldError("category")}
          </label>
          <div>
            <label>
              Default department (optional)
              <select
                value={form.department}
                onChange={set("department")}
                disabled={!departmentsReady}
                aria-describedby={departmentHint ? "department-hint" : undefined}
              >
                <option value="">No department</option>
                {departments.map((d) => <option key={d.id} value={d.id}>{d.name}</option>)}
              </select>
              {fieldError("default_department")}
            </label>
            {departmentHint ? <span className="muted small" id="department-hint">{departmentHint}</span> : null}
          </div>
          <div>
            <label>
              Preparation time in days (optional)
              <input type="number" inputMode="numeric" value={form.sla_days} onChange={set("sla_days")} aria-describedby="sla-hint" />
              {fieldError("sla_days")}
            </label>
            <span className="muted small" id="sla-hint">{SLA_HINT}</span>
          </div>
          <div>
            <label>
              Sort order (optional)
              <input type="number" inputMode="numeric" value={form.sort_order} onChange={set("sort_order")} aria-describedby="sort-hint" />
              {fieldError("sort_order")}
            </label>
            <span className="muted small" id="sort-hint">{SORT_HINT}</span>
          </div>
          <label className="wide">
            Description (optional)
            <textarea rows={3} value={form.description} maxLength={1000} onChange={set("description")} />
            {fieldError("description")}
          </label>
        </div>
        <div className="form-actions">
          <button type="submit" className="btn btn-primary" disabled={!valid || busy}>{busy ? "Adding…" : "Add document type"}</button>
          <button type="button" className="btn" onClick={onClose}>Cancel</button>
        </div>
      </form>
    </Modal>
  );
}
