import { useState } from "react";
import { uploadImport } from "../../api/imports.js";
import { SAMPLE_CSV, downloadText } from "../../utils/download.js";

export default function UploadStep({ onUploaded }) {
  const [file, setFile] = useState(null);
  const [headerRow, setHeaderRow] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");

  async function submit(event) {
    event.preventDefault();
    if (!file) return;
    setBusy(true);
    setError("");
    try {
      const upload = await uploadImport(file, { headerRow: headerRow.trim() });
      onUploaded(upload, file);
    } catch (err) {
      setError(err.message);
    } finally {
      setBusy(false);
    }
  }

  return (
    <form className="card edit-form" onSubmit={submit}>
      <h2>Upload your spreadsheet</h2>
      <p className="muted">
        An Excel (.xlsx) or CSV file with one row per sale: estate, plot, buyer, price, amount paid, and (if you have them) dates and
        the status of each document. Up to 2,000 rows and 5 MB. Nothing is saved until you have checked it.
      </p>
      {error ? <p className="form-error" role="alert">{error}</p> : null}
      <label>
        Spreadsheet (.xlsx or .csv)
        <input type="file" accept=".xlsx,.csv" onChange={(e) => setFile(e.target.files?.[0] ?? null)} />
      </label>
      <details>
        <summary>Advanced</summary>
        <label>
          Header row number (only if we cannot find your column titles)
          <input type="number" min="1" max="1000" value={headerRow} onChange={(e) => setHeaderRow(e.target.value)} />
        </label>
      </details>
      <div className="form-actions">
        <button type="submit" className="btn btn-primary" disabled={!file || busy}>{busy ? "Reading your file…" : "Upload and continue"}</button>
        <button type="button" className="btn btn-quiet" onClick={() => downloadText("titletrack-sample.csv", SAMPLE_CSV)}>Download a sample file</button>
      </div>
    </form>
  );
}
