import { useEffect, useState } from "react";

export default function SaleFilters({ filters, estates = [], onChange, onClear }) {
  const [text, setText] = useState(filters.q || "");
  useEffect(() => setText(filters.q || ""), [filters.q]);

  return (
    <form className="filters" onSubmit={(e) => { e.preventDefault(); onChange({ q: text.trim() }); }} role="search">
      <label>
        Search
        <input type="search" value={text} placeholder="Buyer, phone or plot" onChange={(e) => setText(e.target.value)} />
      </label>
      <label>
        Status
        <select value={filters.status || ""} onChange={(e) => onChange({ status: e.target.value })}>
          <option value="">All</option>
          <option value="active">Active</option>
          <option value="cancelled">Cancelled</option>
        </select>
      </label>
      <label>
        Estate
        <select value={filters.estate || ""} onChange={(e) => onChange({ estate: e.target.value })}>
          <option value="">All</option>
          {estates.map((e) => <option key={e.id} value={e.id}>{e.name}</option>)}
        </select>
      </label>
      <label>
        Payment
        <select value={filters.fully_paid || ""} onChange={(e) => onChange({ fully_paid: e.target.value })}>
          <option value="">All</option>
          <option value="1">Fully paid</option>
          <option value="0">Not fully paid</option>
        </select>
      </label>
      <div className="filter-actions">
        <button type="submit" className="btn btn-primary">Search</button>
        <button type="button" className="btn" onClick={onClear}>Clear filters</button>
      </div>
    </form>
  );
}
