import { useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { Link, useLocation, useNavigate } from "react-router-dom";
import { listAllEstates } from "../api/pagedLists.js";
import { useAuth } from "../context/AuthContext.jsx";
import { useRefreshAfterSave } from "../hooks/useRefreshAfterSave.js";
import { can } from "../utils/permissions.js";
import PageHeader from "../components/layout/PageHeader.jsx";
import LoadingState from "../components/common/LoadingState.jsx";
import ErrorState from "../components/common/ErrorState.jsx";
import EmptyState from "../components/common/EmptyState.jsx";
import EstateFormModal from "../components/estates/EstateFormModal.jsx";

export default function EstatesPage() {
  const { user } = useAuth();
  const navigate = useNavigate();
  const location = useLocation();
  const refresh = useRefreshAfterSave();
  const canManage = can(user, "manage_inventory");
  const [modal, setModal] = useState(null);
  const [notice, setNotice] = useState(location.state?.notice || "");
  const query = useQuery({ queryKey: ["estates", "all"], queryFn: listAllEstates });

  let body;
  if (query.isPending) body = <LoadingState />;
  else if (query.isError) body = <ErrorState error={query.error} onRetry={() => query.refetch()} />;
  else if (query.data.results.length === 0) {
    body = (
      <EmptyState title="No estates yet.">
        {canManage ? "Add an estate, then register its plots before any are sold." : "Estates appear here once they have been added."}
      </EmptyState>
    );
  } else {
    const { count, results } = query.data;
    body = (
      <>
        {count > results.length ? (
          <p className="banner banner-warn" role="status">{`Showing ${results.length} of ${count} estates.`}</p>
        ) : null}
        <div className="table-wrap">
          <table>
            <caption className="sr-only">Estates</caption>
            <thead><tr><th>Estate</th><th>Location</th><th><span className="sr-only">Actions</span></th></tr></thead>
            <tbody>
              {results.map((estate) => (
                <tr key={estate.id}>
                  <td><Link to={`/estates/${estate.id}`}>{estate.name}</Link></td>
                  <td>{estate.location || "—"}</td>
                  <td>
                    {canManage ? (
                      <button type="button" className="btn btn-small" aria-label={`Edit ${estate.name}`}
                        onClick={() => setModal({ type: "edit", estate })}>Edit</button>
                    ) : null}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </>
    );
  }

  return (
    <>
      <PageHeader
        title="Estates"
        subtitle="Every estate and its plots, sold or not."
        actions={canManage ? <button type="button" className="btn btn-primary" onClick={() => setModal({ type: "create" })}>Add estate</button> : null}
      />
      {notice ? <p className="banner banner-ok" role="status">{notice}</p> : null}
      <section className="card">{body}</section>
      {modal?.type === "create" ? (
        <EstateFormModal
          onClose={() => setModal(null)}
          onDone={async (estate) => {
            await refresh();
            navigate(`/estates/${estate.id}`, { state: { notice: "Estate added. Now register its plots." } });
          }}
        />
      ) : null}
      {modal?.type === "edit" ? (
        <EstateFormModal
          estate={modal.estate}
          onClose={() => setModal(null)}
          onDone={async () => {
            setModal(null);
            setNotice("Estate saved.");
            await refresh();
          }}
        />
      ) : null}
    </>
  );
}
