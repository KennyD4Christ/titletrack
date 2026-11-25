import { useState } from "react";
import { commitBulk, previewBulk } from "../../api/inventory.js";
import Modal from "../common/Modal.jsx";
import { plural } from "../../utils/format.js";
import {
  BULK_MAX, NOTHING_ENTERED, blocksProblem, buildEntries, newBlock, rangeExample, totalLabels,
} from "../../utils/bulkEntries.js";

const OUTCOME_LABELS = {
  new: "Will be registered",
  exists: "Already registered",
  duplicate_in_request: "Repeated in this list",
  invalid: "Invalid",
};

function BlockEditor({ block, index, only, onChange, onRemove }) {
  const set = (field) => (event) => onChange({ ...block, [field]: event.target.value });
  const example = rangeExample(block);
  return (
    <fieldset className="map-group">
      <legend>{only ? "Plot numbers" : `Block ${index + 1}`}</legend>
      <div className="inline">
        <label className="check">
          <input type="radio" name={`kind-${block.id}`} checked={block.kind === "labels"}
            onChange={() => onChange({ ...newBlock("labels"), id: block.id })} />
          A list of plot numbers
        </label>
        <label className="check">
          <input type="radio" name={`kind-${block.id}`} checked={block.kind === "range"}
            onChange={() => onChange({ ...newBlock("range"), id: block.id })} />
          A range
        </label>
      </div>
      {block.kind === "labels" ? (
        <label>
          One plot number per line
          <textarea rows={5} value={block.text} onChange={set("text")} placeholder={"A-1\nA-2\nA-3"} />
        </label>
      ) : (
        <>
          <div className="form-grid">
            <label>Prefix (optional)<input value={block.prefix} maxLength={50} onChange={set("prefix")} placeholder="A-" /></label>
            <label>From<input inputMode="numeric" value={block.start} onChange={set("start")} /></label>
            <label>To<input inputMode="numeric" value={block.end} onChange={set("end")} /></label>
            <label>Zero-fill to this many digits<input inputMode="numeric" value={block.pad} onChange={set("pad")} /></label>
            <label>Suffix (optional)<input value={block.suffix} maxLength={50} onChange={set("suffix")} /></label>
          </div>
          {example ? <p className="muted small">{`This makes ${example}.`}</p> : null}
        </>
      )}
      {!only ? <button type="button" className="btn btn-quiet" onClick={onRemove}>{`Remove block ${index + 1}`}</button> : null}
    </fieldset>
  );
}

/** Register many plots at once, in two steps: a preview that writes nothing, then a commit that registers exactly
 *  what was previewed, all or none. The commit sends back the two fingerprints the preview returned; if the estate
 *  has changed in between, the server answers 409 with a fresh preview, which replaces this one for you to confirm. */
