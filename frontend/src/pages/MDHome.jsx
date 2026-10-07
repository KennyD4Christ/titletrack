import AttentionPanel from "../components/dashboard/AttentionPanel.jsx";
import ExposureCard from "../components/dashboard/ExposureCard.jsx";
import AgeingChart from "../components/dashboard/AgeingChart.jsx";
import EstateBreakdownTable from "../components/dashboard/EstateBreakdownTable.jsx";
import TypeBreakdownTable from "../components/dashboard/TypeBreakdownTable.jsx";
import DelayReasons from "../components/dashboard/DelayReasons.jsx";
import OldestCasesList from "../components/dashboard/OldestCasesList.jsx";
import { plural } from "../utils/format.js";

export default function MDHome({ data }) {
  return (
    <div className="stack">
      <div className="grid-2">
        <ExposureCard exposure={data.exposure} />
        <AttentionPanel attention={data.attention} />
      </div>
      <div className="grid-2">
        <AgeingChart ageing={data.ageing} />
        <DelayReasons rows={data.delay_reasons} />
      </div>
      <EstateBreakdownTable rows={data.by_estate} />
      <TypeBreakdownTable rows={data.by_type} />
      <OldestCasesList rows={data.oldest} />
      {data.cancelled_last_30_days > 0 ? (
        <p className="muted small">
          {plural(data.cancelled_last_30_days, "sale")} cancelled in the last 30 days. Cancelled sales are kept for the record
          and are not counted anywhere above.
        </p>
      ) : null}
    </div>
  );
}
