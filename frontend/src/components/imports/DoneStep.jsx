import { Link } from "react-router-dom";
import UndoButton from "./UndoButton.jsx";
import { obligationsLink } from "../../utils/links.js";

export default function DoneStep({ batchId, filename, counts, onAnother, onUndone }) {
  const rows = [
    ["Sales added", counts.sales_created], ["Buyers added", counts.buyers_created], ["Plots added", counts.plots_created],
    ["Estates created", counts.estates_created], ["Document statuses recorded", counts.statuses_applied], ["Rows skipped", counts.rows_skipped],
  ];
  return (
    <section className="card">
      <h2>Import complete</h2>
      <p className="banner banner-ok" role="status">Your data from <strong>{filename}</strong> is now in TitleTrack.</p>
      <dl className="facts">
        {rows.map(([label, value]) => <div key={label}><dt>{label}</dt><dd>{value}</dd></div>)}
      </dl>
      <p className="muted">Imported items start with no owner. The list below shows who still needs assigning, which is the first job to do.</p>
      <div className="form-actions">
        <Link className="btn btn-primary" to={obligationsLink({ incomplete: 1 })}>Assign owners</Link>
        <Link className="btn" to="/">See the dashboard</Link>
        <button type="button" className="btn" onClick={onAnother}>Import another file</button>
        <UndoButton importId={batchId} filename={filename} onUndone={onUndone} />
      </div>
    </section>
  );
}
