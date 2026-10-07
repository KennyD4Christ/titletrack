import { Link } from "react-router-dom";
import { obligationsLink } from "../../utils/links.js";

export default function WorkloadTable({ workload }) {
  return (
    <section className="card" aria-labelledby="workload-title">
      <h2 id="workload-title">Who holds the ball</h2>
      <h3>By department</h3>
      <div className="table-wrap">
        <table>
          <thead><tr><th>Department</th><th className="num">Open</th><th className="num">Overdue</th><th className="num">Not updated</th><th className="num">Waiting on others</th><th className="num">No owner</th></tr></thead>
          <tbody>
            {workload.by_department.map((d) => (
              <tr key={d.department}>
                <td>{d.department}</td>
                <td className="num">{d.open}</td><td className="num">{d.overdue}</td><td className="num">{d.stale}</td>
                <td className="num">{d.awaiting_external}</td><td className="num">{d.unowned}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      {workload.by_owner.length ? (
        <>
          <h3>By person</h3>
          <div className="table-wrap">
            <table>
              <thead><tr><th>Person</th><th>Departments</th><th className="num">Open</th><th className="num">Overdue</th><th className="num">Not updated</th></tr></thead>
              <tbody>
                {workload.by_owner.map((o) => (
                  <tr key={o.owner_id}>
                    <td><Link to={obligationsLink({ owner: o.owner_id, open: 1 })}>{o.owner}</Link></td>
                    <td>{o.departments.join(", ")}</td>
                    <td className="num">{o.open}</td><td className="num">{o.overdue}</td><td className="num">{o.stale}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </>
      ) : null}
    </section>
  );
}
