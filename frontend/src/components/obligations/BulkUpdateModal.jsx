import { useState } from "react";
import { bulkUpdateObligations } from "../../api/obligations.js";
import Modal from "../common/Modal.jsx";
import { STATUS_LABELS } from "../../utils/labels.js";
import { plural } from "../../utils/format.js";

const KEEP = "";
const CLEAR = "__clear__";

/** Change the same thing on many obligations at once. Nothing is saved until Apply; all or nothing. */
export default function BulkUpdateModal({ ids, users = [], labelFor, onClose, onDone }) {
  const [form, setForm] = useState({ owner: KEEP, status: KEEP, next_action: "", next_review_date: "", note: "" });
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState(null);
  const set = (key) => (event) => setForm((f) => ({ ...f, [key]: event.target.value }));

  const payload = { ids };
  if (form.owner === CLEAR) payload.owner = null;
  else if (form.owner !== KEEP) payload.owner = Number(form.owner);
  if (form.status !== KEEP) payload.status = form.status;
  if (form.next_action.trim()) payload.next_action = form.next_action.trim();
  if (form.next_review_date) payload.next_review_date = form.next_review_date;
  if (form.note.trim()) payload.note = form.note.trim();
  const dirty = Object.keys(payload).length > 1;

  async function submit(event) {
    event.preventDefault();
    if (!dirty) return;
    setBusy(true);
    setError(null);
    try {
      const result = await bulkUpdateObligations(payload);
      await onDone(result);
    } catch (err) {
      setError(err);
    } finally {
      setBusy(false);
    }
  }

  const problems = error?.data?.problems;
  return (
    <Modal title={`Update ${plural(ids.length, "obligation")}`} onClose={onClose}>
      <form className="edit-form" onSubmit={submit}>
        <p className="muted">Only the boxes you fill in change. Everything else stays as it is. Each item gets its own history entry.</p>
        {error ? (
          <div className="form-error" role="alert">
            <p>{error.message}</p>
            {problems ? (
              <ul>{problems.map((p) => <li key={p.id}>{labelFor ? labelFor(p.id) : `#${p.id}`}: {p.error}</li>)}</ul>
            ) : null}
          </div>
        ) : null}
        <div className="form-grid">
          <label>
            Owner
            <select value={form.owner} onChange={set("owner")}>
              <option value={KEEP}>Leave unchanged</option>
              <option value={CLEAR}>Unassigned</option>
              {users.map((u) => <option key={u.id} value={u.id}>{u.full_name}</option>)}
            </select>
          </label>
          <label>
            Status
            <select value={form.status} onChange={set("status")}>
              <option value={KEEP}>Leave unchanged</option>
              {Object.entries(STATUS_LABELS).map(([value, label]) => <option key={value} value={value}>{label}</option>)}
            </select>
          </label>
          <label className="wide">
            Next action
            <input value={form.next_action} onChange={set("next_action")} placeholder="Leave blank to keep each item's own" />
          </label>
          <label>
            Next review date
            <input type="date" value={form.next_review_date} onChange={set("next_review_date")} />
          </label>
          <label className="wide">
            Note for the history (optional)
            <textarea rows={2} value={form.note} onChange={set("note")} placeholder="e.g. Reallocated after Monday review" />
          </label>
        </div>
        <div className="form-actions">
          <button type="submit" className="btn btn-primary" disabled={!dirty || busy}>
            {busy ? "Applying…" : `Apply to ${plural(ids.length, "obligation")}`}
          </button>
          <button type="button" className="btn" onClick={onClose}>Cancel</button>
        </div>
      </form>
    </Modal>
  );
}
