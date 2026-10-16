import { useState } from "react";
import { createStaff, issueSetupLink, updateStaff } from "../../api/staff.js";
import { ROLE_LABELS } from "../../utils/labels.js";
import Modal from "../common/Modal.jsx";

/** Create an account, or edit name, role and department. The email can be set once and is never editable. */
export default function StaffFormModal({ mode, account, roles, departments, onClose, onDone }) {
  const editing = mode === "edit";
  const isSelf = Boolean(account?.is_self);
  const [form, setForm] = useState({
    first_name: account?.first_name || "",
    last_name: account?.last_name || "",
    email: "",
    role: account?.role || "",
    department: account?.department ? String(account.department) : "",
  });
  const [sendLink, setSendLink] = useState(true);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState(null);
  const set = (field) => (event) => setForm((f) => ({ ...f, [field]: event.target.value }));

  const roleOptions = roles.some((r) => r.value === form.role) || !form.role
    ? roles
    : [...roles, { value: form.role, label: ROLE_LABELS[form.role] || form.role }];
  const department = form.department ? Number(form.department) : null;

  const payload = {};
  if (editing) {
    if (form.first_name.trim() !== account.first_name) payload.first_name = form.first_name.trim();
    if (form.last_name.trim() !== account.last_name) payload.last_name = form.last_name.trim();
    if (!isSelf && form.role !== account.role) payload.role = form.role;
    if (department !== (account.department ?? null)) payload.department = department;
  }
  const filled = form.first_name.trim() && form.last_name.trim() && form.role
    && (form.role !== "staff" || department !== null) && (editing || form.email.trim());
  const canSubmit = Boolean(filled) && (editing ? Object.keys(payload).length > 0 : true) && !busy;

  const fieldErrors = error?.fieldErrors || {};
  const fieldError = (name) => (fieldErrors[name] ? <span className="field-error" role="alert">{fieldErrors[name][0]}</span> : null);

  async function submit(event) {
    event.preventDefault();
    if (!canSubmit) return;
    setBusy(true);
    setError(null);
    try {
      if (editing) {
        await onDone({ account: await updateStaff(account.id, payload) });
        return;
      }
      const created = await createStaff({
        email: form.email.trim(), first_name: form.first_name.trim(), last_name: form.last_name.trim(),
        role: form.role, department,
      });
      let outcome = null;
      let linkError = null;
      if (sendLink) {
        try {
          outcome = await issueSetupLink(created.id, false);
        } catch (err) {
          linkError = err;
        }
      }
      await onDone({ account: created, outcome, linkError });
    } catch (err) {
      setError(err);
    } finally {
      setBusy(false);
    }
  }

  return (
    <Modal title={editing ? `Edit ${account.full_name}` : "Add an account"} onClose={onClose}>
      <form className="edit-form" onSubmit={submit}>
        {error && !Object.keys(fieldErrors).length ? <p className="form-error" role="alert">{error.message}</p> : null}
        <div className="form-grid">
          <label>
            First name
            <input value={form.first_name} maxLength={150} onChange={set("first_name")} />
            {fieldError("first_name")}
          </label>
          <label>
            Last name
            <input value={form.last_name} maxLength={150} onChange={set("last_name")} />
            {fieldError("last_name")}
          </label>
          {editing ? (
            <div className="wide">
              <span className="muted small">Email</span>
              <div>{account.email || "No email address"}</div>
              <span className="muted small">The email address is the sign-in name and cannot be changed here.</span>
            </div>
          ) : (
            <label className="wide">
              Email
              <input type="email" value={form.email} maxLength={150} autoComplete="off" onChange={set("email")} />
              {fieldError("email")}
            </label>
          )}
          <label>
            Role
            <select value={form.role} onChange={set("role")} disabled={isSelf}>
              {!form.role ? <option value="">Choose a role</option> : null}
              {roleOptions.map((r) => <option key={r.value} value={r.value}>{r.label}</option>)}
            </select>
            {fieldError("role")}
          </label>
          <label>
            Department
            <select value={form.department} onChange={set("department")}>
              <option value="">No department</option>
              {departments.map((d) => <option key={d.id} value={d.id}>{d.name}</option>)}
            </select>
            {fieldError("department")}
          </label>
        </div>
        <p className="muted small">
          {isSelf ? "You cannot change your own role. " : ""}A department is required for Staff accounts.
        </p>
        {!editing ? (
          <label className="check">
            <input type="checkbox" checked={sendLink} onChange={(e) => setSendLink(e.target.checked)} />
            Send the setup link now
          </label>
        ) : null}
        <div className="form-actions">
          <button type="submit" className="btn btn-primary" disabled={!canSubmit}>
            {busy ? "Saving…" : editing ? "Save changes" : "Add account"}
          </button>
          <button type="button" className="btn" onClick={onClose}>Cancel</button>
        </div>
      </form>
    </Modal>
  );
}
