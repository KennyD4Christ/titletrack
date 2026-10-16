import { useState } from "react";
import { keepPreviousData, useQuery } from "@tanstack/react-query";
import { getStaffHistory } from "../../api/staff.js";
import { describeChanges, formatWhen } from "../../utils/staff.js";
import Modal from "../common/Modal.jsx";
import Pagination from "../obligations/Pagination.jsx";
import LoadingState from "../common/LoadingState.jsx";
import ErrorState from "../common/ErrorState.jsx";
import EmptyState from "../common/EmptyState.jsx";

const PAGE_SIZE = 20;

/** Who did what to this account, oldest first. The API never includes a password, token or link. */
export default function HistoryModal({ account, onClose }) {
  const [page, setPage] = useState(1);
  const query = useQuery({
    queryKey: ["staff", "history", account.id, page],
    queryFn: () => getStaffHistory(account.id, { page, page_size: PAGE_SIZE }),
    placeholderData: keepPreviousData,
  });

  let body;
  if (query.isPending) body = <LoadingState />;
  else if (query.isError) body = <ErrorState error={query.error} onRetry={() => query.refetch()} />;
  else if (query.data.count === 0) body = <EmptyState title="No history yet." />;
  else {
    body = (
      <>
        <div className="table-wrap">
          <table>
            <thead><tr><th>When</th><th>Event</th><th>By</th><th>Details</th></tr></thead>
            <tbody>
              {query.data.results.map((event) => (
                <tr key={event.id}>
                  <td>{formatWhen(event.created_at)}</td>
                  <td>{event.action_label}</td>
                  <td>{event.actor_name || "System"}</td>
                  <td>
                    {describeChanges(event.changes).map((line) => <div key={line}>{line}</div>)}
                    {event.note ? <div className="muted small pre-line">{event.note}</div> : null}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
        <Pagination page={page} pageSize={PAGE_SIZE} count={query.data.count} onPage={setPage} />
      </>
    );
  }

  return (
    <Modal title={`History of ${account.full_name}`} onClose={onClose}>
      <div className="stack">{body}</div>
    </Modal>
  );
}
