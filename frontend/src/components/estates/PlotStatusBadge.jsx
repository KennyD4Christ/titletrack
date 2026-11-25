import { paymentLabel, statusLabel } from "../../utils/plotBoard.js";

/** The status in words (never colour alone), and the payment state where the caller is allowed to see it. */
export default function PlotStatusBadge({ plot }) {
  return (
    <>
      <span className={`badge badge-${plot.status}`}>{statusLabel(plot.status)}</span>
      {plot.payment_state ? <span className={`badge badge-${plot.payment_state}`}>{paymentLabel(plot.payment_state)}</span> : null}
    </>
  );
}
