import { useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { Link, useLocation, useParams } from "react-router-dom";
import { cancelSale, getSale, reinstateSale, reversePayment } from "../api/sales.js";
import { useRefreshAfterSave } from "../hooks/useRefreshAfterSave.js";
import PageHeader from "../components/layout/PageHeader.jsx";
import LoadingState from "../components/common/LoadingState.jsx";
import ErrorState from "../components/common/ErrorState.jsx";
import StatusBadge from "../components/common/StatusBadge.jsx";
import Money from "../components/common/Money.jsx";
import RecordPaymentModal from "../components/sales/RecordPaymentModal.jsx";
import EditBuyerModal from "../components/sales/EditBuyerModal.jsx";
import ReasonModal from "../components/sales/ReasonModal.jsx";
import { formatDate, formatDateTime, formatNaira } from "../utils/format.js";
import {
  PAYMENT_KIND_LABELS, SALE_FIELD_LABELS, SALE_STATUS_LABELS, changeValue,
} from "../utils/sales.js";

const describe = (created) => created.map((o) => `${o.type_name} (due ${formatDate(o.due_date)})`).join(", ");

function paymentNotice(result) {
  if (result.duplicate) return "This payment had already been recorded, so nothing new was added.";
  if (!result.obligations_created.length) return "Payment recorded.";
  return `Payment recorded. The buyer is now fully paid, so these documents are due: ${describe(result.obligations_created)}.`;
}

function reinstateNotice(result) {
  if (!result.obligations_created.length) return "Sale reinstated.";
  return `Sale reinstated. These documents were created: ${describe(result.obligations_created)}.`;
}

export default function SaleDetailPage() {
  const { id } = useParams();
  const location = useLocation();
  const refresh = useRefreshAfterSave();
  const [notice, setNotice] = useState(location.state?.notice || "");
  const [modal, setModal] = useState(null);
  const query = useQuery({ queryKey: ["sale", id], queryFn: () => getSale(id) });

  if (query.isPending) return <LoadingState />;
  if (query.isError) return <ErrorState error={query.error} onRetry={() => query.refetch()} />;

  const sale = query.data;
  const allowed = sale.can;
  const close = () => setModal(null);
  async function finish(message) {
    setModal(null);
    setNotice(message);
    await refresh();
  }

  return (
    <div className="stack">
      <p><Link to="/sales">← All sales</Link></p>
      <PageHeader
        title={sale.buyer_name}
        subtitle={`${sale.estate_name} · Plot ${sale.plot_number}`}
        actions={<span className={`badge ${sale.status === "cancelled" ? "badge-cancelled" : ""}`}>{SALE_STATUS_LABELS[sale.status] || sale.status}</span>}
      />
      {sale.status === "cancelled" ? (
        <div className="banner banner-warn" role="status">
          {`This sale was cancelled${sale.cancel_reason ? `: ${sale.cancel_reason}` : ""}. The record is kept for audit; payments and documents can no longer be changed.`}
        </div>
      ) : null}
      {notice ? <p className="banner banner-ok" role="status">{notice}</p> : null}

      <section className="card" aria-labelledby="money-title">
        <h2 id="money-title">The money</h2>
        <dl className="facts">
          <div><dt>Price</dt><dd><Money value={sale.price} full /></dd></div>
          <div><dt>Paid so far</dt><dd><Money value={sale.amount_paid} full /></dd></div>
          <div><dt>Balance due</dt><dd><Money value={sale.balance_due} full /></dd></div>
          <div><dt>Fully paid on</dt><dd>{formatDate(sale.fully_paid_on)}</dd></div>
          <div><dt>Sale date</dt><dd>{formatDate(sale.sale_date)}</dd></div>
          <div><dt>Buyer</dt><dd>{sale.buyer_detail.full_name}</dd></div>
          <div><dt>Buyer phone</dt><dd>{sale.buyer_detail.phone || "—"}</dd></div>
          <div><dt>Buyer email</dt><dd>{sale.buyer_detail.email || "—"}</dd></div>
        </dl>
        <div className="form-actions">
          {allowed.record_payment ? <button type="button" className="btn btn-primary" onClick={() => setModal({ type: "payment" })}>Record payment</button> : null}
          {allowed.edit_buyer ? <button type="button" className="btn" onClick={() => setModal({ type: "buyer" })}>Edit buyer</button> : null}
          {allowed.cancel ? <button type="button" className="btn btn-danger" onClick={() => setModal({ type: "cancel" })}>Cancel sale</button> : null}
          {allowed.reinstate ? <button type="button" className="btn" onClick={() => setModal({ type: "reinstate" })}>Reinstate sale</button> : null}
        </div>
      </section>

      <section className="card" aria-labelledby="docs-title">
        <h2 id="docs-title">Documents owed</h2>
        {sale.obligations.length === 0 ? (
          <p className="muted">No documents yet. Documents due on full payment appear when the buyer has paid in full.</p>
        ) : (
          <div className="table-wrap">
            <table>
              <thead><tr><th>Document</th><th>Status</th><th>Owner</th><th>Started</th><th>Due</th></tr></thead>
              <tbody>
                {sale.obligations.map((o) => (
                  <tr key={o.id}>
                    <td><Link to={`/obligations/${o.id}`}>{o.type_name}</Link></td>
                    <td><StatusBadge status={o.status} /></td>
                    <td>{o.owner_name || <span className="badge badge-unassigned">Unassigned</span>}</td>
                    <td>{formatDate(o.started_on)}</td>
                    <td>{formatDate(o.due_date)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </section>

      <section className="card" aria-labelledby="payments-title">
        <h2 id="payments-title">Payments</h2>
        {sale.payments.length === 0 ? (
          <p className="muted">No payments recorded yet.</p>
        ) : (
          <div className="table-wrap">
            <table>
              <thead>
                <tr>
                  <th>Date</th><th>Type</th><th className="num">Amount</th><th>Reference</th><th>Note or reason</th>
                  <th>Recorded by</th><th><span className="sr-only">Actions</span></th>
                </tr>
              </thead>
              <tbody>
                {sale.payments.map((p) => (
                  <tr key={p.id}>
                    <td>{formatDate(p.paid_on)}</td>
                    <td>
                      {PAYMENT_KIND_LABELS[p.kind] || p.kind}
                      {p.is_reversed ? <span className="badge badge-cancelled">Reversed</span> : null}
                    </td>
                    <td className="num"><span className={p.kind === "reversal" ? "text-danger" : undefined}><Money value={p.amount} full /></span></td>
                    <td>{p.reference || "—"}</td>
                    <td>{p.note || "—"}</td>
                    <td>{p.recorded_by_name || "System"}</td>
                    <td>
                      {allowed.reverse_payment && p.reversible ? (
                        <button type="button" className="btn" aria-label={`Reverse payment #${p.id}`} onClick={() => setModal({ type: "reverse", payment: p })}>Reverse</button>
                      ) : null}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </section>

      <section className="card" aria-labelledby="history-title">
        <h2 id="history-title">History</h2>
        <ol className="timeline">
          {[...sale.history].reverse().map((e) => (
            <li key={e.id}>
              <div className="timeline-head">
                <strong>{e.action_label}</strong>
                <span className="muted small">{`${e.actor_name || "System"} · ${formatDateTime(e.created_at)}`}</span>
              </div>
              {e.note ? <p>{e.note}</p> : null}
              {e.changes_display.length ? (
                <ul className="changes">
                  {e.changes_display.map((c) => (
                    <li key={c.field}>
                      {SALE_FIELD_LABELS[c.field] || c.field}:{" "}
                      <span className="muted">{changeValue(c.field, c.from)}</span> →{" "}
                      <strong>{changeValue(c.field, c.to)}</strong>
                    </li>
                  ))}
                </ul>
              ) : null}
            </li>
          ))}
        </ol>
      </section>

      {modal?.type === "payment" ? (
        <RecordPaymentModal sale={sale} onClose={close} onDone={(result) => finish(paymentNotice(result))} />
      ) : null}
      {modal?.type === "buyer" ? (
        <EditBuyerModal sale={sale} onClose={close} onDone={() => finish("Buyer details saved.")} />
      ) : null}
      {modal?.type === "reverse" ? (
        <ReasonModal
          title="Reverse this payment"
          intro={`This adds a reversing entry for ${formatNaira(modal.payment.amount)}. The original payment stays in the ledger, and the documents already created are not removed.`}
          label="Reason"
          placeholder="e.g. Cheque bounced"
          submitLabel="Reverse payment"
          danger
          onClose={close}
          onSubmit={async (reason) => {
            await reversePayment(sale.id, modal.payment.id, { reason });
            await finish("Payment reversed.");
          }}
        />
      ) : null}
      {modal?.type === "cancel" ? (
        <ReasonModal
          title="Cancel this sale"
          intro="The sale stays in the history. Its documents leave every queue and the money-still-owed figures, and the plot can be sold again."
          label="Reason for cancelling"
          submitLabel="Cancel sale"
          danger
          onClose={close}
          onSubmit={async (reason) => {
            await cancelSale(sale.id, { reason });
            await finish("Sale cancelled.");
          }}
        />
      ) : null}
      {modal?.type === "reinstate" ? (
        <ReasonModal
          title="Reinstate this sale"
          intro="Use this to undo a mistaken cancellation. It is only possible while the plot has not been sold again."
          label="Note (optional)"
          required={false}
          submitLabel="Reinstate sale"
          onClose={close}
          onSubmit={async (note) => {
            const result = await reinstateSale(sale.id, note ? { note } : {});
            await finish(reinstateNotice(result));
          }}
        />
      ) : null}
    </div>
  );
}
