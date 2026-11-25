import { render } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { MemoryRouter } from "react-router-dom";
import { AuthProvider } from "../context/AuthContext.jsx";
import App from "../App.jsx";
import { fixtures } from "./helpers.jsx";

import boardMd from "./fixtures/plot_board_md.json";
import boardViewer from "./fixtures/plot_board_viewer.json";

export const board = (role) => (role === "md" || role === "manager" ? boardMd : boardViewer);

/** Like helpers.jsx renderApp, but with the query defaults main.jsx really uses (every query fresh for 30 seconds)
 *  and the client handed back. The shared helper's client has staleTime 0, which would hide the very thing the
 *  freshness tests are about. */
export function renderAppLikeMain(route = "/") {
  const client = new QueryClient({
    defaultOptions: { queries: { staleTime: 30_000, refetchOnWindowFocus: false, retry: false } },
  });
  const utils = render(
    <QueryClientProvider client={client}>
      <MemoryRouter initialEntries={[route]}>
        <AuthProvider>
          <App />
        </AuthProvider>
      </MemoryRouter>
    </QueryClientProvider>,
  );
  return { client, ...utils };
}

export const estate = fixtures.estates.results[0];

/** What POST /api/inventory/plots/bulk-preview/ answers: every label new, unless `outcomes` says otherwise. */
export function makePreview(labels, { outcomes = {}, warnings = {}, fingerprint = "f1", cap = false } = {}) {
  const items = labels.map((label, index) => ({
    index, label, key: label.toLowerCase(), outcome: outcomes[label] || "new", existing_plot: null,
    message: outcomes[label] === "exists" ? "Already registered in this estate." : outcomes[label] === "invalid" ? "Plot number contains an invisible character." : "",
    warnings: warnings[label] || [],
  }));
  const count = (o) => items.filter((i) => i.outcome === o).length;
  return {
    estate: { id: estate.id, name: estate.name }, items,
    summary: {
      total: items.length, new: count("new"), exists: count("exists"), duplicate_in_request: count("duplicate_in_request"),
      invalid: count("invalid"), warnings: items.filter((i) => i.warnings.length).length, existing_plots: 4,
      limit: 3000, cap_exceeded: cap,
    },
    can_commit: count("new") > 0 && count("invalid") === 0 && !cap,
    input_fingerprint: `in-${fingerprint}`, preview_fingerprint: `pv-${fingerprint}`,
  };
}
