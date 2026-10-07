import { useEffect, useState } from "react";
import { STATUS_LABELS } from "../../utils/labels.js";

const ORDERINGS = [
  ["", "Due date (soonest first)"],
  ["started_on", "Waiting longest first"],
  ["-started_on", "Newest first"],
  ["last_updated_at", "Least recently updated"],
  ["-last_updated_at", "Most recently updated"],
];

const FLAGS = [
  ["open", "Open only"],
  ["overdue", "Overdue"],
  ["stale", "Not updated in 14+ days"],
  ["incomplete", "Needs owner or next action"],
];

export default function ObligationFilters({ filters, estates = [], types = [], users = [], onChange, onClear }) {
  const [text, setText] = useState(filters.q || "");
  useEffect(() => setText(filters.q || ""), [filters.q]);

  return (
    <form className="filters" onSubmit={(e) => { e.preventDefault(); onChange({ q: text.trim() }); }} role="search">
      <label>
        Search
        <input type="search" value={text} placeholder="Buyer, plot or external party" onChange={(e) => setText(e.target.value)} />
      </label>
      <label>
        Status
        <select value={filters.status || ""} onChange={(e) => onChange({ status: e.target.value })}>
          <option value="">All</option>
          {Object.entries(STATUS_LABELS).map(([value, label]) => <option key={value} value={value}>{label}</option>)}
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
        Document
        <select value={filters.type || ""} onChange={(e) => onChange({ type: e.target.value })}>
          <option value="">All</option>
          {types.map((t) => <option key={t.id} value={t.id}>{t.name}</option>)}
        </select>
      </label>
      <label>
        Owner
        <select value={filters.owner || ""} onChange={(e) => onChange({ owner: e.target.value })}>
          <option value="">Anyone</option>
          {users.map((u) => <option key={u.id} value={u.id}>{u.full_name}</option>)}
        </select>
      </label>
      <label>
        Sort by
        <select value={filters.ordering || ""} onChange={(e) => onChange({ ordering: e.target.value })}>
          {ORDERINGS.map(([value, label]) => <option key={value} value={value}>{label}</option>)}
        </select>
      </label>
      <fieldset className="flags">
        <legend>Show only</legend>
        {FLAGS.map(([key, label]) => (
          <label key={key} className="check">
            <input type="checkbox" checked={filters[key] === "1"} onChange={(e) => onChange({ [key]: e.target.checked ? "1" : "" })} />
            {label}
          </label>
        ))}
      </fieldset>
      <div className="filter-actions">
        <button type="submit" className="btn btn-primary">Search</button>
        <button type="button" className="btn" onClick={onClear}>Clear filters</button>
      </div>
    </form>
  );
}
