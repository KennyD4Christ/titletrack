import { useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { addSaleDocument } from "../../api/saleDocuments.js";
import { listAllActiveDocumentTypes, listAllDocumentsOfSale } from "../../api/pagedLists.js";
import Modal from "../common/Modal.jsx";
import LoadingState from "../common/LoadingState.jsx";
import ErrorState from "../common/ErrorState.jsx";
import { todayIso } from "../../utils/sales.js";

const OWED_HINT =
  "If this sale already has open work for this document, it will be linked to it. Otherwise new preparation work will be created for it.";
const ISSUED_HINT = "An already issued document creates no preparation work.";
const REFUSED_PREFIX = "Nothing was added: the sale or the catalogue changed while this form was open. ";
const INCOMPLETE_NOTE =
  "Some records could not be loaded, so this list may be incomplete. The server will refuse a document this sale already has.";

/** Add a catalogue document to a sale, still owed or already issued (POST /api/sale-documents/).
 *  The request never names an obligation: the server links the sale's open work for this document or creates
 *  new work, and an already issued document creates none. The picker leaves out types the sale already has and
 *  inactive types, but that is only a convenience: the server enforces every rule and its message is shown.
 *  When the dialog opens it reads the whole active catalogue and all of this sale's documents (every page), and
 *  drops both when it closes, so the picker is never stale. Neither read retries by itself: a failure is shown
 *  at once, and "Try again" is the retry. The issue date starts empty and the form has `noValidate`. */
export default function AddDocumentModal({ saleId, onClose, onDone, onStale }) {
  const types = useQuery({
    queryKey: ["add-document", "types"],
    queryFn: () => listAllActiveDocumentTypes(),
    gcTime: 0,
    retry: false,
  });
  const attached = useQuery({
    queryKey: ["add-document", saleId, "documents"],
    queryFn: () => listAllDocumentsOfSale(saleId),
    gcTime: 0,
    retry: false,
  });
  const [form, setForm] = useState({ document_type: "", state: "owed", issued_on: "", reference: "" });
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState(null);
  const set = (field) => (event) => setForm((f) => ({ ...f, [field]: event.target.value }));

  const taken = new Set(attached.data ? attached.data.results.map((d) => d.document_type) : []);
  const active = types.data ? types.data.results.filter((t) => t.is_active) : [];
  const available = active.filter((t) => !taken.has(t.id)).sort((a, b) => a.name.localeCompare(b.name));
  const incomplete = Boolean(types.data && attached.data)
    && (types.data.count > types.data.results.length || attached.data.count > attached.data.results.length);
  // The chosen type only counts while it is still on offer: a reload can take it away.
  const selected = available.find((t) => String(t.id) === form.document_type);
  const issued = form.state === "issued";
  const valid = Boolean(selected) && (!issued || form.issued_on !== "");

  const payload = { sale: Number(saleId), document_type: selected?.id, state: form.state };
  if (issued) {
    payload.issued_on = form.issued_on;
    if (form.reference.trim()) payload.reference = form.reference.trim();
  }

  const fieldErrors = error?.fieldErrors || {};
  const fieldError = (name) => (fieldErrors[name] ? <span className="field-error" role="alert">{fieldErrors[name][0]}</span> : null);
  // Only the inputs that are on screen can show a message beside them.
  const visibleFields = issued ? ["document_type", "state", "issued_on", "reference"] : ["document_type", "state"];
  const shownBesideField = visibleFields.some((name) => fieldErrors[name]);

  async function submit(event) {
    event.preventDefault();
    if (!valid || busy) return;
    setBusy(true);
    setError(null);
    try {
      const result = await addSaleDocument(payload);
      await onDone(result);
    } catch (err) {
      setError(err);
      // 409 means the sale or the catalogue is no longer as this form assumed (someone else added the document,
      // or deactivated the type): reload the sale's documents (the section's list and this dialog's own read) and
      // the catalogue, so the picker shows the truth.
      if (err.status === 409) {
        if (onStale) onStale();
        types.refetch();
        attached.refetch();
      }
    } finally {
      setBusy(false);
    }
  }

  const failed = [types, attached].filter((q) => q.isError);
  let body;
  if (failed.length > 0) {
    body = <ErrorState error={failed[0].error} onRetry={() => failed.forEach((q) => q.refetch())} />;
  } else if (types.isPending || attached.isPending) {
    body = <LoadingState label="Loading document types…" />;
  } else if (available.length === 0) {
    const reason = active.length === 0
      ? "There are no active document types in the catalogue."
      : "Every active document type is already on this sale.";
    body = (
      <>
        <p className="muted">{`${error ? REFUSED_PREFIX : ""}${reason}`}</p>
        <div className="form-actions">
          <button type="button" className="btn" onClick={onClose}>Cancel</button>
        </div>
      </>
    );
  } else {
    body = (
      <form className="edit-form" onSubmit={submit} noValidate>
        {error && !shownBesideField ? <p className="form-error" role="alert">{error.message}</p> : null}
        <div className="form-grid">
          <label className="wide">
            Document type
            <select value={selected ? String(selected.id) : ""} onChange={set("document_type")}>
              <option value="">Choose a document type…</option>
              {available.map((t) => <option key={t.id} value={t.id}>{t.name}</option>)}
            </select>
            {fieldError("document_type")}
          </label>
          <fieldset className="map-group wide">
            <legend>Status</legend>
            <div className="inline">
              <label className="check">
                <input type="radio" name="document-state" value="owed" checked={!issued} onChange={set("state")} />
                Still owed
              </label>
              <label className="check">
                <input type="radio" name="document-state" value="issued" checked={issued} onChange={set("state")} />
                Already issued
              </label>
            </div>
            {fieldError("state")}
          </fieldset>
          {issued ? (
            <>
              <label>
                Issue date
                <input type="date" value={form.issued_on} max={todayIso()} onChange={set("issued_on")} />
                {fieldError("issued_on")}
              </label>
              <label>
                Reference (optional)
                <input value={form.reference} maxLength={120} onChange={set("reference")} placeholder="e.g. document or certificate number" />
                {fieldError("reference")}
              </label>
            </>
          ) : null}
        </div>
        {issued ? (
          <p>
            <button type="button" className="btn btn-quiet" onClick={() => setForm((f) => ({ ...f, issued_on: todayIso() }))}>
              Use today's date
            </button>
          </p>
        ) : null}
        {incomplete ? <p className="muted small">{INCOMPLETE_NOTE}</p> : null}
        <p className="muted">{issued ? ISSUED_HINT : OWED_HINT}</p>
        <div className="form-actions">
          <button type="submit" className="btn btn-primary" disabled={!valid || busy}>{busy ? "Adding…" : "Add document"}</button>
          <button type="button" className="btn" onClick={onClose}>Cancel</button>
        </div>
      </form>
    );
  }

  return <Modal title="Add a document to this sale" onClose={onClose}>{body}</Modal>;
}
