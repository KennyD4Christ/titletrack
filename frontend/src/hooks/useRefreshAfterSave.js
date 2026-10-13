import { useQueryClient } from "@tanstack/react-query";

/** After any change, reload everything that might show it: lists, the open item, queues, dashboards and sales. */
export function useRefreshAfterSave() {
  const queryClient = useQueryClient();
  return () => Promise.all(
    ["obligations", "obligation", "queue", "dashboard", "sales", "sale"]
      .map((key) => queryClient.invalidateQueries({ queryKey: [key] })),
  );
}
