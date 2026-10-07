import { Link } from "react-router-dom";
import Money from "../common/Money.jsx";
import { days } from "../../utils/format.js";
import { obligationsLink } from "../../utils/links.js";

export default function EstateBreakdownTable({ rows }) {
  if (!rows.length) return null;
  return (
    <section className="card" aria-labelledby="estates-title">
      <h2 id="estates-title">By estate</h2>
      <div className="table-wrap">
        <table>
          <thead><tr><th>Estate</th><th className="num">Money collected</th><th className="num">Buyers</th><th className="num">Open</th><th className="num">Overdue</th><th className="num">Oldest</th></tr></thead>
          <tbody>
            {rows.map((r) => (
              <tr key={r.estate_id}>
                <td><Link to={obligationsLink({ estate: r.estate_id, open: 1 })}>{r.estate}</Link></td>
                <td className="num"><Money value={r.amount} /></td>
                <td className="num">{r.buyers}</td>
                <td className="num">{r.open_obligations}</td>
                <td className="num">{r.overdue > 0 ? <Link to={obligationsLink({ estate: r.estate_id, overdue: 1 })}>{r.overdue}</Link> : 0}</td>
                <td className="num">{days(r.oldest_days)}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </section>
  );
}
