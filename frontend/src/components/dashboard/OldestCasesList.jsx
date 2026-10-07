import { Link } from "react-router-dom";
import AgeBadge from "../common/AgeBadge.jsx";
import StatusBadge from "../common/StatusBadge.jsx";

export default function OldestCasesList({ rows }) {
  if (!rows.length) return null;
  return (
    <section className="card" aria-labelledby="oldest-title">
      <h2 id="oldest-title">Oldest cases</h2>
      <div className="table-wrap">
        <table>
          <thead><tr><th>Buyer</th><th>Document</th><th>Waiting</th><th>Status</th><th>Owner</th></tr></thead>
          <tbody>
            {rows.map((r) => (
              <tr key={r.id}>
                <td><Link to={`/obligations/${r.id}`}>{r.buyer_name}</Link><div className="muted small">{r.estate_name} · {r.plot_number}</div></td>
                <td>{r.type_name}</td>
                <td><AgeBadge daysOutstanding={r.days_outstanding} daysOverdue={r.days_overdue} /></td>
                <td><StatusBadge status={r.status} /></td>
                <td>{r.owner_name || <span className="badge badge-unassigned">Unassigned</span>}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </section>
  );
}
