import { useState } from "react";
import { keepPreviousData, useQuery, useQueryClient } from "@tanstack/react-query";
import { Link, useSearchParams } from "react-router-dom";
import { getStaffOptions, listDepartments, listStaff, reactivateStaff } from "../api/staff.js";
import { useAuth } from "../context/AuthContext.jsx";
import { ROLE_LABELS } from "../utils/labels.js";
import { obligationsLink } from "../utils/links.js";
import { errorMessage, plural } from "../utils/staff.js";
import PageHeader from "../components/layout/PageHeader.jsx";
import Pagination from "../components/obligations/Pagination.jsx";
import LoadingState from "../components/common/LoadingState.jsx";
import ErrorState from "../components/common/ErrorState.jsx";
import EmptyState from "../components/common/EmptyState.jsx";
import StaffFormModal from "../components/staff/StaffFormModal.jsx";
import LinkModal from "../components/staff/LinkModal.jsx";
import DeactivateModal from "../components/staff/DeactivateModal.jsx";
import ConfirmModal from "../components/staff/ConfirmModal.jsx";
import HistoryModal from "../components/staff/HistoryModal.jsx";

const PAGE_SIZE = 25;
const FILTER_KEYS = ["q", "role", "is_active", "page"];

function StaffFilters({ filters, onChange, onClear }) {
  const [text, setText] = useState(filters.q || "");
  return (
    <form className="filters" onSubmit={(e) => { e.preventDefault(); onChange({ q: text.trim() }); }} role="search">
      <label>
        Search
        <input type="search" value={text} placeholder="Name or email" onChange={(e) => setText(e.target.value)} />
      </label>
      <label>
        Role
        <select value={filters.role || ""} onChange={(e) => onChange({ role: e.target.value })}>
          <option value="">All</option>
          {Object.entries(ROLE_LABELS).map(([value, label]) => <option key={value} value={value}>{label}</option>)}
        </select>
      </label>
      <label>
        Status
        <select value={filters.is_active || ""} onChange={(e) => onChange({ is_active: e.target.value })}>
          <option value="">All</option>
          <option value="1">Active</option>
          <option value="0">Deactivated</option>
        </select>
      </label>
      <div className="filter-actions">
        <button type="submit" className="btn btn-primary">Search</button>
        <button type="button" className="btn" onClick={() => { setText(""); onClear(); }}>Clear filters</button>
      </div>
    </form>
  );
}

function RowActions({ row, viewer, onAction }) {
  const name = row.full_name;
  const button = (type, label, extra = "") => (
    <button type="button" className={`btn btn-small ${extra}`} aria-label={`${label} ${name}`} onClick={() => onAction(type, row)}>
      {label}
    </button>
  );
  const canSeeHistory = row.can_manage || viewer.role === "md";
  if (!row.can_manage) {
    return (
      <div className="row-actions">
        <span className="muted small">{row.is_protected ? "Protected account" : "Read only"}</span>
        {canSeeHistory ? button("history", "History") : null}
      </div>
    );
  }
  return (
    <div className="row-actions">
      {button("edit", "Edit")}
      {row.is_active && !row.is_self ? button("setup", "Setup link") : null}
      {row.is_active && !row.is_self ? button("reset", "Reset link") : null}
      {row.is_active && !row.is_self ? button("deactivate", "Deactivate", "btn-danger") : null}
      {!row.is_active ? button("reactivate", "Reactivate") : null}
      {button("history", "History")}
    </div>
  );
}

