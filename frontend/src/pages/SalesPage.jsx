import { useState } from "react";
import { keepPreviousData, useQuery } from "@tanstack/react-query";
import { Link, useNavigate, useSearchParams } from "react-router-dom";
import { listSales } from "../api/sales.js";
import { useReferenceData } from "../hooks/useReferenceData.js";
import { useAuth } from "../context/AuthContext.jsx";
import { can } from "../utils/permissions.js";
import { formatDate } from "../utils/format.js";
import PageHeader from "../components/layout/PageHeader.jsx";
import SaleFilters from "../components/sales/SaleFilters.jsx";
import AddSaleModal from "../components/sales/AddSaleModal.jsx";
import Pagination from "../components/obligations/Pagination.jsx";
import Money from "../components/common/Money.jsx";
import LoadingState from "../components/common/LoadingState.jsx";
import ErrorState from "../components/common/ErrorState.jsx";
import EmptyState from "../components/common/EmptyState.jsx";

const PAGE_SIZE = 25;
const FILTER_KEYS = ["q", "status", "estate", "fully_paid", "page"];

function SaleTable({ rows }) {
  return (
    <div className="table-wrap">
      <table>
        <thead>
          <tr>
            <th>Buyer</th><th>Estate and plot</th><th className="num">Price</th><th className="num">Paid</th>
            <th className="num">Balance due</th><th>Documents</th><th>Sale date</th><th>Status</th>
          </tr>
        </thead>
        <tbody>
          {rows.map((r) => (
            <tr key={r.id} className={r.status === "cancelled" ? "row-cancelled" : ""}>
              <td>
                <Link to={`/sales/${r.id}`}>{r.buyer_name}</Link>
                <div className="muted small">{r.buyer_phone || "No phone"}</div>
              </td>
              <td>
                {r.estate_name}
                <div className="muted small">{`Plot ${r.plot_number}`}</div>
              </td>
              <td className="num"><Money value={r.price} full /></td>
              <td className="num"><Money value={r.amount_paid} full /></td>
              <td className="num"><Money value={r.balance_due} full /></td>
              <td>{`${r.obligations_open} open of ${r.obligations_total}`}</td>
              <td>{formatDate(r.sale_date)}</td>
              <td>
                {r.status === "cancelled"
                  ? <span className="badge badge-cancelled">Cancelled</span>
                  : <span className="badge">Active</span>}
                {r.is_fully_paid && r.status !== "cancelled" ? <span className="badge badge-completed">Fully paid</span> : null}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

export default function SalesPage() {
  const [params, setParams] = useSearchParams();
  const filters = Object.fromEntries([...params].filter(([k]) => FILTER_KEYS.includes(k)));
  const page = Math.max(1, Number(filters.page) || 1);
  const reference = useReferenceData();
  const { user } = useAuth();
  const navigate = useNavigate();
  const [adding, setAdding] = useState(false);

  const query = useQuery({
    queryKey: ["sales", filters],
    queryFn: () => listSales({ ...filters, page, page_size: PAGE_SIZE }),
    placeholderData: keepPreviousData,
  });

  function update(patch, { keepPage = false } = {}) {
    const next = new URLSearchParams(params);
    for (const [key, value] of Object.entries(patch)) {
      if (value === "" || value === null || value === undefined) next.delete(key);
      else next.set(key, String(value));
    }
    if (!keepPage) next.delete("page");
    setParams(next);
  }

  let body;
  if (query.isPending) body = <LoadingState />;
  else if (query.isError) body = <ErrorState error={query.error} onRetry={() => query.refetch()} />;
  else if (query.data.count === 0) body = <EmptyState title="No sales match these filters.">Try clearing a filter, or add a sale.</EmptyState>;
  else {
    body = (
      <>
        <SaleTable rows={query.data.results} />
        <Pagination page={page} pageSize={PAGE_SIZE} count={query.data.count} onPage={(p) => update({ page: p }, { keepPage: true })} />
      </>
    );
  }

  return (
    <>
      <PageHeader
        title="Sales"
        subtitle="Who bought what, what they have paid, and which documents they are owed."
        actions={can(user, "add_sale")
          ? <button type="button" className="btn btn-primary" onClick={() => setAdding(true)}>Add sale</button>
          : null}
      />
      <SaleFilters filters={filters} estates={reference.estates} onChange={update} onClear={() => setParams(new URLSearchParams())} />
      <section className="card">{body}</section>
      {adding ? (
        <AddSaleModal
          estates={reference.estates}
          onClose={() => setAdding(false)}
          onCreated={(sale) => navigate(`/sales/${sale.id}`, { state: { notice: "Sale added." } })}
        />
      ) : null}
    </>
  );
}
