import BarList from "./BarList.jsx";

export default function DelayReasons({ rows }) {
  if (!rows.length) return null;
  return (
    <section className="card" aria-labelledby="reasons-title">
      <h2 id="reasons-title">Why things are stuck</h2>
      <BarList label="Delay reasons" items={rows.map((r) => ({ key: r.reason, label: r.reason, value: r.count }))} />
    </section>
  );
}
