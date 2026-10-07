import { days } from "../../utils/format.js";

export default function AgeBadge({ daysOutstanding, daysOverdue }) {
  if (daysOutstanding === null || daysOutstanding === undefined) return <span className="muted">—</span>;
  return (
    <span className="age">
      {days(daysOutstanding)}
      {daysOverdue > 0 ? <span className="badge badge-overdue">{days(daysOverdue)} overdue</span> : null}
    </span>
  );
}
