import { useState } from "react";
import { issueResetLink, issueSetupLink } from "../../api/staff.js";
import { copyText } from "../../utils/clipboard.js";
import { LINK_LIFETIME, errorMessage, formatWhen } from "../../utils/staff.js";
import Modal from "../common/Modal.jsx";

/**
 * Issue a setup or reset link. When the API hands the link back it is shown HERE, once, and lives only in this
 * dialog's memory: closing the dialog discards it, and no other screen or call can show it again.
 */
export default function LinkModal({ account, purpose, initialOutcome = null, onClose }) {
  const [outcome, setOutcome] = useState(initialOutcome);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState(null);
  const [copied, setCopied] = useState("");
  const isSetup = purpose === "setup";
  const noun = isSetup ? "setup link" : "password reset link";
  const lifetime = LINK_LIFETIME[purpose];

  async function issue(reveal) {
    setBusy(true);
    setError(null);
    setCopied("");
    try {
      const issueFn = isSetup ? issueSetupLink : issueResetLink;
      setOutcome(await issueFn(account.id, reveal));
    } catch (err) {
      setError(err);
    } finally {
      setBusy(false);
    }
  }

  async function copy() {
    setCopied((await copyText(outcome.link)) ? "Copied." : "Could not copy. Select the link and copy it by hand.");
  }

  let body;
  if (!outcome) {
    body = (
      <>
        <p>{`Issue a new ${noun} for ${account.full_name}. Any earlier link for this account stops working. The new link works once and expires in ${lifetime}.`}</p>
        <div className="form-actions">
          <button type="button" className="btn btn-primary" disabled={busy} onClick={() => issue(false)}>
            {busy ? "Working…" : `Issue ${noun}`}
          </button>
          <button type="button" className="btn" onClick={onClose}>Cancel</button>
        </div>
      </>
    );
  } else if (outcome.status === "emailed") {
    body = (
      <>
        <p role="status">{`The ${noun} was emailed to ${account.email}. It works once and expires ${formatWhen(outcome.expires_at)}.`}</p>
        <div className="form-actions"><button type="button" className="btn btn-primary" onClick={onClose}>Done</button></div>
      </>
    );
  } else if (outcome.status === "revealed") {
    body = (
      <>
        <p className="banner banner-warn">
          Copy this link now. It is shown only once and is not stored anywhere. Anyone who has it can set the password for this account, so send it only to {account.full_name}.
        </p>
        <label>
          One-time link
          <textarea className="link-box" readOnly rows={4} value={outcome.link} onFocus={(e) => e.target.select()} />
        </label>
        <p className="muted small">{`It works once and expires in ${lifetime} (${formatWhen(outcome.expires_at)}).`}</p>
        {copied ? <p role="status">{copied}</p> : null}
        <div className="form-actions">
          <button type="button" className="btn btn-primary" onClick={copy}>Copy link</button>
          <button type="button" className="btn" onClick={onClose}>Done</button>
        </div>
      </>
    );
  } else {
    body = (
      <>
        <p className="banner banner-warn" role="status">
          The email could not be sent, so nobody has this link yet. You can have a new link shown to you here once instead; that replaces the one that failed.
        </p>
        <div className="form-actions">
          <button type="button" className="btn btn-primary" disabled={busy} onClick={() => issue(true)}>
            {busy ? "Working…" : "Show the link here instead"}
          </button>
          <button type="button" className="btn" onClick={onClose}>Close</button>
        </div>
      </>
    );
  }

  return (
    <Modal title={`${isSetup ? "Setup link" : "Password reset link"} for ${account.full_name}`} onClose={onClose}>
      <div className="edit-form">
        {error ? <p className="form-error" role="alert">{errorMessage(error)}</p> : null}
        {body}
      </div>
    </Modal>
  );
}
