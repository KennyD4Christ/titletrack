import { Link } from "react-router-dom";

export default function SalesHome() {
  return (
    <div className="card">
      <h2>Find a buyer's document status</h2>
      <p className="muted">Search by buyer name or plot to see exactly where each document stands.</p>
      <Link to="/obligations" className="btn btn-primary">Search obligations</Link>
    </div>
  );
}
