import { Link } from "react-router-dom";
import QueueSummary from "../components/dashboard/QueueSummary.jsx";
import EmptyState from "../components/common/EmptyState.jsx";

export default function StaffHome({ data }) {
  if (data.queue.open === 0) {
    return <EmptyState title="Nothing is assigned to you right now.">Items you own will appear here.</EmptyState>;
  }
  return (
    <div className="stack">
      <QueueSummary queue={data.queue} />
      <p><Link to="/queue" className="btn btn-primary">Open my queue</Link></p>
    </div>
  );
}
