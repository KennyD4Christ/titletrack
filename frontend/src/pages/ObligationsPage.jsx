import { useEffect, useState } from "react";
import { keepPreviousData, useQuery } from "@tanstack/react-query";
import { useSearchParams } from "react-router-dom";
import { listObligations } from "../api/obligations.js";
import { useReferenceData } from "../hooks/useReferenceData.js";
import { useRefreshAfterSave } from "../hooks/useRefreshAfterSave.js";
import { useAuth } from "../context/AuthContext.jsx";
import { can } from "../utils/permissions.js";
import { plural } from "../utils/format.js";
import QuickUpdateModal from "../components/obligations/QuickUpdateModal.jsx";
import BulkUpdateModal from "../components/obligations/BulkUpdateModal.jsx";
import PageHeader from "../components/layout/PageHeader.jsx";
import ObligationFilters from "../components/obligations/ObligationFilters.jsx";
import ObligationTable from "../components/obligations/ObligationTable.jsx";
import Pagination from "../components/obligations/Pagination.jsx";
import LoadingState from "../components/common/LoadingState.jsx";
import ErrorState from "../components/common/ErrorState.jsx";
import EmptyState from "../components/common/EmptyState.jsx";

const PAGE_SIZE = 25;
const FILTER_KEYS = ["q", "status", "estate", "type", "owner", "department", "external_party", "open", "overdue",
  "stale", "incomplete", "due_within", "age_min", "age_max", "ordering", "page", "include_cancelled"];

function chipsFor(filters, users) {
  const chips = [];
  if (filters.owner) {
    const u = users.find((x) => String(x.id) === filters.owner);
    chips.push({ key: "owner", label: `Owner: ${u ? u.full_name : filters.owner}` });
  }
  if (filters.due_within) chips.push({ key: "due_within", label: `Due within ${filters.due_within} days` });
  if (filters.age_min || filters.age_max) {
    const lo = filters.age_min, hi = filters.age_max;
    const label = lo && hi ? `Waiting ${lo}–${hi} days` : lo ? `Waiting over ${Number(lo) - 1} days` : `Waiting up to ${hi} days`;
    chips.push({ key: ["age_min", "age_max"], label });
  }
  if (filters.external_party) chips.push({ key: "external_party", label: `External party: ${filters.external_party}` });
  return chips;
}

export default function ObligationsPage() {
  const [params, setParams] = useSearchParams();
  const filters = Object.fromEntries([...params].filter(([k]) => FILTER_KEYS.includes(k)));
  const page = Math.max(1, Number(filters.page) || 1);
  const reference = useReferenceData();
  const { user } = useAuth();
  const refresh = useRefreshAfterSave();
  const canBulk = can(user, "bulk_update");
  const [selected, setSelected] = useState(() => new Set());
  const [editing, setEditing] = useState(null);
  const [bulkOpen, setBulkOpen] = useState(false);
  const [message, setMessage] = useState("");
  useEffect(() => setSelected(new Set()), [params.toString()]);   // a different list means a new selection

  const query = useQuery({
    queryKey: ["obligations", filters],
    queryFn: () => listObligations({ ...filters, page, page_size: PAGE_SIZE }),
    placeholderData: keepPreviousData,
  });

  function update(patch, { keepPage = false } = {}) {
    const next = new URLSearchParams(params);
    for (const [key, value] of Object.entries(patch)) {
      if (value === "" || value === null || value === undefined) next.delete(key);
      else next.set(key, String(value));
    }
    if (!keepPage) next.delete("page");
    setParams(next);
  }
  const removeChip = (key) => update(Object.fromEntries([].concat(key).map((k) => [k, ""])));

  const chips = chipsFor(filters, reference.users);
  let body;
  if (query.isPending) body = <LoadingState />;
  else if (query.isError) body = <ErrorState error={query.error} onRetry={() => query.refetch()} />;
  else if (query.data.count === 0) body = <EmptyState title="Nothing matches these filters.">Try clearing a filter.</EmptyState>;
  else {
    body = (
      <>
        <ObligationTable
          rows={query.data.results}
          onUpdate={setEditing}
          selection={canBulk ? {
            selected,
            onToggle: (id) => setSelected((prev) => { const next = new Set(prev); next.has(id) ? next.delete(id) : next.add(id); return next; }),
            onToggleAll: (ids, on) => setSelected(on ? new Set(ids) : new Set()),
          } : undefined}
        />
        <Pagination page={page} pageSize={PAGE_SIZE} count={query.data.count} onPage={(p) => update({ page: p }, { keepPage: true })} />
      </>
    );
  }

  return (
    <>
      <PageHeader title="Obligations" subtitle="Everything owed to buyers, with who holds it and how long it has waited." />
      <ObligationFilters
        filters={filters}
        estates={reference.estates}
        types={reference.types}
        users={reference.users}
        onChange={update}
        onClear={() => setParams(new URLSearchParams())}
      />
      {chips.length ? (
        <ul className="chips" aria-label="Active filters">
          {chips.map((c) => (
            <li key={c.label}>
              <span className="chip">{c.label}<button type="button" aria-label={`Remove filter ${c.label}`} onClick={() => removeChip(c.key)}>×</button></span>
            </li>
          ))}
        </ul>
      ) : null}
      {message ? <p className="banner banner-ok" role="status">{message}</p> : null}
      {selected.size > 0 ? (
        <div className="bulk-bar" role="region" aria-label="Selected obligations">
          <strong>{selected.size} selected</strong>
          <button type="button" className="btn btn-primary" onClick={() => setBulkOpen(true)}>Update selected</button>
          <button type="button" className="btn" onClick={() => setSelected(new Set())}>Clear selection</button>
        </div>
      ) : null}
      <section className="card">{body}</section>
      {editing ? (
        <QuickUpdateModal
          obligation={editing}
          users={reference.users}
          onClose={() => setEditing(null)}
          onSaved={async () => { setEditing(null); setMessage("Saved."); await refresh(); }}
        />
      ) : null}
      {bulkOpen ? (
        <BulkUpdateModal
          ids={[...selected]}
          users={reference.users}
          labelFor={(id) => { const r = query.data?.results.find((x) => x.id === id); return r ? `${r.buyer_name}, ${r.type_name}` : `#${id}`; }}
          onClose={() => setBulkOpen(false)}
          onDone={async (result) => { setBulkOpen(false); setSelected(new Set()); setMessage(`Updated ${plural(result.updated, "obligation")}.`); await refresh(); }}
        />
      ) : null}
    </>
  );
}
