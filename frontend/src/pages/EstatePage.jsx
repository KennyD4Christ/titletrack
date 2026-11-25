import { useMemo, useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { Link, useLocation, useParams, useSearchParams } from "react-router-dom";
import { getPlotBoard, removePlot } from "../api/inventory.js";
import { useAuth } from "../context/AuthContext.jsx";
import { useRefreshAfterSave } from "../hooks/useRefreshAfterSave.js";
import { can } from "../utils/permissions.js";
import { plural } from "../utils/format.js";
import { filterPlots, hasPaymentDetail, sortPlots, summarise } from "../utils/plotBoard.js";
import PageHeader from "../components/layout/PageHeader.jsx";
import LoadingState from "../components/common/LoadingState.jsx";
import ErrorState from "../components/common/ErrorState.jsx";
import EmptyState from "../components/common/EmptyState.jsx";
import Pagination from "../components/obligations/Pagination.jsx";
import PlotTable from "../components/estates/PlotTable.jsx";
import PlotPanel from "../components/estates/PlotPanel.jsx";
import EstateFormModal from "../components/estates/EstateFormModal.jsx";
import RegisterPlotModal from "../components/estates/RegisterPlotModal.jsx";
import RenamePlotModal from "../components/estates/RenamePlotModal.jsx";
import BulkRegisterModal from "../components/estates/BulkRegisterModal.jsx";
import AddSaleModal from "../components/sales/AddSaleModal.jsx";
import ReasonModal from "../components/sales/ReasonModal.jsx";

const PAGE_SIZE = 100;

export default function EstatePage() {
  const { id } = useParams();
  const location = useLocation();
  const [params, setParams] = useSearchParams();
  const { user } = useAuth();
  const refresh = useRefreshAfterSave();
  const canManage = can(user, "manage_inventory");
  const canAddSale = can(user, "add_sale");
  const [modal, setModal] = useState(null);
  const [notice, setNotice] = useState(location.state?.notice || "");

  // Live, derived data. main.jsx makes every query fresh for 30 seconds; this one must not be: a sale, a payment
  // or a cancellation made a moment ago has to show the next time the board is opened.
  const query = useQuery({ queryKey: ["plot-board", id], queryFn: () => getPlotBoard(id), staleTime: 0 });

  const board = query.data;
  const plots = useMemo(() => (board ? sortPlots(board.plots) : []), [board]);
  const q = params.get("q") || "";
  const status = params.get("status") || "";
  const payment = params.get("payment") || "";
  const page = Math.max(1, Number(params.get("page")) || 1);
  const rows = useMemo(() => filterPlots(plots, { q, status, payment }), [plots, q, status, payment]);
  const selected = plots.find((plot) => String(plot.id) === params.get("plot")) || null;

  function update(patch, { keepPage = false } = {}) {
    const next = new URLSearchParams(params);
    for (const [key, value] of Object.entries(patch)) {
      if (value === "" || value === null || value === undefined) next.delete(key);
      else next.set(key, String(value));
    }
    if (!keepPage) next.delete("page");
    setParams(next, { replace: true });
  }

  if (query.isPending) return <LoadingState />;
  if (query.isError) {
    return (
      <div className="stack">
        <p><Link to="/estates">← All estates</Link></p>
        <ErrorState error={query.error?.status === 404 ? { message: "This estate was not found." } : query.error}
          onRetry={query.error?.status === 404 ? undefined : () => query.refetch()} />
      </div>
    );
  }

  const estate = board.estate;
  const totals = summarise(plots);
  const showPayment = hasPaymentDetail(plots);
  const pageRows = rows.slice((page - 1) * PAGE_SIZE, page * PAGE_SIZE);
  const close = () => setModal(null);

  async function changed(message, nextPlotId) {
    setModal(null);
    setNotice(message);
    if (nextPlotId !== undefined) update({ plot: nextPlotId }, { keepPage: true });
    await refresh();
  }

  return (
    <div className="stack">
      <p><Link to="/estates">← All estates</Link></p>
      <PageHeader
        title={estate.name}
        subtitle={estate.location || "No location recorded"}
        actions={canManage ? (
          <>
            <button type="button" className="btn btn-primary" onClick={() => setModal({ type: "register" })}>Register plot</button>
            <button type="button" className="btn" onClick={() => setModal({ type: "bulk" })}>Register many plots</button>
            <button type="button" className="btn" onClick={() => setModal({ type: "edit" })}>Edit estate</button>
          </>
        ) : null}
      />
      {notice ? <p className="banner banner-ok" role="status">{notice}</p> : null}
      {board.truncated ? (
        <p className="banner banner-warn" role="status">{`This estate has ${board.count} plots. Only the first ${board.plots.length} are shown.`}</p>
      ) : null}

      <section aria-label="Plot totals">
        <div className="tiles">
          <div className="tile static"><span className="tile-count">{totals.total}</span><span className="tile-label">Plots</span></div>
          <div className="tile static ok"><span className="tile-count">{totals.available}</span><span className="tile-label">Available</span></div>
          <div className="tile static"><span className="tile-count">{totals.sold}</span><span className="tile-label">Sold</span></div>
          {showPayment ? (
            <>
              <div className="tile static"><span className="tile-count">{totals.partPaid}</span><span className="tile-label">Part paid</span></div>
              <div className="tile static"><span className="tile-count">{totals.fullyPaid}</span><span className="tile-label">Fully paid</span></div>
            </>
          ) : null}
        </div>
      </section>

      {plots.length === 0 ? (
        <section className="card">
          <EmptyState title="No plots registered yet.">
            {canManage ? "Register this estate's plots one at a time, or many at once." : "Plots appear here once they have been registered."}
          </EmptyState>
        </section>
      ) : (
        <div className="plot-layout">
          <section className="card" aria-label="Plots">
            <form className="filters" role="search" onSubmit={(e) => e.preventDefault()}>
              <label>
                Search plot number
                <input type="search" value={q} onChange={(e) => update({ q: e.target.value })} />
              </label>
              <label>
                Status
                <select value={status} onChange={(e) => update({ status: e.target.value })}>
                  <option value="">All</option>
                  <option value="available">Available</option>
                  <option value="sold">Sold</option>
                </select>
              </label>
              {showPayment ? (
                <label>
                  Payment
                  <select value={payment} onChange={(e) => update({ payment: e.target.value })}>
                    <option value="">All</option>
                    <option value="part_paid">Part paid</option>
                    <option value="fully_paid">Fully paid</option>
                  </select>
                </label>
              ) : null}
              <div className="filter-actions">
                <button type="button" className="btn" onClick={() => update({ q: "", status: "", payment: "" })}>Clear filters</button>
              </div>
            </form>
            {rows.length === 0 ? (
              <EmptyState title="No plots match these filters." />
            ) : (
              <>
                <PlotTable estateName={estate.name} rows={pageRows} selectedId={selected?.id}
                  onSelect={(plotId) => update({ plot: plotId }, { keepPage: true })} />
                <Pagination page={page} pageSize={PAGE_SIZE} count={rows.length} onPage={(p) => update({ page: p }, { keepPage: true })} />
              </>
            )}
          </section>
          {selected ? (
            <PlotPanel
              estate={estate} plot={selected} canAddSale={canAddSale} canManage={canManage}
              onAddSale={() => setModal({ type: "sale" })}
              onRename={() => setModal({ type: "rename" })}
              onRemove={() => setModal({ type: "remove" })}
              onClose={() => update({ plot: "" }, { keepPage: true })}
            />
          ) : (
            <aside className="card muted plot-hint">{`Select a plot to see its details. ${plural(plots.length, "plot")} in this estate.`}</aside>
          )}
        </div>
      )}

      {modal?.type === "register" ? (
        <RegisterPlotModal estate={estate} onClose={close}
          onDone={(plot) => changed(`Plot ${plot.plot_number} registered.`, plot.id)} />
      ) : null}
      {modal?.type === "bulk" ? (
        <BulkRegisterModal estate={estate} onClose={close}
          onDone={(result) => changed(
            `${plural(result.created, "plot")} registered.`
              + (result.skipped_existing.length ? ` ${result.skipped_existing.length} already registered (skipped).` : "")
              + (result.skipped_repeated.length ? ` ${result.skipped_repeated.length} repeated (skipped).` : ""))} />
      ) : null}
      {modal?.type === "edit" ? (
        <EstateFormModal estate={estate} onClose={close} onDone={() => changed("Estate saved.")} />
      ) : null}
      {modal?.type === "rename" && selected ? (
        <RenamePlotModal plot={selected} onClose={close} onDone={(plot) => changed(`Plot renamed to ${plot.plot_number}.`)} />
      ) : null}
      {modal?.type === "remove" && selected ? (
        <ReasonModal
          title={`Remove plot ${selected.plot_number}`}
          intro="Only a plot that never had a sale can be removed, and a cancelled sale counts. The record of the removal is kept."
          label="Reason for removing it"
          placeholder="e.g. Registered twice by mistake"
          submitLabel="Remove plot"
          danger
          onClose={close}
          onSubmit={async (reason) => {
            await removePlot(selected.id, { reason });
            update({ plot: "" }, { keepPage: true });
            await changed(`Plot ${selected.plot_number} removed.`);
          }}
        />
      ) : null}
      {modal?.type === "sale" && selected ? (
        <AddSaleModal
          initial={{ estateId: estate.id, plotNumber: selected.plot_number }}
          onClose={close}
          onCreated={() => changed("Sale added.")}
        />
      ) : null}
    </div>
  );
}