function StaffTable({ rows, viewer, onAction }) {
  return (
    <div className="table-wrap">
      <table>
        <thead>
          <tr><th>Name</th><th>Role</th><th>Department</th><th>Status</th><th className="num">Open obligations</th><th>Actions</th></tr>
        </thead>
        <tbody>
          {rows.map((row) => (
            <tr key={row.id} className={row.is_active ? "" : "row-cancelled"}>
              <td>
                {row.full_name}{row.is_self ? <span className="badge">You</span> : null}
                <div className="muted small">{row.email || row.username}</div>
              </td>
              <td>{ROLE_LABELS[row.role] || row.role}</td>
              <td>{row.department_name || "—"}</td>
              <td>
                {row.is_active ? <span className="badge badge-completed">Active</span> : <span className="badge badge-cancelled">Deactivated</span>}
                {row.is_protected ? <span className="badge">Admin account</span> : null}
              </td>
              <td className="num">{row.open_obligations}</td>
              <td><RowActions row={row} viewer={viewer} onAction={onAction} /></td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

export default function StaffPage() {
  const [params, setParams] = useSearchParams();
  const filters = Object.fromEntries([...params].filter(([k]) => FILTER_KEYS.includes(k)));
  const page = Math.max(1, Number(filters.page) || 1);
  const { user } = useAuth();
  const queryClient = useQueryClient();
  const [modal, setModal] = useState(null);
  const [notice, setNotice] = useState(null);

  const query = useQuery({
    queryKey: ["staff", filters],
    queryFn: () => listStaff({ ...filters, page, page_size: PAGE_SIZE }),
    placeholderData: keepPreviousData,
  });
  const options = useQuery({ queryKey: ["staff-options"], queryFn: getStaffOptions, staleTime: 5 * 60_000 });
  const departments = useQuery({ queryKey: ["departments"], queryFn: listDepartments, staleTime: 5 * 60_000 });

  // Changing a person changes the staff list and also the people pickers used on obligation screens.
  const refresh = () => Promise.all([
    queryClient.invalidateQueries({ queryKey: ["staff"] }),
    queryClient.invalidateQueries({ queryKey: ["users"] }),
  ]);
  const close = () => setModal(null);
  const say = (text, extra = {}) => setNotice({ text, ...extra });

  function update(patch, { keepPage = false } = {}) {
    const next = new URLSearchParams(params);
    for (const [key, value] of Object.entries(patch)) {
      if (value === "" || value === null || value === undefined) next.delete(key);
      else next.set(key, String(value));
    }
    if (!keepPage) next.delete("page");
    setParams(next);
  }

  function onAction(type, row) {
    setNotice(null);
    if (type === "edit") setModal({ type: "form", mode: "edit", account: row });
    else if (type === "setup" || type === "reset") setModal({ type: "link", purpose: type, account: row });
    else setModal({ type, account: row });
  }

  async function created({ account, outcome, linkError }) {
    await refresh();
    if (linkError) {
      say(`${account.full_name} was added, but the setup link could not be issued: ${errorMessage(linkError)} Use "Setup link" on their row to try again.`);
      setModal(null);
    } else if (outcome?.status === "revealed") {
      say(`${account.full_name} was added.`);
      setModal({ type: "link", purpose: "setup", account, outcome });
    } else if (outcome?.status === "emailed") {
      say(`${account.full_name} was added and the setup link was emailed to ${account.email}.`);
      setModal(null);
    } else if (outcome?.status === "email_failed") {
      say(`${account.full_name} was added, but the setup email could not be sent. Use "Setup link" on their row to show the link instead.`);
      setModal(null);
    } else {
      say(`${account.full_name} was added. Use "Setup link" on their row when they are ready to sign in.`);
      setModal(null);
    }
  }

  async function deactivated(result) {
    await refresh();
    setModal(null);
    const remaining = result.open_obligations_remaining || 0;
    say(
      remaining > 0
        ? `${result.full_name} was deactivated. ${plural(remaining, "open obligation")} still ${remaining === 1 ? "belongs" : "belong"} to them until reassigned.`
        : `${result.full_name} was deactivated.`,
      remaining > 0 ? { link: obligationsLink({ owner: result.id, open: true }), linkLabel: "Reassign them from the obligations list" } : {},
    );
  }

  let body;
  if (query.isPending) body = <LoadingState />;
  else if (query.isError) body = <ErrorState error={query.error} onRetry={() => query.refetch()} />;
  else if (query.data.count === 0) body = <EmptyState title="No accounts match these filters.">Try clearing a filter, or add an account.</EmptyState>;
  else {
    body = (
      <>
        <StaffTable rows={query.data.results} viewer={user} onAction={onAction} />
        <Pagination page={page} pageSize={PAGE_SIZE} count={query.data.count} onPage={(p) => update({ page: p }, { keepPage: true })} />
      </>
    );
  }

  const roles = options.data?.roles || [];
  return (
    <>
      <PageHeader
        title="Staff"
        subtitle="Who can sign in to your company, and what each person may do."
        actions={<button type="button" className="btn btn-primary" onClick={() => { setNotice(null); setModal({ type: "form", mode: "create" }); }}>Add account</button>}
      />
      <StaffFilters filters={filters} onChange={update} onClear={() => setParams(new URLSearchParams())} />
      {notice ? (
        <p className="banner banner-ok" role="status">
          {notice.text}{notice.link ? <>{" "}<Link to={notice.link}>{notice.linkLabel}</Link></> : null}
        </p>
      ) : null}
      <section className="card">{body}</section>

      {modal?.type === "form" ? (
        <StaffFormModal
          mode={modal.mode}
          account={modal.account}
          roles={roles}
          departments={departments.data || []}
          onClose={close}
          onDone={modal.mode === "create" ? created : async ({ account }) => { await refresh(); setModal(null); say(`${account.full_name} was updated.`); }}
        />
      ) : null}
      {modal?.type === "link" ? <LinkModal account={modal.account} purpose={modal.purpose} initialOutcome={modal.outcome} onClose={close} /> : null}
      {modal?.type === "deactivate" ? <DeactivateModal account={modal.account} onClose={close} onDone={deactivated} /> : null}
      {modal?.type === "reactivate" ? (
        <ConfirmModal
          title={`Reactivate ${modal.account.full_name}`}
          message="They will be able to sign in again once they have a password. Links issued before the deactivation stay invalid, so issue a new link if they need one."
          confirmLabel="Reactivate account"
          onClose={close}
          onConfirm={async () => { await reactivateStaff(modal.account.id); await refresh(); setModal(null); say(`${modal.account.full_name} was reactivated.`); }}
        />
      ) : null}
      {modal?.type === "history" ? <HistoryModal account={modal.account} onClose={close} /> : null}
    </>
  );
}
