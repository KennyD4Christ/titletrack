import { Link } from "react-router-dom";
import { obligationsLink } from "../../utils/links.js";

const TILES = [
  { key: "overdue", label: "Overdue", params: { overdue: 1 }, tone: "danger" },
  { key: "due_soon", label: "Due this week", params: { due_within: 7 }, tone: "warn" },
  { key: "stale", label: "Not updated in 14+ days", params: { stale: 1 }, tone: "warn" },
  { key: "awaiting_external", label: "Waiting on others", params: { status: "awaiting_external" }, tone: "" },
  { key: "incomplete", label: "Need an owner or next action", params: { incomplete: 1 }, tone: "warn" },
  { key: "open", label: "All open", params: { open: 1 }, tone: "" },
];

export default function AttentionPanel({ attention }) {
  return (
    <section className="card" aria-labelledby="attention-title">
      <h2 id="attention-title">What needs attention</h2>
      <div className="tiles">
        {TILES.map((t) => (
          <Link key={t.key} to={obligationsLink(t.params)} className={`tile ${attention[t.key] > 0 ? t.tone : ""}`}>
            <span className="tile-count">{attention[t.key]}</span>
            <span className="tile-label">{t.label}</span>
          </Link>
        ))}
      </div>
    </section>
  );
}
