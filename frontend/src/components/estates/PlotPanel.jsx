import { useQuery } from "@tanstack/react-query";
import { Link } from "react-router-dom";
import { getSale } from "../../api/sales.js";
import Money from "../common/Money.jsx";
import LoadingState from "../common/LoadingState.jsx";
import ErrorState from "../common/ErrorState.jsx";
import PlotStatusBadge from "./PlotStatusBadge.jsx";
import { formatDate } from "../../utils/format.js";
import { statusLabel } from "../../utils/plotBoard.js";

/** What the board knows about one plot, plus (only where the board gave a sale to open) the sale itself, read from
 *  the Sales desk's own endpoint, which enforces its own permission. A role that is not given active_sale_id never
 *  causes that request, so there is nothing to hide: the buyer and the money are simply never asked for. */
function SaleSummary({ saleId }) {
  const sale = useQuery({
    queryKey: ["sale", String(saleId)],           // the same key the Sale page uses, so the two never disagree
    queryFn: () => getSale(saleId),
    staleTime: 0,                                 // money changes elsewhere: ask again every time the panel opens
  });
  if (sale.isPending) return <LoadingState label="Loading the sale…" />;
  if (sale.isError) return <ErrorState error={sale.error} onRetry={() => sale.refetch()} />;
  const s = sale.data;
  return (
    <>
      <h3>The sale</h3>
      <dl className="facts">
        <div><dt>Buyer</dt><dd>{s.buyer_name}</dd></div>
        <div><dt>Phone</dt><dd>{s.buyer_phone || "—"}</dd></div>
        <div><dt>Price</dt><dd><Money value={s.price} full /></dd></div>
        <div><dt>Paid so far</dt><dd><Money value={s.amount_paid} full /></dd></div>
        <div><dt>Balance due</dt><dd><Money value={s.balance_due} full /></dd></div>
        <div><dt>Sale date</dt><dd>{formatDate(s.sale_date)}</dd></div>
        <div><dt>Documents</dt><dd>{`${s.obligations_open} open of ${s.obligations_total}`}</dd></div>
      </dl>
      <p><Link to={`/sales/${saleId}`}>Open the sale</Link></p>
    </>
  );
}

export default function PlotPanel({ estate, plot, canAddSale, canManage, onAddSale, onRename, onRemove, onClose }) {
  const hasSale = plot.active_sale_id !== null && plot.active_sale_id !== undefined;
  return (
    <section className="card plot-panel" aria-labelledby="plot-panel-title">
      <div className="panel-head">
        <h2 id="plot-panel-title">{`Plot ${plot.plot_number}`}</h2>
        <button type="button" className="btn btn-quiet" aria-label="Close plot details" onClick={onClose}>×</button>
      </div>
      <p className="sr-only" role="status">{`Plot ${plot.plot_number}, ${statusLabel(plot.status)}`}</p>
      <p><PlotStatusBadge plot={plot} /></p>
      <dl className="facts">
        <div><dt>Estate</dt><dd>{estate.name}</dd></div>
        <div><dt>Status</dt><dd>{statusLabel(plot.status)}</dd></div>
      </dl>
      {hasSale ? <SaleSummary saleId={plot.active_sale_id} /> : null}
      <div className="form-actions">
        {canAddSale && plot.status === "available" ? (
          <button type="button" className="btn btn-primary" onClick={onAddSale}>Add sale for this plot</button>
        ) : null}
        {canManage ? <button type="button" className="btn" onClick={onRename}>Rename plot</button> : null}
        {canManage ? <button type="button" className="btn btn-danger" onClick={onRemove}>Remove plot</button> : null}
      </div>
    </section>
  );
}
