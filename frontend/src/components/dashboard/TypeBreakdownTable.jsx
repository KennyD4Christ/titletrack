import { Link } from "react-router-dom";
import Money from "../common/Money.jsx";
import { days } from "../../utils/format.js";
import { obligationsLink } from "../../utils/links.js";

export default function TypeBreakdownTable({ rows }) {
  if (!rows.length) return null;
  return (
    <section className="card" aria-labelledby="types-title">
      <h2 id="types-title">By document</h2>
      <div className="table-wrap">
        <table>
          <thead><tr><th>Document</th><th className="num">Open</th><th className="num">Overdue</th><th className="num">Buyers waiting</th><th className="num">Money attached</th><th className="num">Oldest</th></tr></thead>
          <tbody>
            {rows.map((r) => (
              <tr key={r.type_id}>
                <td><Link to={obligationsLink({ type: r.type_id, open: 1 })}>{r.type}</Link></td>
                <td className="num">{r.open}</td>
                <td className="num">{r.overdue > 0 ? <Link to={obligationsLink({ type: r.type_id, overdue: 1 })}>{r.overdue}</Link> : 0}</td>
                <td className="num">{r.buyers}</td>
                <td className="num"><Money value={r.amount} /></td>
                <td className="num">{days(r.oldest_days)}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      <p className="muted small">The same buyer can be waiting on several documents, so these amounts overlap and must not be added together.</p>
    </section>
  );
}
