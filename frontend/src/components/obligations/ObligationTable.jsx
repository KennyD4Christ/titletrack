import { Link } from "react-router-dom";
import AgeBadge from "../common/AgeBadge.jsx";
import StaleBadge from "../common/StaleBadge.jsx";
import StatusBadge from "../common/StatusBadge.jsx";
import { formatDate } from "../../utils/format.js";

export default function ObligationTable({ rows, showOwner = true }) {
  return (
    <div className="table-wrap">
      <table>
        <thead>
          <tr>
            <th>Buyer</th><th>Document</th><th>Status</th>{showOwner ? <th>Owner</th> : null}
            <th>Waiting</th><th>Next action</th><th>Updated</th>
          </tr>
        </thead>
        <tbody>
          {rows.map((r) => (
            <tr key={r.id} className={r.is_cancelled ? "row-cancelled" : ""}>
              <td>
                <Link to={`/obligations/${r.id}`}>{r.buyer_name}</Link>
                <div className="muted small">{r.estate_name} · {r.plot_number}</div>
              </td>
              <td>{r.type_name}</td>
              <td>
                <StatusBadge status={r.status} />
                {r.is_cancelled ? <span className="badge badge-cancelled">Sale cancelled</span> : null}
              </td>
              {showOwner ? <td>{r.owner_name || <span className="badge badge-unassigned">Unassigned</span>}</td> : null}
              <td><AgeBadge daysOutstanding={r.days_outstanding} daysOverdue={r.days_overdue} /></td>
              <td>
                {r.next_action || <span className="muted">None set</span>}
                {r.next_review_date ? <div className="muted small">Review {formatDate(r.next_review_date)}</div> : null}
              </td>
              <td><StaleBadge staleness={r.staleness} updatedAt={r.last_updated_at} /></td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}
