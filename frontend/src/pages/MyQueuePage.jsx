import { useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { getMyQueue } from "../api/dashboard.js";
import PageHeader from "../components/layout/PageHeader.jsx";
import ObligationTable from "../components/obligations/ObligationTable.jsx";
import LoadingState from "../components/common/LoadingState.jsx";
import ErrorState from "../components/common/ErrorState.jsx";
import EmptyState from "../components/common/EmptyState.jsx";
import QuickUpdateModal from "../components/obligations/QuickUpdateModal.jsx";
import { useReferenceData } from "../hooks/useReferenceData.js";
import { useRefreshAfterSave } from "../hooks/useRefreshAfterSave.js";

const GROUPS = [
  ["overdue", "Overdue"],
  ["due_soon", "Due this week"],
  ["awaiting_external", "Waiting on others"],
  ["other", "Everything else"],
];

export default function MyQueuePage() {
  const query = useQuery({ queryKey: ["queue"], queryFn: getMyQueue });
  const reference = useReferenceData();
  const refresh = useRefreshAfterSave();
  const [editing, setEditing] = useState(null);
  const [message, setMessage] = useState("");
  let body;
  if (query.isPending) body = <LoadingState />;
  else if (query.isError) body = <ErrorState error={query.error} onRetry={() => query.refetch()} />;
  else if (query.data.counts.open === 0) body = <EmptyState title="Nothing is assigned to you right now.">Items you own will appear here.</EmptyState>;
  else {
    body = (
      <div className="stack">
        {GROUPS.filter(([key]) => query.data[key].length > 0).map(([key, title]) => (
          <section className="card" key={key} aria-labelledby={`q-${key}`}>
            <h2 id={`q-${key}`}>{title} <span className="count">{query.data[key].length}</span></h2>
            <ObligationTable rows={query.data[key]} showOwner={false} onUpdate={setEditing} />
          </section>
        ))}
      </div>
    );
  }
  return (
    <>
      <PageHeader title="My Queue" subtitle="Your own work, most urgent first." />
      {message ? <p className="banner banner-ok" role="status">{message}</p> : null}
      {body}
      {editing ? (
        <QuickUpdateModal
          obligation={editing}
          users={reference.users}
          onClose={() => setEditing(null)}
          onSaved={async () => { setEditing(null); setMessage("Saved."); await refresh(); }}
        />
      ) : null}
    </>
  );
}
