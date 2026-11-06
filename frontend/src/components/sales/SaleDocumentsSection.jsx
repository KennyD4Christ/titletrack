import { useState } from "react";
import { useRefreshAfterSave } from "../../hooks/useRefreshAfterSave.js";
import { useAuth } from "../../context/AuthContext.jsx";
import { can } from "../../utils/permissions.js";
import IssueDocumentModal from "./IssueDocumentModal.jsx";
import CorrectIssuanceModal from "./CorrectIssuanceModal.jsx";
import AddDocumentModal from "./AddDocumentModal.jsx";
import ReasonModal from "./ReasonModal.jsx";
import { useQuery } from "@tanstack/react-query";
import { Link } from "react-router-dom";
import { listSaleDocuments, markDocumentNotApplicable } from "../../api/saleDocuments.js";
import LoadingState from "../common/LoadingState.jsx";
import ErrorState from "../common/ErrorState.jsx";
import StatusBadge from "../common/StatusBadge.jsx";
import { formatDate } from "../../utils/format.js";
import {
  OVERDUE_BADGE_CLASS, OVERDUE_LABEL, catalogueNameDiffers, documentBadgeClass, documentStatusLabel,
} from "../../utils/documents.js";

// Display statuses that mean work is still open on an owed document. The API derives them from the
// obligation's status (display_status in api/serializers/sale_documents.py), so no obligation status
// list is repeated here.
const OPEN_DISPLAY = new Set(["outstanding", "in_progress"]);

const NOT_APPLICABLE_INTRO =
  "Use this when the document turns out not to be required for this sale. The reason will be recorded in the sale's history. This action cannot be undone from this page.";
const NOT_APPLICABLE_OPEN_WORK = "Its open preparation work will also be marked as not applicable.";
const NOT_APPLICABLE_DONE_WORK = "Its preparation work is already completed and will remain completed.";

/** What marking this document not applicable will do to its preparation work, from its display status. */
function notApplicableIntro(doc) {
  if (OPEN_DISPLAY.has(doc.display_status)) return `${NOT_APPLICABLE_INTRO} ${NOT_APPLICABLE_OPEN_WORK}`;
  if (doc.display_status === "ready_to_issue") return `${NOT_APPLICABLE_INTRO} ${NOT_APPLICABLE_DONE_WORK}`;
  return NOT_APPLICABLE_INTRO;
}

/** The second line under a document's status: when it was issued, or why it is not required. */
function statusDetail(doc) {
  if (doc.state === "issued") {
    return `Issued ${formatDate(doc.issued_on)}${doc.reference ? ` · ${doc.reference}` : ""}`;
  }
  if (doc.state === "not_applicable" && doc.not_applicable_reason) return `Reason: ${doc.not_applicable_reason}`;
  return "";
}

function Owner({ doc }) {
  if (doc.owner_name) return doc.owner_name;
  if (OPEN_DISPLAY.has(doc.display_status)) return <span className="badge badge-unassigned">Unassigned</span>;
  return "—";
}

/** The documents of one sale, read-only. "Status" is the document itself (the API's display status);
 *  "Preparation" is the status of the internal work (the obligation) behind it. Asking for one sale returns
 *  its documents whatever the sale's status, so a cancelled sale's documents stay visible as the record. */
