import { useEffect, useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { Link } from "react-router-dom";
import { createSale, searchBuyers } from "../../api/sales.js";
import Modal from "../common/Modal.jsx";
import { plural } from "../../utils/format.js";
import { toAmount, todayIso } from "../../utils/sales.js";
import { listAllActiveDocumentTypes } from "../../api/pagedLists.js";
import { useAuth } from "../../context/AuthContext.jsx";
import { can } from "../../utils/permissions.js";
import ChooseSaleDocuments from "./ChooseSaleDocuments.jsx";
import SalePlotFields from "./SalePlotFields.jsx";
import {
  AUTOMATIC_NOTE, CHOOSE_HINT, buildDocuments, documentFailure, documentsProblem, sortedActiveTypes,
  startingChoice, withoutRow,
} from "../../utils/newSaleDocuments.js";

/** Add a sale. Everything is checked again by the server, which refuses an occupied plot by name.
 *  `initial` ({estateId, plotNumber}) only fills the form in, for a sale started from a plot on the estate page;
 *  the estate and the plot number can still be changed, and without it the form starts empty as before. */
export default function AddSaleModal({ initial = null, onClose, onCreated }) {
  const [estateChoice, setEstateChoice] = useState(initial?.estateId ? String(initial.estateId) : "");
  const [plot, setPlot] = useState(initial?.plotNumber ?? "");
  const [plotSelectionValid, setPlotSelectionValid] = useState(false);
  const [buyerMode, setBuyerMode] = useState("new");
  const [buyer, setBuyer] = useState(null);
  const [term, setTerm] = useState("");
  const [search, setSearch] = useState("");
  const [form, setForm] = useState({
    buyer_name: "", phone: "", email: "", price: "", amount_paid: "", sale_date: todayIso(), paid_on: "",
  });
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState(null);
  // Choosing the documents of this sale (Phase 8B). Left on "automatic", nothing below changes: no catalogue
  // read, no `documents` in the request, and the server creates the automatic documents as before.
  const { user } = useAuth();
  const canChoose = can(user, "manage_sale_documents");
  const canIssue = can(user, "issue_document");
  const [docMode, setDocMode] = useState("automatic");
  const [choices, setChoices] = useState({});
  const [docFailure, setDocFailure] = useState(null);
  const choosing = canChoose && docMode === "choose";
  const catalogue = useQuery({
    queryKey: ["new-sale", "document-types"],
    queryFn: () => listAllActiveDocumentTypes(),
    enabled: choosing,
    gcTime: 0,
    retry: false,
  });
  const types = catalogue.data ? sortedActiveTypes(catalogue.data.results) : [];
  function changeMode(mode) {
    setDocMode(mode);
    setError(null);
    setDocFailure(null);
  }
  function changeChoice(typeId, patch) {
    setChoices((all) => {
      if (patch === null) {
        const rest = { ...all };
        delete rest[typeId];
        return rest;
      }
      return { ...all, [typeId]: { ...(all[typeId] || startingChoice()), ...patch } };
    });
    setDocFailure((failure) => withoutRow(failure, typeId));     // a changed document no longer shows its old message
  }
  const set = (key) => (event) => setForm((f) => ({ ...f, [key]: event.target.value }));

  useEffect(() => {
    const timer = setTimeout(() => setSearch(term.trim()), 300);
    return () => clearTimeout(timer);
  }, [term]);
  const found = useQuery({
    queryKey: ["buyer-search", search],
    queryFn: () => searchBuyers(search),
    enabled: buyerMode === "existing" && !buyer && search.length >= 2,
  });

  const buyerOk = buyerMode === "existing" ? Boolean(buyer) : form.buyer_name.trim() !== "";
  const valid = plotSelectionValid && buyerOk && form.price.trim() !== "" && form.sale_date !== "";

  const payload = {
    estate: Number(estateChoice),
    plot_number: plot.trim(),
    price: form.price.trim(),
    sale_date: form.sale_date,
  };
  if (buyerMode === "existing") {
    if (buyer) payload.buyer = buyer.id;
  } else {
    payload.buyer_name = form.buyer_name.trim();
    if (form.phone.trim()) payload.phone = form.phone.trim();
    if (form.email.trim()) payload.email = form.email.trim();
  }
  if (form.amount_paid.trim()) {
    payload.amount_paid = form.amount_paid.trim();
    if (form.paid_on) payload.paid_on = form.paid_on;
  }

  // Only a chosen list is sent: leaving `documents` out is how the server is asked for the automatic documents.
  const documents = choosing && catalogue.isSuccess ? buildDocuments(types, choices, canIssue) : [];
  if (documents.length > 0) payload.documents = documents;
  const docsProblem = choosing ? documentsProblem({ status: catalogue.status, types, choices }) : null;

  const paidInFull = !Number.isNaN(toAmount(form.amount_paid)) && toAmount(form.amount_paid) > 0
    && toAmount(form.amount_paid) === toAmount(form.price);
  const fieldErrors = error?.fieldErrors || {};
  const fieldError = (key) => (fieldErrors[key] ? <span className="field-error" role="alert">{fieldErrors[key][0]}</span> : null);

  async function submit(event) {
    event.preventDefault();
    if (!valid || docsProblem) return;
    setBusy(true);
    setError(null);
    setDocFailure(null);
    try {
      const sale = await createSale(payload);
      await onCreated(sale);
    } catch (err) {
      setError(err);
      // Position i of the server's answer is the i-th document that was sent.
      setDocFailure(documentFailure(err, documents.map((d) => d.document_type)));
    } finally {
      setBusy(false);
    }
  }

  return (
    <Modal title="Add a sale" onClose={onClose}>
      <form className="edit-form" onSubmit={submit}>
        {error && !Object.keys(fieldErrors).length && !docFailure ? (
          <div className="form-error" role="alert">
            <p>{error.message}</p>
            {error.data?.existing_sale ? <p><Link to={`/sales/${error.data.existing_sale.id}`}>View that sale</Link></p> : null}
          </div>
        ) : null}
        <div className="form-grid">
          <SalePlotFields
            estateChoice={estateChoice}
            setEstateChoice={setEstateChoice}
            plot={plot}
            setPlot={setPlot}
            fieldError={fieldError}
            onSelectionValidityChange={setPlotSelectionValid}
          />

          <fieldset className="map-group wide">
            <legend>Buyer</legend>
            <div className="inline">
              <label className="check">
                <input type="radio" name="buyer-mode" checked={buyerMode === "new"} onChange={() => setBuyerMode("new")} />
                New buyer
              </label>
              <label className="check">
                <input type="radio" name="buyer-mode" checked={buyerMode === "existing"} onChange={() => setBuyerMode("existing")} />
                Existing buyer
              </label>
            </div>
            {buyerMode === "new" ? (
              <div className="form-grid">
                <label>
                  Buyer name
                  <input value={form.buyer_name} maxLength={200} onChange={set("buyer_name")} />
                </label>
                <label>
                  Phone (optional)
                  <input value={form.phone} maxLength={40} onChange={set("phone")} />
                </label>
                <label>
                  Email (optional)
                  <input type="email" value={form.email} onChange={set("email")} />
                </label>
              </div>
            ) : buyer ? (
              <p>
                {`Chosen buyer: ${buyer.full_name}${buyer.phone ? ` · ${buyer.phone}` : ""}`}{" "}
                <button type="button" className="btn btn-quiet" onClick={() => setBuyer(null)}>Change buyer</button>
              </p>
            ) : (
              <>
                <label>
                  Find a buyer by name, phone or email
                  <input type="search" value={term} onChange={(e) => setTerm(e.target.value)} />
                </label>
                {search.length >= 2 && found.isSuccess ? (
                  found.data.results.length ? (
                    <ul className="chips">
                      {found.data.results.map((b) => (
                        <li key={b.id}>
                          <button type="button" className="btn" onClick={() => setBuyer(b)}>
                            {`${b.full_name} · ${b.phone || "no phone"} · ${plural(b.sales_count, "sale")}`}
                          </button>
                        </li>
                      ))}
                    </ul>
                  ) : <p className="muted small">No buyer matches. Choose "New buyer" instead.</p>
                ) : null}
                {fieldError("buyer")}
              </>
            )}
          </fieldset>

          <label>
            Price
            <input inputMode="decimal" value={form.price} onChange={set("price")} placeholder="e.g. 25000000" />
            {fieldError("price")}
          </label>
          <label>
            Amount paid so far (optional)
            <input inputMode="decimal" value={form.amount_paid} onChange={set("amount_paid")} />
            {fieldError("amount_paid")}
          </label>
          <label>
            Sale date
            <input type="date" value={form.sale_date} onChange={set("sale_date")} />
            {fieldError("sale_date")}
          </label>
          {form.amount_paid.trim() ? (
            <label>
              Payment date (optional)
              <input type="date" value={form.paid_on} onChange={set("paid_on")} />
              {fieldError("paid_on")}
            </label>
          ) : null}
        </div>
        {canChoose ? (
          <fieldset className="map-group">
            <legend>Documents</legend>
            <div className="inline">
              <label className="check">
                <input type="radio" name="document-mode" checked={!choosing} onChange={() => changeMode("automatic")} />
                Use the automatic documents
              </label>
              <label className="check">
                <input type="radio" name="document-mode" checked={choosing} onChange={() => changeMode("choose")} />
                Choose the documents for this sale
              </label>
            </div>
            {choosing ? (
              <>
                <ChooseSaleDocuments
                  catalogue={catalogue}
                  types={types}
                  choices={choices}
                  canIssue={canIssue}
                  failure={docFailure}
                  onChange={changeChoice}
                />
                {docFailure?.general.map((message) => (
                  <p key={message} className="field-error" role="alert">{message}</p>
                ))}
              </>
            ) : <p className="muted small">{AUTOMATIC_NOTE}</p>}
          </fieldset>
        ) : null}
        <ul className="hints">
          {choosing ? <li>{CHOOSE_HINT}</li> : null}
          {!choosing && paidInFull ? <li>Paid in full: the documents due on full payment are created straight away.</li> : null}
          {docsProblem ? <li>{docsProblem}</li> : null}
        </ul>
        <div className="form-actions">
          <button type="submit" className="btn btn-primary" disabled={!valid || Boolean(docsProblem) || busy}>{busy ? "Adding…" : "Add sale"}</button>
          <button type="button" className="btn" onClick={onClose}>Cancel</button>
        </div>
      </form>
    </Modal>
  );
}
