import PlotStatusBadge from "./PlotStatusBadge.jsx";

/** The conventional list of plots. It is also the accessible way to reach every plot: each number is a button. */
export default function PlotTable({ estateName, rows, selectedId, onSelect }) {
  return (
    <div className="table-wrap">
      <table>
        <caption className="sr-only">{`Plots in ${estateName}`}</caption>
        <thead><tr><th>Plot number</th><th>Status</th></tr></thead>
        <tbody>
          {rows.map((plot) => (
            <tr key={plot.id} className={plot.id === selectedId ? "row-selected" : ""}>
              <td>
                <button type="button" className="linklike" aria-pressed={plot.id === selectedId}
                  onClick={() => onSelect(plot.id)}>
                  {plot.plot_number}
                </button>
              </td>
              <td><PlotStatusBadge plot={plot} /></td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}