export default function SaleDocumentsSection({ saleId, cancelled = false }) {
  const refresh = useRefreshAfterSave();
  const { user } = useAuth();
  const [modal, setModal] = useState(null);
  const [notice, setNotice] = useState("");
  const query = useQuery({
    queryKey: ["sale", saleId, "documents"],
    queryFn: () => listSaleDocuments(saleId),
  });
  // The role check uses the shared permission helper; a cancelled sale refuses changes; and the sale's own list
  // must have loaded, so the button never sits above an error. The server enforces all of it anyway.
  const canAdd = query.isSuccess && !cancelled && can(user, "manage_sale_documents");

  // Issuing completes the document's obligation, so reload everything that shows it (this list included:
  // its key starts with "sale"), not just this card.
  async function issued(result) {
    setModal(null);
    setNotice(`${result.name} marked as issued on ${formatDate(result.issued_on)}.`);
    await refresh();
  }

  // Marking a document not applicable closes its open obligation too, so everything that shows it is reloaded.
  // A 409 means the document or sale is no longer in a state that allows this, often because someone else
  // changed it first: the list reloads quietly so it is correct when the dialog is closed, and the error still
  // goes back to the dialog, which shows it.
  async function markNotApplicable(doc, reason) {
    let result;
    try {
      result = await markDocumentNotApplicable(doc.id, { reason });
    } catch (err) {
      if (err.status === 409) query.refetch();
      throw err;
    }
    setModal(null);
    setNotice(`${result.name} marked as not applicable.`);
    await refresh();
  }

  // A correction also adds a history note to the document's obligation, so everything that shows it is reloaded.
  async function corrected(result) {
    setModal(null);
    setNotice(`${result.name} issuance details corrected.`);
    await refresh();
  }

  // A new owed document links the sale's open work or creates new work, and an issued one creates none: reload
  // everything that shows obligations, not just this card.
  async function added(result) {
    setModal(null);
    setNotice(`${result.name} added to this sale.`);
    await refresh();
  }

  let content;
  if (query.isPending) {
    content = <LoadingState label="Loading documents…" />;
  } else if (query.isError) {
    content = <ErrorState error={query.error} onRetry={() => query.refetch()} />;
  } else {
    const { results, count } = query.data;
    content = results.length === 0 ? (
      <p className="muted">No catalogue documents have been added to this sale yet.</p>
    ) : (
      <>
        <p className="muted small">Status is the document itself. Preparation is the internal work towards it.</p>
        <div className="table-wrap">
          <table>
            <thead>
              <tr><th>Document</th><th>Status</th><th>Preparation</th><th>Owner</th><th>Due</th><th><span className="sr-only">Actions</span></th></tr>
            </thead>
            <tbody>
              {results.map((d) => {
                const detail = statusDetail(d);
                return (
                  <tr key={d.id}>
                    <td>
                      {d.name}
                      {catalogueNameDiffers(d) ? (
                        <div className="muted small">{`Now called ${d.catalogue_name} in the catalogue`}</div>
                      ) : null}
                    </td>
                    <td>
                      <span className={documentBadgeClass(d.display_status)}>{documentStatusLabel(d.display_status)}</span>
                      {d.is_overdue ? <span className={OVERDUE_BADGE_CLASS}>{OVERDUE_LABEL}</span> : null}
                      {detail ? <div className="muted small">{detail}</div> : null}
                    </td>
                    <td>
                      {d.obligation ? (
                        <Link to={`/obligations/${d.obligation}`}><StatusBadge status={d.obligation_status} /></Link>
                      ) : "—"}
                    </td>
                    <td><Owner doc={d} /></td>
                    <td className={d.is_overdue ? "text-danger" : undefined}>{formatDate(d.due_date)}</td>
                    <td>
                      <div className="row-actions">
                        {d.can?.issue ? (
                          <button
                            type="button"
                            className="btn btn-small"
                            aria-label={`Issue ${d.name}`}
                            onClick={() => { setNotice(""); setModal({ type: "issue", doc: d }); }}
                          >
                            Issue
                          </button>
                        ) : null}
			{d.can?.mark_not_applicable ? (
                          <button
                            type="button"
                            className="btn btn-small"
                            aria-label={`Mark ${d.name} not applicable`}
                            onClick={() => { setNotice(""); setModal({ type: "not_applicable", doc: d }); }}
                          >
                            Not applicable
                          </button>
                        ) : null}
			{d.can?.correct_issuance ? (
                          <button
                            type="button"
                            className="btn btn-small"
                            aria-label={`Correct issuance of ${d.name}`}
                            onClick={() => { setNotice(""); setModal({ type: "correct", doc: d }); }}
                          >
                            Correct issuance
                          </button>
                        ) : null}
                      </div>
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
        {count > results.length ? (
          <p className="muted small">{`Showing ${results.length} of ${count} documents.`}</p>
        ) : null}
      </>
    );
  }

  return (
    <section className="card" aria-labelledby="sale-docs-title">
      <h2 id="sale-docs-title">Sale documents</h2>
      {cancelled ? (
        <p className="muted small">
          This sale is cancelled. These documents are retained for audit purposes.
        </p>
      ) : null}
      {notice ? <p className="banner banner-ok" role="status">{notice}</p> : null}
      {canAdd ? (
        <p>
          <button
            type="button"
            className="btn btn-primary btn-small"
            onClick={() => { setNotice(""); setModal({ type: "add" }); }}
          >
            Add document
          </button>
        </p>
      ) : null}
      {content}
      {modal?.type === "issue" ? (
        <IssueDocumentModal
          doc={modal.doc}
          completesWork={OPEN_DISPLAY.has(modal.doc.display_status)}
          onClose={() => setModal(null)}
          onDone={issued}
          onStale={() => query.refetch()}
        />
      ) : null}
      {modal?.type === "not_applicable" ? (
        <ReasonModal
          title={`Mark ${modal.doc.name} not applicable`}
          intro={notApplicableIntro(modal.doc)}
          label="Reason"
          placeholder="e.g. Buyer already holds the original"
          submitLabel="Mark not applicable"
          danger
          onClose={() => setModal(null)}
          onSubmit={(reason) => markNotApplicable(modal.doc, reason)}
        />
      ) : null}
      {modal?.type === "correct" ? (
        <CorrectIssuanceModal
          doc={modal.doc}
          onClose={() => setModal(null)}
          onDone={corrected}
          onStale={() => query.refetch()}
        />
      ) : null}
      {modal?.type === "add" ? (
        <AddDocumentModal
          saleId={saleId}
          onClose={() => setModal(null)}
          onDone={added}
          onStale={() => query.refetch()}
        />
      ) : null}
    </section>
  );
}
