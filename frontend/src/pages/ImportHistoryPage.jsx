import { useQuery, useQueryClient } from "@tanstack/react-query";
import { Link } from "react-router-dom";
import { listImports } from "../api/imports.js";
import PageHeader from "../components/layout/PageHeader.jsx";
import LoadingState from "../components/common/LoadingState.jsx";
import ErrorState from "../components/common/ErrorState.jsx";
import EmptyState from "../components/common/EmptyState.jsx";
import UndoButton from "../components/imports/UndoButton.jsx";
import { useRefreshAfterSave } from "../hooks/useRefreshAfterSave.js";
import { formatDateTime } from "../utils/format.js";

function result(r) {
  if (r.status === "committed" && r.counts) return `${r.counts.sales_created} sales added, ${r.counts.rows_skipped} rows skipped`;
  if (r.status === "uploaded" || r.status === "mapped" || r.status === "validated") return "Not finished (nothing was saved)";
  if (r.status === "undone") return "Removed";
  return "";
}

export default function ImportHistoryPage() {
  const query = useQuery({ queryKey: ["imports"], queryFn: listImports });
  const refresh = useRefreshAfterSave();
  const queryClient = useQueryClient();

  let body;
  if (query.isPending) body = <LoadingState />;
  else if (query.isError) body = <ErrorState error={query.error} onRetry={() => query.refetch()} />;
  else if (query.data.results.length === 0) body = <EmptyState title="No imports yet.">Your first import will appear here.</EmptyState>;
  else {
    body = (
      <div className="table-wrap">
        <table>
          <thead><tr><th>File</th><th>When</th><th>By</th><th className="num">Rows</th><th>Status</th><th>Result</th><th><span className="sr-only">Actions</span></th></tr></thead>
          <tbody>
            {query.data.results.map((r) => (
              <tr key={r.id}>
                <td>{r.filename}</td>
                <td>{formatDateTime(r.committed_at || r.created_at)}</td>
                <td>{r.created_by_name || "—"}</td>
                <td className="num">{r.row_count}</td>
                <td><span className={`badge badge-import-${r.status}`}>{r.status_label}</span></td>
                <td>{result(r)}</td>
                <td>
                  {r.status === "committed" ? (
                    <UndoButton importId={r.id} filename={r.filename} onUndone={async () => { await refresh(); await queryClient.invalidateQueries({ queryKey: ["imports"] }); }} />
                  ) : null}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    );
  }
  return (
    <>
      <PageHeader title="Past imports" actions={<Link to="/import" className="btn btn-primary">New import</Link>} />
      <section className="card">{body}</section>
    </>
  );
}
