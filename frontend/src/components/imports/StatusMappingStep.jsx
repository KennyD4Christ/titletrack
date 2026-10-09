import { useState } from "react";
import { saveStatusMapping } from "../../api/imports.js";
import { STATUS_LABELS } from "../../utils/labels.js";
import { plural } from "../../utils/format.js";

export default function StatusMappingStep({ batchId, values, onSaved, onBack }) {
  const [choices, setChoices] = useState(() => Object.fromEntries(values.map((v) => [v.key, v.chosen || v.suggested || ""])));
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState(null);
  const ready = values.every((v) => choices[v.key]);

  async function submit(event) {
    event.preventDefault();
    if (!ready) return;
    setBusy(true);
    setError(null);
    try {
      await saveStatusMapping(batchId, choices);
      onSaved();
    } catch (err) {
      setError(err);
    } finally {
      setBusy(false);
    }
  }

  return (
    <form className="card edit-form" onSubmit={submit}>
      <h2>What do your status words mean?</h2>
      <p className="muted">These are the different things your document columns say. Tell us what each one means. Nothing is guessed silently.</p>
      {error ? <p className="form-error" role="alert">{error.message}</p> : null}
      <div className="table-wrap">
        <table>
          <thead><tr><th>In your file</th><th className="num">Times</th><th>Means</th></tr></thead>
          <tbody>
            {values.map((v) => (
              <tr key={v.key}>
                <td>{v.key === "(blank)" ? <em>(left blank)</em> : <>“{v.example || v.key}”</>}</td>
                <td className="num">{v.count}</td>
                <td>
                  <select aria-label={`Meaning of ${v.key === "(blank)" ? "blank" : v.key}`} value={choices[v.key]} onChange={(e) => setChoices((c) => ({ ...c, [v.key]: e.target.value }))}>
                    <option value="">Choose…</option>
                    {Object.entries(STATUS_LABELS).map(([value, label]) => <option key={value} value={value}>{label}</option>)}
                  </select>
                  {!v.suggested && !choices[v.key] ? <span className="muted small"> We could not guess this one.</span> : null}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      {!ready ? <p className="hints">Choose a meaning for {plural(values.filter((v) => !choices[v.key]).length, "value")} to continue.</p> : null}
      <div className="form-actions">
        <button type="submit" className="btn btn-primary" disabled={!ready || busy}>{busy ? "Saving…" : "Continue"}</button>
        <button type="button" className="btn" onClick={onBack}>Back</button>
      </div>
    </form>
  );
}
