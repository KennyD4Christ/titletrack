import { useState } from "react";
import { updateObligation } from "../../api/obligations.js";
import { STATUS_LABELS } from "../../utils/labels.js";

const OPEN = ["not_started", "in_progress", "awaiting_external"];
const text = (v) => (v ?? "").toString();
const same = (a, b) => text(a).trim() === text(b).trim();

/** Edit one obligation. Sends ONLY what changed, plus an optional note, to PATCH /api/obligations/:id/. */
export default function ObligationForm({ obligation, users = [], compact = false, onSaved, onCancel }) {
  const [form, setForm] = useState({
    status: obligation.status,
    owner: text(obligation.owner),
    stage: text(obligation.stage),
    external_party: text(obligation.external_party),
    reason: text(obligation.reason),
    next_action: text(obligation.next_action),
    next_review_date: text(obligation.next_review_date),
    note: "",
  });
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState(null);

  const set = (key) => (event) => setForm((f) => ({ ...f, [key]: event.target.value }));

  function changes() {
    const out = {};
    if (form.status !== obligation.status) out.status = form.status;
    if (form.owner !== text(obligation.owner)) out.owner = form.owner === "" ? null : Number(form.owner);
    for (const key of ["stage", "external_party", "reason", "next_action"]) {
      if (!compact || key === "next_action") if (!same(form[key], obligation[key])) out[key] = form[key].trim();
    }
    if (form.next_review_date !== text(obligation.next_review_date)) out.next_review_date = form.next_review_date || null;
    if (form.note.trim()) out.note = form.note.trim();
    return out;
  }
  const payload = changes();
  const dirty = Object.keys(payload).length > 0;
  const willBeIncomplete = OPEN.includes(form.status) && (form.owner === "" || form.next_action.trim() === "");
  const fieldErrors = error?.fieldErrors || {};

  async function submit(event) {
    event.preventDefault();
    if (!dirty) return;
    setBusy(true);
    setError(null);
    try {
      const saved = await updateObligation(obligation.id, payload);
      await onSaved?.(saved);
    } catch (err) {
      setError(err);
    } finally {
      setBusy(false);
    }
  }

  const fieldError = (key) => (fieldErrors[key] ? <span className="field-error" role="alert">{fieldErrors[key][0]}</span> : null);

  return (
    <form className="edit-form" onSubmit={submit}>
      {error && !Object.keys(fieldErrors).length ? <p className="form-error" role="alert">{error.message}</p> : null}
      <div className="form-grid">
        <label>
          Status
          <select value={form.status} onChange={set("status")}>
            {Object.entries(STATUS_LABELS).map(([value, label]) => <option key={value} value={value}>{label}</option>)}
          </select>
          {fieldError("status")}
        </label>
        <label>
          Owner
          <select value={form.owner} onChange={set("owner")}>
            <option value="">Unassigned</option>
            {users.map((u) => <option key={u.id} value={u.id}>{u.full_name}</option>)}
          </select>
          {fieldError("owner")}
        </label>
        {!compact ? (
          <label>
            Stage
            <input value={form.stage} onChange={set("stage")} placeholder="e.g. With the registry" maxLength={120} />
            {fieldError("stage")}
          </label>
        ) : null}
        {!compact ? (
          <label>
            Waiting on (external party)
            <input value={form.external_party} onChange={set("external_party")} placeholder="e.g. Adeyemi & Co Solicitors" maxLength={200} />
            {fieldError("external_party")}
          </label>
        ) : null}
        <label className="wide">
          Next action
          <input value={form.next_action} onChange={set("next_action")} placeholder="What happens next, and who does it" />
          {fieldError("next_action")}
        </label>
        <label>
          Next review date
          <input type="date" value={form.next_review_date} onChange={set("next_review_date")} />
          {fieldError("next_review_date")}
        </label>
        {!compact ? (
          <label className="wide">
            Reason for any delay
            <textarea rows={2} value={form.reason} onChange={set("reason")} placeholder="Why is this not done yet?" />
            {fieldError("reason")}
          </label>
        ) : null}
        <label className="wide">
          Note for the history (optional)
          <textarea rows={2} value={form.note} onChange={set("note")} placeholder="e.g. Called the solicitor; documents resubmitted" />
          {fieldError("note")}
        </label>
      </div>

      <ul className="hints">
        {form.status === "awaiting_external" && form.external_party.trim() === "" ? <li>Say who it is waiting on, so the follow-up is clear.</li> : null}
        {willBeIncomplete ? <li>Without an owner and a next action, this stays on the "needs attention" list.</li> : null}
        {form.status === "completed" ? <li>Completing this closes it: it leaves every queue and the exposure figures.</li> : null}
      </ul>

      <div className="form-actions">
        <button type="submit" className="btn btn-primary" disabled={!dirty || busy}>{busy ? "Saving…" : "Save changes"}</button>
        {onCancel ? <button type="button" className="btn" onClick={onCancel}>Cancel</button> : null}
      </div>
    </form>
  );
}
