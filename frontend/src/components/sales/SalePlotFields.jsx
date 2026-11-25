
import { useEffect } from "react";
import { useQuery } from "@tanstack/react-query";
import { Link } from "react-router-dom";
import { listAllEstates } from "../../api/pagedLists.js";
import { getPlotBoard } from "../../api/inventory.js";

/**
 * Estate and registered-plot selection for the Sales Desk.
 *
 * The estate list is paginated. A plot is selectable only when the live board
 * reports its status as "available". The backend independently enforces this.
 */
export default function SalePlotFields({
  estateChoice,
  setEstateChoice,
  plot,
  setPlot,
  fieldError,
  onSelectionValidityChange,
}) {
  const estatesQuery = useQuery({
    queryKey: ["new-sale", "estates"],
    queryFn: listAllEstates,
    retry: false,
  });

  const boardQuery = useQuery({
    queryKey: ["plot-board", estateChoice],
    queryFn: () => getPlotBoard(estateChoice),
    enabled: Boolean(estateChoice),
    staleTime: 0,
    retry: false,
  });

  const board = boardQuery.data;
  const estateOptions = estatesQuery.data?.results || [];
  const boardReady = boardQuery.isSuccess && !boardQuery.isFetching;
  const availablePlots = (board?.plots || [])
    .filter((item) => item.status === "available")
    .slice()
    .sort((a, b) =>
      String(a.plot_number).localeCompare(String(b.plot_number), undefined, {
        numeric: true,
        sensitivity: "base",
      }),
    );

  // A contextual plot is only a suggestion. Clear it if the live board
  // confirms that it is no longer available.
  useEffect(() => {
    const estateListComplete = estatesQuery.isSuccess
      && estatesQuery.data.count === estatesQuery.data.results.length;

    const estateExists = estateListComplete
      && estateOptions.some((item) => String(item.id) === estateChoice);

    const plotIsAvailable = boardReady
      && Boolean(plot)
      && availablePlots.some((item) => item.plot_number === plot);

    onSelectionValidityChange(Boolean(estateExists && plotIsAvailable));

    if (boardReady && plot && !plotIsAvailable) {
      setPlot("");
    }
  }, [
    estatesQuery.isSuccess,
    estatesQuery.data,
    estateOptions,
    estateChoice,
    boardReady,
    plot,
    availablePlots,
    setPlot,
    onSelectionValidityChange,
  ]);

  function changeEstate(event) {
    setEstateChoice(event.target.value);
    setPlot("");
    onSelectionValidityChange(false);
  }

  const estateListIncomplete = estatesQuery.isSuccess
    && estatesQuery.data.count > estatesQuery.data.results.length;

  let boardMessage = null;

  if (estateChoice && boardQuery.isPending) {
    boardMessage = <p className="muted small">Loading registered plots…</p>;
  } else if (estateChoice && boardQuery.isError) {
    boardMessage = (
      <p className="field-error" role="alert">
        Could not load this estate's plot board. Please try again.
        {" "}
        <button type="button" className="btn btn-quiet" onClick={() => boardQuery.refetch()}>
          Retry
        </button>
      </p>
    );
  } else if (boardReady && board) {
    if (availablePlots.length > 0) {
      boardMessage = board.truncated ? (
        <p className="banner banner-warn" role="status">
          Only part of this estate's plot register is shown. Select from the
          available plots listed here; the list may be incomplete.
        </p>
      ) : null;
    } else if (board.truncated) {
      boardMessage = (
        <p className="banner banner-warn" role="status">
          This estate's plot register is too large to display in full, and the
          returned portion contains no available plots. Check the Estates page
          to review the complete register.
          {" "}
          <Link to="/estates">Open Estates</Link>
        </p>
      );
    } else if (board.count === 0) {
      boardMessage = (
        <p className="muted small">
          No plots have been registered for this estate yet.
          {" "}
          <Link to="/estates">Register plots in Estates</Link>
          {" "}before recording a sale.
        </p>
      );
    } else {
      boardMessage = (
        <p className="muted small">
          This estate has registered plots, but none are currently available.
          {" "}
          <Link to="/estates">Review plots in Estates</Link>.
        </p>
      );
    }
  }

  return (
    <>
      <label>
        Estate
        <select
          value={estateChoice}
          onChange={changeEstate}
          disabled={estatesQuery.isPending || estatesQuery.isError}
        >
          <option value="">Choose an estate…</option>
          {estateOptions.map((estate) => (
            <option key={estate.id} value={String(estate.id)}>
              {estate.name}
            </option>
          ))}
        </select>
        {fieldError("estate")}
        {estatesQuery.isPending ? (
          <span className="muted small">Loading estates…</span>
        ) : null}
        {estatesQuery.isError ? (
          <span className="field-error" role="alert">
            Could not load estates.
            {" "}
            <button
              type="button"
              className="btn btn-quiet"
              onClick={() => estatesQuery.refetch()}
            >
              Retry
            </button>
          </span>
        ) : null}
        {estateListIncomplete ? (
          <span className="field-error" role="alert">
            The estate list could not be loaded completely. Refresh before
            recording a sale.
          </span>
        ) : null}
      </label>

      {estateChoice ? (
        <label>
          Registered available plot
          <select
            value={plot}
            onChange={(event) => setPlot(event.target.value)}
            disabled={!boardReady || availablePlots.length === 0}
          >
            <option value="">Choose an available plot…</option>
            {availablePlots.map((item) => (
              <option key={item.id} value={item.plot_number}>
                {item.plot_number}
              </option>
            ))}
          </select>
          {fieldError("plot_number")}
          {boardMessage}
        </label>
      ) : (
        <p className="muted small">
          Choose an existing estate to see its registered available plots.
        </p>
      )}
    </>
  );
}
