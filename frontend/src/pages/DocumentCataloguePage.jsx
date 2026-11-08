import { useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { useSearchParams } from "react-router-dom";
import { listAllDocumentTypes } from "../api/pagedLists.js";
import { activateDocumentType, deactivateDocumentType } from "../api/documentTypes.js";
import { listDepartments } from "../api/staff.js";
import { days, plural } from "../utils/format.js";
import PageHeader from "../components/layout/PageHeader.jsx";
import LoadingState from "../components/common/LoadingState.jsx";
import ErrorState from "../components/common/ErrorState.jsx";
import EmptyState from "../components/common/EmptyState.jsx";
import DocumentTypeFormModal from "../components/catalogue/DocumentTypeFormModal.jsx";
import EditDocumentTypeModal from "../components/catalogue/EditDocumentTypeModal.jsx";
import RenameDocumentTypeModal from "../components/catalogue/RenameDocumentTypeModal.jsx";
import ConfirmModal from "../components/staff/ConfirmModal.jsx";
import { useRefreshCatalogue } from "../hooks/useRefreshCatalogue.js";

const STATUSES = ["active", "inactive"];

const matches = (type, status) => {
  if (status === "active") return type.is_active;
  if (status === "inactive") return !type.is_active;
  return true;
};

/** The company's document catalogue: list, add, edit, rename, activate and deactivate. Everyone can read it through
 *  the API, but this is a management page, so the route is limited to people who may manage the catalogue. There is
 *  no history here: the API does not expose a catalogue entry's history. */
export default function DocumentCataloguePage() {
  const [params, setParams] = useSearchParams();
  const status = STATUSES.includes(params.get("status")) ? params.get("status") : "";
  const types = useQuery({ queryKey: ["document-types", "all"], queryFn: () => listAllDocumentTypes() });
  const departments = useQuery({ queryKey: ["departments"], queryFn: listDepartments, staleTime: 5 * 60_000 });
  const refresh = useRefreshCatalogue();
  const [modal, setModal] = useState(null);
  const [notice, setNotice] = useState("");

  // A new entry also creates a work type that the Obligations filters list, so both caches are reloaded.
  async function added(entry) {
    setModal(null);
    setNotice(`${entry.name} added to the catalogue.`);
    await refresh();
  }

  // Editing or renaming can change the entry's work type (a rename may create a replacement), so the same caches reload.
  async function edited(entry) {
    setModal(null);
    setNotice(`${entry.name} updated.`);
    await refresh();
  }

  // The old name comes from the row as it was when the dialog opened, never from the refreshed catalogue.
  async function renamed(previousName, entry) {
    setModal(null);
    setNotice(`${previousName} renamed to ${entry.name}.`);
    await refresh();
  }

  // A 409 means the entry changed under the person: reload the list so the page shows the truth.
  const reloadCatalogue = () => types.refetch();

  // Activating or deactivating changes which entries the sale screens offer. A 409 means the entry is already in the
  // wanted state, often because someone else changed it first: the list reloads so the row shows the truth, and the
  // refusal still goes back to the dialog, which shows it.
  async function setActive(entry, active) {
    let result;
    try {
      result = await (active ? activateDocumentType(entry.id) : deactivateDocumentType(entry.id));
    } catch (err) {
      if (err.status === 409) reloadCatalogue();
      throw err;
    }
    setModal(null);
    setNotice(`${result.name} ${active ? "activated" : "deactivated"}.`);
    await refresh();
  }

  function setStatus(value) {
    const next = new URLSearchParams(params);
    if (value) next.set("status", value);
    else next.delete("status");
    setParams(next);
  }

  const departmentName = new Map((departments.data || []).map((d) => [d.id, d.name]));
  function departmentLabel(id) {
    if (id === null || id === undefined) return "—";
    if (departments.isPending) return "…";
    return departmentName.get(id) || `Department #${id}`;
  }

  let body;
  if (types.isPending) {
    body = <LoadingState />;
  } else if (types.isError) {
    body = <ErrorState error={types.error} onRetry={() => types.refetch()} />;
  } else {
    const { results, count } = types.data;
    const rows = results.filter((t) => matches(t, status));
    if (results.length === 0) {
      body = <EmptyState title="The catalogue is empty.">No document types have been added yet.</EmptyState>;
    } else if (rows.length === 0) {
      body = <EmptyState title="No document types match this filter.">Choose another status.</EmptyState>;
    } else {
      body = (
        <>
          <p className="muted small">{`${plural(rows.length, "document type")}, listed by sort order and then name.`}</p>
          <div className="table-wrap">
            <table>
              <thead>
                <tr>
                  <th>Name</th><th>Category</th><th>Preparation time</th><th>Default department</th>
                  <th className="num">Sort order</th><th>Status</th><th><span className="sr-only">Actions</span></th>
                </tr>
              </thead>
              <tbody>
                {rows.map((t) => (
                  <tr key={t.id} className={t.is_active ? "" : "row-cancelled"}>
                    <td>
                      {t.name}
                      {t.description ? <div className="muted small">{t.description}</div> : null}
                    </td>
                    <td>{t.category || "—"}</td>
                    <td>{days(t.sla_days)}</td>
                    <td>{departmentLabel(t.default_department)}</td>
                    <td className="num">{t.sort_order}</td>
                    <td>
                      {t.is_active ? <span className="badge badge-completed">Active</span> : <span className="badge badge-cancelled">Inactive</span>}
                      {t.drives_legacy_generation ? (
                        <span className="badge" title="This entry came from an automatic document type, which still creates obligations on existing sales.">
                          Automatic generation
                        </span>
                      ) : null}
                    </td>
		    <td>
                      <div className="row-actions">
                        <button
                          type="button"
                          className="btn btn-small"
                          aria-label={`Edit details of ${t.name}`}
                          onClick={() => { setNotice(""); setModal({ type: "edit", entry: t }); }}
                        >
                          Edit details
                        </button>
                        <button
                          type="button"
                          className="btn btn-small"
                          aria-label={`Rename ${t.name}`}
                          onClick={() => { setNotice(""); setModal({ type: "rename", entry: t }); }}
                        >
                          Rename
                        </button>
			<button
                          type="button"
                          className="btn btn-small"
                          aria-label={`${t.is_active ? "Deactivate" : "Activate"} ${t.name}`}
                          onClick={() => { setNotice(""); setModal({ type: t.is_active ? "deactivate" : "activate", entry: t }); }}
                        >
                          {t.is_active ? "Deactivate" : "Activate"}
                        </button>
                      </div>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
          {count > results.length ? <p className="muted small">{`Showing ${results.length} of ${count} document types.`}</p> : null}
          {departments.isError ? <p className="muted small">Department names could not be loaded.</p> : null}
        </>
      );
    }
  }

  return (
    <>
      <PageHeader
        title="Document catalogue"
        subtitle="The documents your company tracks on sales, how long each takes to prepare, and who handles it by default."
	actions={(
          <button type="button" className="btn btn-primary" onClick={() => { setNotice(""); setModal({ type: "add" }); }}>
            Add document type
          </button>
        )}
      />
      <div className="filters">
        <label>
          Status
          <select value={status} onChange={(e) => setStatus(e.target.value)}>
            <option value="">All</option>
            <option value="active">Active</option>
            <option value="inactive">Inactive</option>
          </select>
        </label>
      </div>
      {notice ? <p className="banner banner-ok" role="status">{notice}</p> : null}
      <section className="card">{body}</section>
      {modal?.type === "add" ? (
        <DocumentTypeFormModal
          departments={departments.data || []}
          departmentsStatus={departments.status}
          onClose={() => setModal(null)}
          onDone={added}
        />
      ) : null}
      {modal?.type === "edit" ? (
        <EditDocumentTypeModal
          entry={modal.entry}
          departments={departments.data || []}
          departmentsStatus={departments.status}
          onClose={() => setModal(null)}
          onDone={edited}
          onStale={reloadCatalogue}
        />
      ) : null}
      {modal?.type === "deactivate" ? (
        <ConfirmModal
          title={`Deactivate ${modal.entry.name}`}
          message={`${modal.entry.name} will no longer be offered when adding documents to sales. Documents already on sales, existing obligations, and the automatic generation of documents on existing sales are not affected. You can activate it again at any time.`}
          confirmLabel="Deactivate document type"
          onClose={() => setModal(null)}
          onConfirm={() => setActive(modal.entry, false)}
        />
      ) : null}
      {modal?.type === "activate" ? (
        <ConfirmModal
          title={`Activate ${modal.entry.name}`}
          message={`${modal.entry.name} will be offered again when adding documents to sales.`}
          confirmLabel="Activate document type"
          onClose={() => setModal(null)}
          onConfirm={() => setActive(modal.entry, true)}
        />
      ) : null}
      {modal?.type === "rename" ? (
        <RenameDocumentTypeModal
          entry={modal.entry}
          onClose={() => setModal(null)}
          onDone={(result) => renamed(modal.entry.name, result)}
          onStale={reloadCatalogue}
        />
      ) : null}
    </>
  );
}
