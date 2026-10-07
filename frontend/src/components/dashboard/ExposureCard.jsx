import { days, formatNaira, formatNumber, plural } from "../../utils/format.js";

export default function ExposureCard({ exposure }) {
  return (
    <section className="card exposure" aria-labelledby="exposure-title">
      <h2 id="exposure-title">Collected, but documents still owed</h2>
      <p className="big-number">{formatNaira(exposure.amount)}</p>
      <p className="muted">across {plural(exposure.buyers, "buyer")}</p>
      <dl className="facts">
        <div><dt>Average wait</dt><dd>{exposure.average_wait_days === null ? "—" : days(Math.round(exposure.average_wait_days))}</dd></div>
        <div><dt>Fully paid buyers waiting</dt><dd>{formatNumber(exposure.fully_paid_buyers)} ({formatNaira(exposure.fully_paid_amount)})</dd></div>
      </dl>
    </section>
  );
}
