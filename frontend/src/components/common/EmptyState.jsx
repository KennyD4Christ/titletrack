export default function EmptyState({ title, children }) {
  return (
    <div className="state">
      <strong>{title}</strong>
      {children ? <p className="muted">{children}</p> : null}
    </div>
  );
}
