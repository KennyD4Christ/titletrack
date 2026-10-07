export default function LoadingState({ label = "Loading…" }) {
  return <div className="state" role="status">{label}</div>;
}