export default function BulkRegisterModal({ estate, onClose, onDone }) {
  const [blocks, setBlocks] = useState(() => [newBlock("labels")]);
  const [stage, setStage] = useState("input");
  const [preview, setPreview] = useState(null);
  const [previewedEntries, setPreviewedEntries] = useState(null);
  const [stale, setStale] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState(null);

  const problem = blocksProblem(blocks);
  const total = totalLabels(blocks);

  const changeBlock = (id) => (next) => setBlocks((all) => all.map((b) => (b.id === id ? next : b)));
  const removeBlock = (id) => setBlocks((all) => all.filter((b) => b.id !== id));

  async function runPreview(event) {
    event.preventDefault();
    if (problem) return;
    const entries = buildEntries(blocks);
    setBusy(true);
    setError(null);
    try {
      const result = await previewBulk({ estate: estate.id, entries });
      setPreviewedEntries(entries);
      setPreview(result);
      setStale(false);
      setStage("preview");
    } catch (err) {
      setError(err);
    } finally {
      setBusy(false);
    }
  }

  async function runCommit() {
    setBusy(true);
    setError(null);
    try {
      const result = await commitBulk({
        estate: estate.id, entries: previewedEntries,
        input_fingerprint: preview.input_fingerprint, preview_fingerprint: preview.preview_fingerprint,
      });
      await onDone(result);
    } catch (err) {
      if (err.status === 409 && err.data?.preview) {
        setPreview(err.data.preview);              // the estate changed since the preview: confirm the new one
        setStale(true);
      } else {
        setError(err);
      }
    } finally {
      setBusy(false);
    }
  }

  if (stage === "preview" && preview) {
    const s = preview.summary;
    return (
      <Modal title="Register many plots: review" onClose={onClose}>
        <div className="edit-form">
          {stale ? (
            <p className="banner banner-warn" role="status">
              The estate changed since you previewed this, so nothing was registered. This is the new preview: review it and confirm again.
            </p>
          ) : null}
          {error ? <p className="form-error" role="alert">{error.message}</p> : null}
          <p>
            <strong>{plural(s.new, "plot")}</strong>{" will be registered in "}{estate.name}
            {s.exists ? `; ${s.exists} already registered (skipped)` : ""}
            {s.duplicate_in_request ? `; ${s.duplicate_in_request} repeated in your list (skipped)` : ""}
            {s.invalid ? `; ${s.invalid} invalid` : ""}.
          </p>
          {s.invalid ? <p className="form-error" role="alert">Fix the invalid plot numbers before registering. Nothing is registered while any is invalid.</p> : null}
          {s.cap_exceeded ? (
            <p className="form-error" role="alert">{`An estate can hold at most ${s.limit} registered plots. This one has ${s.existing_plots}.`}</p>
          ) : null}
          {s.warnings ? <p className="banner banner-warn">{plural(s.warnings, "plot number")} may be the same plot written another way. Check the notes below.</p> : null}
          <div className="table-wrap scroll-box">
            <table>
              <caption className="sr-only">What registering each plot number would do</caption>
              <thead><tr><th>#</th><th>Plot number</th><th>Result</th><th>Note</th></tr></thead>
              <tbody>
                {preview.items.map((item) => (
                  <tr key={item.index}>
                    <td className="num">{item.index + 1}</td>
                    <td>{item.label || "(blank)"}</td>
                    <td><span className={`badge badge-outcome-${item.outcome}`}>{OUTCOME_LABELS[item.outcome] || item.outcome}</span></td>
                    <td>
                      {item.message ? <div className="muted small">{item.message}</div> : null}
                      {item.warnings.map((w) => <div key={w} className="small text-warn">{w}</div>)}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
          <div className="form-actions">
            <button type="button" className="btn btn-primary" disabled={!preview.can_commit || busy} onClick={runCommit}>
              {busy ? "Registering…" : `Register ${plural(s.new, "plot")}`}
            </button>
            <button type="button" className="btn" disabled={busy} onClick={() => { setStage("input"); setPreview(null); setStale(false); setError(null); }}>
              Back and edit
            </button>
            <button type="button" className="btn btn-quiet" onClick={onClose}>Cancel</button>
          </div>
        </div>
      </Modal>
    );
  }

  return (
    <Modal title="Register many plots" onClose={onClose}>
      <form className="edit-form" onSubmit={runPreview}>
        <p className="muted">{`Enter plot numbers or ranges for ${estate.name}. You will see exactly what will happen before anything is registered.`}</p>
        {error ? <p className="form-error" role="alert">{error.message}</p> : null}
        {blocks.map((block, index) => (
          <BlockEditor key={block.id} block={block} index={index} only={blocks.length === 1}
            onChange={changeBlock(block.id)} onRemove={() => removeBlock(block.id)} />
        ))}
        <p>
          <button type="button" className="btn" onClick={() => setBlocks((all) => [...all, newBlock("range")])}>
            Add another block
          </button>
        </p>
        <p className="muted small">{`${plural(total, "plot")} entered (the most in one request is ${BULK_MAX}).`}</p>
        {problem && problem !== NOTHING_ENTERED ? <p className="hints">{problem}</p> : null}
        <div className="form-actions">
          <button type="submit" className="btn btn-primary" disabled={Boolean(problem) || busy}>{busy ? "Checking…" : "Preview"}</button>
          <button type="button" className="btn" onClick={onClose}>Cancel</button>
        </div>
      </form>
    </Modal>
  );
}
