import { Link } from "react-router-dom";
import AgeBadge from "../common/AgeBadge.jsx";
import StaleBadge from "../common/StaleBadge.jsx";
import StatusBadge from "../common/StatusBadge.jsx";
import { formatDate } from "../../utils/format.js";

/** `onUpdate(row)` adds an Update button on rows the person may edit.
 *  `selection = { selected: Set, onToggle(id), onToggleAll(ids, on) }` adds checkboxes for bulk actions. */
export default function ObligationTable({ rows, showOwner = true, onUpdate, selection }) {
  const editable = rows.filter((r) => r.can_edit);
  const allSelected = selection && editable.length > 0 && editable.every((r) => selection.selected.has(r.id));
  return (
    <div className="table-wrap">
      <table>
        <thead>
          <tr>
            {selection ? (
              <th className="check-col">
                <input type="checkbox" aria-label="Select all on this page" checked={Boolean(allSelected)} disabled={editable.length === 0}
                  onChange={(e) => selection.onToggleAll(editable.map((r) => r.id), e.target.checked)} />
              </th>
            ) : null}
            <th>Buyer</th><th>Document</th><th>Status</th>{showOwner ? <th>Owner</th> : null}
            <th>Waiting</th><th>Next action</th><th>Updated</th>{onUpdate ? <th><span className="sr-only">Actions</span></th> : null}
          </tr>
        </thead>
        <tbody>
          {rows.map((r) => (
            <tr key={r.id} className={r.is_cancelled ? "row-cancelled" : ""}>
              {selection ? (
                <td className="check-col">
                  {r.can_edit ? (
                    <input type="checkbox" aria-label={`Select ${r.buyer_name}, ${r.type_name}`} checked={selection.selected.has(r.id)}
                      onChange={() => selection.onToggle(r.id)} />
                  ) : null}
                </td>
              ) : null}
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
              {onUpdate ? (
                <td>{r.can_edit ? <button type="button" className="btn" aria-label={`Update ${r.buyer_name}, ${r.type_name}`} onClick={() => onUpdate(r)}>Update</button> : null}</td>
              ) : null}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}
