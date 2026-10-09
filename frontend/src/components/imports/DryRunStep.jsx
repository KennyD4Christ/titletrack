import { useEffect, useState } from "react";
import { commitImport, getIssuesCsv, runDryRun } from "../../api/imports.js";
import Modal from "../common/Modal.jsx";
import LoadingState from "../common/LoadingState.jsx";
import ErrorState from "../common/ErrorState.jsx";
import { downloadText } from "../../utils/download.js";
import { plural } from "../../utils/format.js";

const LEVEL_LABELS = { error: "Error", conflict: "Conflict", warning: "Warning" };

function Tile({ label, count, tone }) {
  return (
    <div className={`tile static ${count > 0 ? tone : ""}`}>
      <span className="tile-count">{count}</span>
      <span className="tile-label">{label}</span>
    </div>
  );
}

export default function DryRunStep({ batchId, filename, onBack, onCommitted }) {
  const [report, setReport] = useState(null);
  const [error, setError] = useState(null);
  const [level, setLevel] = useState("all");
  const [confirming, setConfirming] = useState(false);
  const [busy, setBusy] = useState(false);
  const [commitError, setCommitError] = useState(null);

  const check = async () => {
    setReport(null);
    setError(null);
    try {
      setReport(await runDryRun(batchId));
    } catch (err) {
      setError(err);
    }
  };
  useEffect(() => { check(); }, [batchId]);   // eslint-disable-line react-hooks/exhaustive-deps

  async function download() {
    downloadText(`import-${batchId}-issues.csv`, await getIssuesCsv(batchId));
  }

  async function commit() {
    setBusy(true);
    setCommitError(null);
    try {
      const result = await commitImport(batchId);
      setConfirming(false);
      await onCommitted(result);
    } catch (err) {
      setCommitError(err);
    } finally {
      setBusy(false);
    }
  }

  if (error) return <ErrorState error={error} onRetry={check} />;
  if (!report) return <LoadingState label="Checking every row…" />;

  const s = report.summary;
  const shown = report.issues.filter((i) => level === "all" || i.level === level);
  const blocked = report.mapping_problems.length > 0 || report.unmapped_statuses.length > 0;

  return (
    <div className="stack">
      <section className="card">
        <h2>Here is what will happen</h2>
        <p className="muted">Nothing has been saved yet. We checked {plural(s.total_rows, "row")} in <strong>{filename}</strong>.</p>
        <div className="tiles">
          <Tile label="Will be imported" count={s.to_import} tone="ok" />
          <Tile label="Already there (skipped)" count={s.unchanged} tone="" />
          <Tile label="Conflicts (skipped)" count={s.conflicts} tone="warn" />
          <Tile label="Errors (skipped)" count={s.errors} tone="danger" />
          <Tile label="Warnings" count={s.warnings} tone="warn" />
        </div>
        {s.new_estates.length ? (
          <p className="banner banner-warn">
            These estates do not exist yet and will be <strong>created</strong>: {s.new_estates.join(", ")}. If one of them is a spelling
            of an estate you already have, go back and fix the spreadsheet first.
          </p>
        ) : null}
        {report.mapping_problems.length ? <ul className="form-error">{report.mapping_problems.map((m) => <li key={m}>{m}</li>)}</ul> : null}
        {report.unmapped_statuses.length ? (
          <p className="form-error" role="alert">Some status words are not matched yet: {report.unmapped_statuses.map((u) => `“${u.example || u.key}”`).join(", ")}. Go back and match them.</p>
        ) : null}
      </section>

      {report.issues.length ? (
        <section className="card" aria-labelledby="issues-title">
          <div className="issues-head">
            <h2 id="issues-title">What we found ({report.issues_total})</h2>
            <label className="inline">
              Show
              <select value={level} onChange={(e) => setLevel(e.target.value)}>
                <option value="all">Everything</option>
                {Object.entries(LEVEL_LABELS).map(([k, label]) => <option key={k} value={k}>{label}s</option>)}
              </select>
            </label>
            <button type="button" className="btn" onClick={download}>Download all issues (CSV)</button>
          </div>
          <div className="table-wrap">
            <table>
              <thead><tr><th>Row</th><th>Type</th><th>Column</th><th>What is wrong</th><th>Value</th></tr></thead>
              <tbody>
                {shown.map((i, n) => (
                  <tr key={`${i.row}-${n}`}>
                    <td>{i.row}</td>
                    <td><span className={`badge badge-issue-${i.level}`}>{LEVEL_LABELS[i.level]}</span></td>
                    <td>{i.column}</td>
                    <td>{i.message}</td>
                    <td className="muted">{i.value}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
          {report.issues_total > report.issues.length ? <p className="muted small">Showing the first {report.issues.length}. Download the file for the rest.</p> : null}
          <p className="muted small">Row numbers are the row numbers in your spreadsheet. Skipped rows are simply left out; fix them in the file and import again.</p>
        </section>
      ) : null}

      <div className="form-actions">
        <button type="button" className="btn btn-primary" disabled={!s.can_commit} onClick={() => setConfirming(true)}>
          {s.to_import > 0 ? `Import ${plural(s.to_import, "row")}` : "Nothing to import"}
        </button>
        <button type="button" className="btn" onClick={onBack}>Back</button>
        <button type="button" className="btn btn-quiet" onClick={check}>Check again</button>
      </div>
      {!s.can_commit && !blocked ? <p className="muted">There are no rows that can be imported. Fix the errors in your spreadsheet and upload it again.</p> : null}

      {confirming ? (
        <Modal title={`Import ${plural(s.to_import, "row")}?`} onClose={() => setConfirming(false)}>
          <p>This will add {plural(s.to_import, "sale")} and their document obligations to your records
            {s.new_estates.length ? `, and create ${plural(s.new_estates.length, "new estate")}` : ""}. {plural(s.total_rows - s.to_import, "row")} will be skipped.</p>
          <p className="muted small">You can undo the import afterwards, as long as nobody has edited what it created.</p>
          {commitError ? <p className="form-error" role="alert">{commitError.message}</p> : null}
          <div className="form-actions">
            <button type="button" className="btn btn-primary" onClick={commit} disabled={busy}>{busy ? "Importing…" : "Yes, import"}</button>
            <button type="button" className="btn" onClick={() => setConfirming(false)}>Not yet</button>
          </div>
        </Modal>
      ) : null}
    </div>
  );
}
