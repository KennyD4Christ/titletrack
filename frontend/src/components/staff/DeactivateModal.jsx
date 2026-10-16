import { useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { Link } from "react-router-dom";
import { deactivateStaff, getOpenObligations } from "../../api/staff.js";
import { obligationsLink } from "../../utils/links.js";
import { plural } from "../../utils/staff.js";
import Modal from "../common/Modal.jsx";

/** Shows how many open obligations the person owns BEFORE deactivating. Nothing is reassigned automatically. */
export default function DeactivateModal({ account, onClose, onDone }) {
  const [reason, setReason] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState(null);
  const count = useQuery({
    queryKey: ["staff", "open-obligations", account.id],
    queryFn: () => getOpenObligations(account.id),
    staleTime: 0,
    gcTime: 0,
  });
  const owned = count.data?.count ?? 0;

  async function submit(event) {
    event.preventDefault();
    setBusy(true);
    setError(null);
    try {
      await onDone(await deactivateStaff(account.id, reason.trim()));
    } catch (err) {
      setError(err);
    } finally {
      setBusy(false);
    }
  }

  return (
    <Modal title={`Deactivate ${account.full_name}`} onClose={onClose}>
      <form className="edit-form" onSubmit={submit}>
        <p>{`${account.full_name} will no longer be able to sign in, and any setup or reset link for them stops working. Their history is kept.`}</p>
        {count.isPending ? <p role="status">Checking their open obligations…</p> : null}
        {count.isError ? (
          <p className="form-error" role="alert">
            {`Could not check their open obligations: ${count.error.message}`}{" "}
            <button type="button" className="btn" onClick={() => count.refetch()}>Try again</button>
          </p>
        ) : null}
        {count.isSuccess && owned > 0 ? (
          <div className="banner banner-warn" role="status">
            <strong>{`${plural(owned, "open obligation")} ${owned === 1 ? "is" : "are"} assigned to ${account.full_name}.`}</strong>
            {" "}Deactivating does not reassign them: they stay assigned to this person until someone reassigns them.{" "}
            <Link to={obligationsLink({ owner: account.id, open: true })}>See their open obligations</Link>
            {" "}(select them there and use Update selected to give them to someone else).
          </div>
        ) : null}
        {count.isSuccess && owned === 0 ? <p className="muted" role="status">They have no open obligations.</p> : null}
        {error ? <p className="form-error" role="alert">{error.message}</p> : null}
        <label>
          Reason (optional)
          <textarea rows={3} maxLength={500} value={reason} onChange={(e) => setReason(e.target.value)} />
        </label>
        <div className="form-actions">
          <button type="submit" className="btn btn-danger" disabled={busy || !count.isSuccess}>
            {busy ? "Working…" : "Deactivate account"}
          </button>
          <button type="button" className="btn" onClick={onClose}>Not now</button>
        </div>
      </form>
    </Modal>
  );
}
