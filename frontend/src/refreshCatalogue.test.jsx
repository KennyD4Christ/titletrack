import { describe, expect, it } from "vitest";
import { renderHook } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { useRefreshCatalogue } from "./hooks/useRefreshCatalogue.js";

function setup() {
  const client = new QueryClient();
  client.setQueryData(["document-types", "all"], { count: 0, results: [] });
  client.setQueryData(["obligation-types"], []);
  client.setQueryData(["sales"], []);
  const wrapper = ({ children }) => <QueryClientProvider client={client}>{children}</QueryClientProvider>;
  const { result } = renderHook(() => useRefreshCatalogue(), { wrapper });
  return { client, refresh: result.current };
}

const stale = (client, key) => client.getQueryState(key).isInvalidated;

describe("reloading after a catalogue change", () => {
  it("marks the catalogue and the work-type list the Obligations filters use as out of date", async () => {
    const { client, refresh } = setup();
    expect(stale(client, ["document-types", "all"])).toBe(false);
    expect(stale(client, ["obligation-types"])).toBe(false);
    await refresh();
    expect(stale(client, ["document-types", "all"])).toBe(true);
    expect(stale(client, ["obligation-types"])).toBe(true);
  });

  it("leaves unrelated data alone", async () => {
    const { client, refresh } = setup();
    await refresh();
    expect(stale(client, ["sales"])).toBe(false);
  });
});
