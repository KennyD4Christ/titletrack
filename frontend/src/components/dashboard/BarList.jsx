import { Link } from "react-router-dom";

/** Simple horizontal bars (no chart library needed). Each row can link to a list. */
export default function BarList({ items, label }) {
  const max = Math.max(1, ...items.map((i) => i.value));
  return (
    <ul className="bars" aria-label={label}>
      {items.map((item) => {
        const text = <span className="bar-label">{item.label}</span>;
        return (
          <li key={item.key ?? item.label} className="bar-row">
            {item.href ? <Link to={item.href}>{text}</Link> : text}
            <span className="bar-track" aria-hidden="true">
              <span className={`bar-fill ${item.tone || ""}`} style={{ width: `${(item.value / max) * 100}%` }} />
            </span>
            <span className="bar-value">{item.value}</span>
          </li>
        );
      })}
    </ul>
  );
}
