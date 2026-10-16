import { useState } from "react";
import { changePassword } from "../api/passwords.js";
import { errorMessage } from "../utils/staff.js";
import PageHeader from "../components/layout/PageHeader.jsx";

export default function ChangePasswordPage() {
  const [form, setForm] = useState({ current: "", next: "", confirm: "" });
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState(null);
  const [done, setDone] = useState(false);
  const set = (field) => (event) => { setDone(false); setForm((f) => ({ ...f, [field]: event.target.value })); };
  const mismatch = form.confirm !== "" && form.next !== form.confirm;
  const fieldErrors = error?.fieldErrors || {};
  const fieldError = (name) => (fieldErrors[name] ? <span className="field-error" role="alert">{fieldErrors[name][0]}</span> : null);

  async function submit(event) {
    event.preventDefault();
    if (mismatch) return;
    setBusy(true);
    setError(null);
    setDone(false);
    try {
      await changePassword(form.current, form.next);
      setForm({ current: "", next: "", confirm: "" });
      setDone(true);
    } catch (err) {
      setError(err);
    } finally {
      setBusy(false);
    }
  }

  return (
    <>
      <PageHeader title="Change password" subtitle="Other devices where you are signed in will be signed out." />
      <section className="card">
        <form className="edit-form" onSubmit={submit}>
          {done ? <p className="banner banner-ok" role="status">Your password has been changed.</p> : null}
          {error && !Object.keys(fieldErrors).length ? <p className="form-error" role="alert">{errorMessage(error)}</p> : null}
          <div className="form-grid">
            <label className="wide">
              Current password
              <input type="password" value={form.current} onChange={set("current")} autoComplete="current-password" required />
              {fieldError("current_password")}
            </label>
            <label>
              New password
              <input type="password" value={form.next} onChange={set("next")} autoComplete="new-password" required />
              {fieldErrors.new_password ? fieldErrors.new_password.map((m) => <span key={m} className="field-error" role="alert">{m}</span>) : null}
            </label>
            <label>
              Confirm new password
              <input type="password" value={form.confirm} onChange={set("confirm")} autoComplete="new-password" required />
            </label>
          </div>
          {mismatch ? <span className="field-error" role="alert">The two passwords do not match.</span> : null}
          <div className="form-actions">
            <button type="submit" className="btn btn-primary" disabled={busy || mismatch || !form.current || !form.next}>
              {busy ? "Saving…" : "Change password"}
            </button>
          </div>
        </form>
      </section>
    </>
  );
}
