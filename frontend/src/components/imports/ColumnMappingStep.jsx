import { useMemo, useState } from "react";
import { saveMapping } from "../../api/imports.js";

const NONE = "";

function initial(upload) {
  const source = upload.mapping && Object.keys(upload.mapping).length ? upload.mapping : upload.suggested_mapping || {};
  const fields = Object.fromEntries(upload.fields.map((f) => [f.key, source[f.key] || NONE]));
  const docs = Object.fromEntries(upload.obligation_types.map((t) => [t, source.documents?.[t] || NONE]));
  return { fields, docs };
}

/** Shows what is in the first rows of a column so people pick the right one. */
function examples(upload, header) {
  const i = upload.headers.indexOf(header);
  if (i < 0) return "";
  return upload.sample_rows.map((row) => row[i]).filter((v) => v !== null && v !== "").slice(0, 3).join(", ");
}

export default function ColumnMappingStep({ upload, onSaved, onChangeSheet, onBack }) {
  const [state, setState] = useState(() => initial(upload));
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState(null);

  const used = useMemo(() => {
    const counts = {};
    [...Object.values(state.fields), ...Object.values(state.docs)].filter(Boolean).forEach((h) => { counts[h] = (counts[h] || 0) + 1; });
    return counts;
  }, [state]);
  const missing = upload.fields.filter((f) => f.required && !state.fields[f.key]);
  const duplicates = Object.entries(used).filter(([, n]) => n > 1).map(([h]) => h);
  const ready = missing.length === 0 && duplicates.length === 0;

  const setField = (key) => (e) => setState((s) => ({ ...s, fields: { ...s.fields, [key]: e.target.value } }));
  const setDoc = (key) => (e) => setState((s) => ({ ...s, docs: { ...s.docs, [key]: e.target.value } }));

  async function submit(event) {
    event.preventDefault();
    if (!ready) return;
    const mapping = Object.fromEntries(Object.entries(state.fields).filter(([, v]) => v));
    const documents = Object.fromEntries(Object.entries(state.docs).filter(([, v]) => v));
    if (Object.keys(documents).length) mapping.documents = documents;
    setBusy(true);
    setError(null);
    try {
      onSaved(await saveMapping(upload.id, mapping));
    } catch (err) {
      setError(err);
    } finally {
      setBusy(false);
    }
  }

  const options = upload.headers.map((h) => <option key={h} value={h}>{h}</option>);
  const row = (key, label, value, onChange, requiredMark, emptyLabel) => (
    <label key={key} className="map-row">
      <span>{label}{requiredMark ? <span className="req" title="Required"> *</span> : null}</span>
      <select value={value} onChange={onChange} aria-label={label} aria-required={requiredMark || undefined}>
        <option value={NONE}>{emptyLabel}</option>
        {options}
      </select>
      <span className="muted small">{value ? `Examples: ${examples(upload, value) || "(empty)"}` : ""}{value && used[value] > 1 ? " · used twice" : ""}</span>
    </label>
  );

  return (
    <form className="card edit-form" onSubmit={submit}>
      <h2>Match your columns</h2>
      <p className="muted">
        We read {upload.row_count} rows from <strong>{upload.filename}</strong> (column titles on row {upload.header_row}). We guessed the matches; please check them.
      </p>
      {upload.sheets.length > 1 ? (
        <label>
          Sheet
          <select value={upload.sheet_name} onChange={(e) => onChangeSheet(e.target.value)}>
            {upload.sheets.map((s) => <option key={s} value={s}>{s}</option>)}
          </select>
        </label>
      ) : null}
      {error ? (
        <div className="form-error" role="alert">
          <p>{error.message}</p>
          {error.data?.mapping ? <ul>{error.data.mapping.map((m) => <li key={m}>{m}</li>)}</ul> : null}
        </div>
      ) : null}

      <fieldset className="map-group">
        <legend>About each sale</legend>
        {upload.fields.map((f) => row(f.key, f.label, state.fields[f.key], setField(f.key), f.required, f.required ? "Choose a column…" : "Not in my file"))}
      </fieldset>
      <fieldset className="map-group">
        <legend>Which columns say whether a document has been issued?</legend>
        {upload.obligation_types.map((t) => row(`doc-${t}`, t, state.docs[t], setDoc(t), false, "Not tracked in this file"))}
      </fieldset>

      {missing.length ? <p className="hints">Still needed: {missing.map((f) => f.label).join(", ")}.</p> : null}
      {duplicates.length ? <p className="hints">Each column can be used once. Used twice: {duplicates.join(", ")}.</p> : null}
      <div className="form-actions">
        <button type="submit" className="btn btn-primary" disabled={!ready || busy}>{busy ? "Saving…" : "Continue"}</button>
        <button type="button" className="btn" onClick={onBack}>Choose a different file</button>
      </div>
    </form>
  );
}
