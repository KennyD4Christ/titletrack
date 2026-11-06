import { useState } from "react";
import { updateDocumentType } from "../../api/documentTypes.js";
import Modal from "../common/Modal.jsx";

// The inputs this form shows a server message beside. Any other refusal goes in the form-level box.
const FIELDS = ["category", "description", "sla_days", "default_department", "sort_order"];
const INTRO = "Use this to change the details of this document type. To change its name, use Rename.";
const UNCHANGED = "Change at least one detail to save.";
const WORK_NOTE =
  "Changing the preparation time or default department affects work created from now on. Existing obligations keep their due dates.";
const LEGACY_NOTE =
  "This document type also drives automatic generation on existing sales, so the change applies to those obligations too.";
const NO_WORK_TYPE =
  "This document type has no current work type, so its preparation time and default department cannot be changed.";
const DEPARTMENT_HINTS = {
  pending: "Loading departments…",
  error: "Departments could not be loaded, so the default department cannot be changed now.",
};

/** The server's own tidying of text (_clean_text in tracker/services.py): runs of whitespace become one space and the
 *  ends are trimmed. Comparing the same way means "no real change" is spotted here as well. */
const clean = (text) => String(text ?? "").replace(/\s+/g, " ").trim();
const textOf = (value) => (value === null || value === undefined ? "" : String(value));
const isWhole = (text) => text.trim() !== "" && Number.isInteger(Number(text));

/** Change an entry's details (PATCH /api/document-types/{id}/). The form starts with the current values and sends only
 *  what really changed: a field left out is kept by the server, "" clears a category or description, and null clears
 *  the default department. Preparation time and sort order cannot be emptied, and only whole numbers are sent: nothing
 *  is rounded. Preparation time and department live on the entry's current work type, so an entry without one cannot
 *  change them. The server checks everything else and its message is shown beside the field it concerns. */
export default function EditDocumentTypeModal({ entry, departments, departmentsStatus, onClose, onDone, onStale }) {
  const [form, setForm] = useState({
    category: textOf(entry.category),
    description: textOf(entry.description),
    sla_days: textOf(entry.sla_days),
    department: textOf(entry.default_department),
    sort_order: textOf(entry.sort_order),
  });
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState(null);
  const set = (field) => (event) => setForm((f) => ({ ...f, [field]: event.target.value }));

  const hasWorkType = entry.work_type !== null && entry.work_type !== undefined;
  const departmentsReady = departmentsStatus === "success";
  const departmentHint = hasWorkType ? DEPARTMENT_HINTS[departmentsStatus] || "" : "";
  const known = departments.some((d) => String(d.id) === form.department);

  const category = clean(form.category);
  const description = form.description.trim();
  const departmentValue = form.department === "" ? null : Number(form.department);

  const categoryChanged = category !== clean(entry.category);
  const descriptionChanged = description !== textOf(entry.description).trim();
  const slaChanged = hasWorkType && isWhole(form.sla_days) && Number(form.sla_days) !== entry.sla_days;
  const departmentChanged = hasWorkType && departmentValue !== (entry.default_department ?? null);
  const sortChanged = isWhole(form.sort_order) && Number(form.sort_order) !== entry.sort_order;
  const changed = categoryChanged || descriptionChanged || slaChanged || departmentChanged || sortChanged;

  let slaProblem = "";
  if (hasWorkType && form.sla_days.trim() === "") slaProblem = "Preparation time is required.";
  else if (hasWorkType && !isWhole(form.sla_days)) slaProblem = "Preparation time must be a whole number.";
  let sortProblem = "";
  if (form.sort_order.trim() === "") sortProblem = "Sort order is required.";
  else if (!isWhole(form.sort_order)) sortProblem = "Sort order must be a whole number.";
  const valid = changed && !slaProblem && !sortProblem;
  const workChanged = slaChanged || departmentChanged;

  const payload = {};
  if (categoryChanged) payload.category = category;
  if (descriptionChanged) payload.description = description;
  if (slaChanged) payload.sla_days = Number(form.sla_days);
  if (departmentChanged) payload.default_department = departmentValue;
  if (sortChanged) payload.sort_order = Number(form.sort_order);

  const fieldErrors = error?.fieldErrors || {};
  const fieldError = (name) => (fieldErrors[name] ? <span className="field-error" role="alert">{fieldErrors[name][0]}</span> : null);
  const shownBesideField = FIELDS.some((name) => fieldErrors[name]);

  async function submit(event) {
    event.preventDefault();
    if (!valid || busy) return;
    setBusy(true);
    setError(null);
    try {
      const result = await updateDocumentType(entry.id, payload);
      await onDone(result);
    } catch (err) {
      setError(err);
      // 409 means the entry is no longer in a state that allows this change: let the catalogue reload so the
      // page shows the truth when this dialog is closed.
      if (err.status === 409 && onStale) onStale();
    } finally {
      setBusy(false);
    }
  }

  return (
    <Modal title={`Edit ${entry.name}`} onClose={onClose}>
      <form className="edit-form" onSubmit={submit} noValidate>
        <p className="muted">{INTRO}</p>
        {error && !shownBesideField ? <p className="form-error" role="alert">{error.message}</p> : null}
        <div className="form-grid">
          <div className="wide">
            <span className="muted small">Name</span>
            <div>{entry.name}</div>
            <span className="muted small">The name has its own action, Rename.</span>
          </div>
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
                disabled={!hasWorkType || !departmentsReady}
                aria-describedby={departmentHint ? "department-hint" : undefined}
              >
                <option value="">No department</option>
                {form.department !== "" && !known ? <option value={form.department}>{`Department #${form.department}`}</option> : null}
                {departments.map((d) => <option key={d.id} value={d.id}>{d.name}</option>)}
              </select>
              {fieldError("default_department")}
            </label>
            {departmentHint ? <span className="muted small" id="department-hint">{departmentHint}</span> : null}
          </div>
          <label>
            Preparation time in days
            <input type="number" inputMode="numeric" value={form.sla_days} onChange={set("sla_days")} disabled={!hasWorkType} />
            {fieldError("sla_days")}
          </label>
          <label>
            Sort order
            <input type="number" inputMode="numeric" value={form.sort_order} onChange={set("sort_order")} />
            {fieldError("sort_order")}
          </label>
          <label className="wide">
            Description (optional)
            <textarea rows={3} value={form.description} maxLength={1000} onChange={set("description")} />
            {fieldError("description")}
          </label>
        </div>
        {!hasWorkType ? <p className="muted small">{NO_WORK_TYPE}</p> : null}
        {!changed && !slaProblem && !sortProblem ? <p className="muted small">{UNCHANGED}</p> : null}
        <ul className="hints">
          {slaProblem ? <li>{slaProblem}</li> : null}
          {sortProblem ? <li>{sortProblem}</li> : null}
          {workChanged ? <li>{WORK_NOTE}</li> : null}
          {workChanged && entry.drives_legacy_generation ? <li>{LEGACY_NOTE}</li> : null}
        </ul>
        <div className="form-actions">
          <button type="submit" className="btn btn-primary" disabled={!valid || busy}>{busy ? "Saving…" : "Save changes"}</button>
          <button type="button" className="btn" onClick={onClose}>Cancel</button>
        </div>
      </form>
    </Modal>
  );
}
