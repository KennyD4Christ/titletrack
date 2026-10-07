import { render } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { MemoryRouter } from "react-router-dom";
import { vi } from "vitest";
import { AuthProvider } from "../context/AuthContext.jsx";
import App from "../App.jsx";

import meMd from "./fixtures/me_md.json";
import meManager from "./fixtures/me_manager.json";
import meStaff from "./fixtures/me_staff.json";
import meSales from "./fixtures/me_sales.json";
import meViewer from "./fixtures/me_viewer.json";
import homeMd from "./fixtures/home_md.json";
import homeManager from "./fixtures/home_manager.json";
import homeStaff from "./fixtures/home_staff.json";
import homeSales from "./fixtures/home_sales.json";
import homeViewer from "./fixtures/home_viewer.json";
import obligationsList from "./fixtures/obligations_list.json";
import obligationDetail from "./fixtures/obligation_detail.json";
import obligationEvents from "./fixtures/obligation_events.json";
import estates from "./fixtures/estates.json";
import obligationTypes from "./fixtures/obligation_types.json";
import users from "./fixtures/users.json";
import queue from "./fixtures/queue.json";

export const fixtures = {
  me: { md: meMd, manager: meManager, staff: meStaff, sales: meSales, viewer: meViewer },
  home: { md: homeMd, manager: homeManager, staff: homeStaff, sales: homeSales, viewer: homeViewer },
  obligationsList, obligationDetail, obligationEvents, estates, obligationTypes, users, queue,
};

export const json = (body, status = 200) =>
  new Response(JSON.stringify(body), { status, headers: { "Content-Type": "application/json" } });

/** A fake API. `routes` maps "METHOD /path" (query string ignored) to a body or a function(url, init) -> Response|body. */
export function mockApi(routes) {
  const calls = [];
  const fn = vi.fn(async (url, init = {}) => {
    const method = (init.method || "GET").toUpperCase();
    const path = String(url).split("?")[0];
    calls.push({ method, url: String(url), init });
    const handler = routes[`${method} ${path}`];
    if (handler === undefined) return json({ detail: `No mock for ${method} ${path}` }, 404);
    const result = typeof handler === "function" ? await handler(String(url), init) : handler;
    return result instanceof Response ? result : json(result);
  });
  vi.stubGlobal("fetch", fn);
  return { calls, fn, urls: () => calls.map((c) => c.url) };
}

/** Routes every signed-in screen needs. */
export function baseRoutes(role = "md", extra = {}) {
  return {
    "GET /api/auth/me/": fixtures.me[role],
    "GET /api/dashboard/home/": fixtures.home[role],
    "GET /api/estates/": fixtures.estates,
    "GET /api/obligation-types/": fixtures.obligationTypes,
    "GET /api/users/": fixtures.users,
    "GET /api/obligations/": fixtures.obligationsList,
    "GET /api/me/queue/": fixtures.queue,
    "GET /api/auth/csrf/": { detail: "ok" },
    ...extra,
  };
}

export function renderApp(route = "/") {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={client}>
      <MemoryRouter initialEntries={[route]}>
        <AuthProvider>
          <App />
        </AuthProvider>
      </MemoryRouter>
    </QueryClientProvider>,
  );
}
