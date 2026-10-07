import { Link } from "react-router-dom";

const TILES = [
  { key: "overdue", label: "Overdue", tone: "danger" },
  { key: "due_soon", label: "Due this week", tone: "warn" },
  { key: "awaiting_external", label: "Waiting on others", tone: "" },
  { key: "other", label: "Everything else", tone: "" },
];

export default function QueueSummary({ queue }) {
  return (
    <section className="card" aria-labelledby="queue-title">
      <h2 id="queue-title">Your work</h2>
      <div className="tiles">
        {TILES.map((t) => (
          <Link key={t.key} to="/queue" className={`tile ${queue[t.key] > 0 ? t.tone : ""}`}>
            <span className="tile-count">{queue[t.key]}</span>
            <span className="tile-label">{t.label}</span>
          </Link>
        ))}
      </div>
    </section>
  );
}
