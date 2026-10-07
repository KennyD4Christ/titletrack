import { STALENESS_LABELS } from "../../utils/labels.js";
import { formatDate } from "../../utils/format.js";

/** Colour AND text, so it never depends on colour alone. */
export default function StaleBadge({ staleness, updatedAt }) {
  if (!staleness) return <span className="muted">—</span>;
  return (
    <span className={`badge badge-stale-${staleness}`} title={`Last updated ${formatDate(updatedAt)}`}>
      {STALENESS_LABELS[staleness]}
    </span>
  );
}
