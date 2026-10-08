import { useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { Link, useParams } from "react-router-dom";
import { getObligation, getObligationEvents } from "../api/obligations.js";
import { useAuth } from "../context/AuthContext.jsx";
import { can } from "../utils/permissions.js";
import PageHeader from "../components/layout/PageHeader.jsx";
import ObligationForm from "../components/obligations/ObligationForm.jsx";
import { useReferenceData } from "../hooks/useReferenceData.js";
import { useRefreshAfterSave } from "../hooks/useRefreshAfterSave.js";
import LoadingState from "../components/common/LoadingState.jsx";
import ErrorState from "../components/common/ErrorState.jsx";
import StatusBadge from "../components/common/StatusBadge.jsx";
import StaleBadge from "../components/common/StaleBadge.jsx";
import Money from "../components/common/Money.jsx";
import { FIELD_LABELS, statusLabel } from "../utils/labels.js";
import { days, formatDate, formatDateTime } from "../utils/format.js";

function WhyOverdue({ ob }) {
  const t = ob.timeline;
  const late = t.delay_days > 0;
  return (
    <section className="card" aria-labelledby="why-title">
      <h2 id="why-title">{late ? "Why is this overdue?" : "Where this stands"}</h2>
      {ob.is_open ? (
        <p className="why-numbers">
          Expected <strong>{days(t.expected_days)}</strong> · waiting <strong>{days(t.actual_days)}</strong>
          {late ? <> · <span className="text-danger"><strong>{days(t.delay_days)} late</strong></span></> : null}
        </p>
      ) : null}
      <dl className="facts">
        <div><dt>Current stage</dt><dd>{ob.stage || "—"}</dd></div>
        <div><dt>Reason</dt><dd>{ob.reason || "No reason recorded"}</dd></div>
        <div><dt>Owner</dt><dd>{ob.owner_name || "Unassigned"}{ob.department_name ? ` (${ob.department_name})` : ""}</dd></div>
        <div><dt>External party</dt><dd>{ob.external_party || "—"}</dd></div>
        <div><dt>Next action</dt><dd>{ob.next_action || "None set"}</dd></div>
        <div><dt>Next review</dt><dd>{formatDate(ob.next_review_date)}</dd></div>
        <div><dt>Last updated</dt><dd>{formatDate(ob.last_updated_at)} <StaleBadge staleness={ob.staleness} updatedAt={ob.last_updated_at} /></dd></div>
      </dl>
    </section>
  );
}

function History({ events }) {
  return (
    <section className="card" aria-labelledby="history-title">
      <h2 id="history-title">History</h2>
      <ol className="timeline">
        {[...events].reverse().map((e) => (
          <li key={e.id}>
            <div className="timeline-head">
              <strong>{e.actor_name || "System"}</strong>
              <span className="muted small">{formatDateTime(e.created_at)}</span>
            </div>
            {e.note ? <p>{e.note}</p> : null}
            {e.changes_display.length ? (
              <ul className="changes">
                {e.changes_display.map((c) => (
                  <li key={c.field}>
                    {FIELD_LABELS[c.field] || c.field}:{" "}
                    <span className="muted">{c.field === "status" ? statusLabel(c.from) : (c.from ?? "—")}</span> →{" "}
                    <strong>{c.field === "status" ? statusLabel(c.to) : (c.to ?? "—")}</strong>
                  </li>
                ))}
              </ul>
            ) : null}
          </li>
        ))}
      </ol>
    </section>
  );
}

export default function ObligationDetailPage() {
  const { id } = useParams();
  const { user } = useAuth();
  const detail = useQuery({ queryKey: ["obligation", id], queryFn: () => getObligation(id) });
  const showHistory = can(user, "view_history");
  const reference = useReferenceData();
  const refresh = useRefreshAfterSave();
  const [saved, setSaved] = useState(false);
  const events = useQuery({ queryKey: ["obligation", id, "events"], queryFn: () => getObligationEvents(id), enabled: showHistory });

  if (detail.isPending) return <LoadingState />;
  if (detail.isError) return <ErrorState error={detail.error} onRetry={() => detail.refetch()} />;
  const ob = detail.data;
  const sale = ob.sale_summary;

  return (
    <div className="stack">
      <p><Link to="/obligations">← All obligations</Link></p>
      <PageHeader
        title={`${ob.type_name}: ${ob.buyer_name}`}
        subtitle={`${ob.estate_name} · Plot ${ob.plot_number}`}
        actions={<StatusBadge status={ob.status} />}
      />
      {ob.is_cancelled ? (
        <div className="banner banner-warn" role="status">
          This sale was cancelled{sale.cancel_reason ? `: ${sale.cancel_reason}` : ""}. The record is kept for audit and can no longer be edited.
        </div>
      ) : null}
      <WhyOverdue ob={ob} />
      {ob.can_edit ? (
        <section className="card" aria-labelledby="edit-title">
          <h2 id="edit-title">Update this item</h2>
          {saved ? <p className="banner banner-ok" role="status">Saved. The history below shows the change.</p> : null}
          <ObligationForm
            key={ob.last_updated_at}
            obligation={ob}
            users={reference.users}
            onSaved={async () => { setSaved(true); await refresh(); }}
          />
        </section>
      ) : !ob.is_cancelled ? (
        <p className="muted small">You can view this item but not change it.</p>
      ) : null}
      <section className="card" aria-labelledby="sale-title">
        <h2 id="sale-title">The sale</h2>
        <dl className="facts">
          <div><dt>Price</dt><dd><Money value={sale.price} full /></dd></div>
          <div><dt>Paid so far</dt><dd><Money value={sale.amount_paid} full /></dd></div>
          <div><dt>Fully paid on</dt><dd>{formatDate(sale.fully_paid_on)}</dd></div>
          <div><dt>Sale date</dt><dd>{formatDate(sale.sale_date)}</dd></div>
          <div><dt>Buyer phone</dt><dd>{ob.buyer.phone || "—"}</dd></div>
          <div><dt>Buyer email</dt><dd>{ob.buyer.email || "—"}</dd></div>
        </dl>
      </section>
      {showHistory ? (events.isPending ? <LoadingState label="Loading history…" /> : events.isError ? <ErrorState error={events.error} /> : <History events={events.data} />) : null}
    </div>
  );
}
