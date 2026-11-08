import ErrorState from "../common/ErrorState.jsx";
import LoadingState from "../common/LoadingState.jsx";
import { startingChoice } from "../../utils/newSaleDocuments.js";
import { todayIso } from "../../utils/sales.js";

const FIELDS = ["document_type", "state", "issued_on", "reference"];
const INCOMPLETE_NOTE = "Some document types could not be loaded, so this list may be incomplete.";

/** One catalogue document: a tick box, and once ticked its status (still owed or already issued) with, for an
 *  issued one, the issue date and an optional reference. A message from the server about this document is shown
 *  beside the control it concerns. There is deliberately no `max` on the date and the form is not `noValidate`d
 *  here: the server decides what an acceptable issue date is and its message appears beside the date. */
function DocumentChoice({ type, choice, canIssue, errors = {}, onChange }) {
  const beside = (name) => (errors[name]?.[0]
    ? <span className="field-error" role="alert">{errors[name][0]}</span>
    : null);
  const others = Object.entries(errors)
    .filter(([name]) => !FIELDS.includes(name))
    .flatMap(([, value]) => (Array.isArray(value) ? value : [value]))
    .filter((text) => typeof text === "string");
  const issued = canIssue && choice?.state === "issued";
  return (
    <div className="edit-form" role="group" aria-label={type.name}>
      <label className="check">
        <input
          type="checkbox"
          checked={Boolean(choice)}
          onChange={(event) => onChange(type.id, event.target.checked ? startingChoice() : null)}
        />
        {type.name}
      </label>
      {beside("document_type")}
      {choice ? (
        <>
          {canIssue ? (
            <div className="inline" role="radiogroup" aria-label={`Status of ${type.name}`}>
              <label className="check">
                <input
                  type="radio"
                  name={`document-state-${type.id}`}
                  value="owed"
                  checked={!issued}
                  onChange={() => onChange(type.id, { state: "owed" })}
                />
                Still owed
              </label>
              <label className="check">
                <input
                  type="radio"
                  name={`document-state-${type.id}`}
                  value="issued"
                  checked={issued}
                  onChange={() => onChange(type.id, { state: "issued" })}
                />
                Already issued
              </label>
            </div>
          ) : <p className="muted small">This document will be recorded as still owed.</p>}
          {beside("state")}
          {issued ? (
            <>
              <div className="form-grid">
                <label>
                  Issue date
                  <input
                    type="date"
                    value={choice.issued_on}
                    onChange={(event) => onChange(type.id, { issued_on: event.target.value })}
                  />
                  {beside("issued_on")}
                </label>
                <label>
                  Reference (optional)
                  <input
                    value={choice.reference}
                    maxLength={120}
                    placeholder="e.g. document or certificate number"
                    onChange={(event) => onChange(type.id, { reference: event.target.value })}
                  />
                  {beside("reference")}
                </label>
              </div>
              <p>
                <button
                  type="button"
                  className="btn btn-quiet"
                  onClick={() => onChange(type.id, { issued_on: todayIso() })}
                >
                  Use today's date
                </button>
              </p>
            </>
          ) : null}
        </>
      ) : null}
      {others.map((text) => <span key={text} className="field-error" role="alert">{text}</span>)}
    </div>
  );
}

/** The documents to choose from while a sale is added. `catalogue` is the query for the active catalogue and
 *  `types` its entries (active, sorted). `choices` maps a type id to what is ticked; `onChange(typeId, patch)`
 *  merges a patch, or removes the document when the patch is null. `failure` is the server's answer about the
 *  documents of the last attempt (see documentFailure). */
export default function ChooseSaleDocuments({ catalogue, types, choices, canIssue, failure, onChange }) {
  if (catalogue.isError) return <ErrorState error={catalogue.error} onRetry={() => catalogue.refetch()} />;
  if (!catalogue.isSuccess) return <LoadingState label="Loading document types…" />;
  if (types.length === 0) return <p className="muted">There are no active document types in the catalogue.</p>;
  const incomplete = catalogue.data.count > catalogue.data.results.length;
  return (
    <>
      <div className="edit-form">
        {types.map((type) => (
          <DocumentChoice
            key={type.id}
            type={type}
            choice={choices[type.id]}
            canIssue={canIssue}
            errors={failure?.rows[type.id]}
            onChange={onChange}
          />
        ))}
      </div>
      {incomplete ? <p className="muted small">{INCOMPLETE_NOTE}</p> : null}
    </>
  );
}
