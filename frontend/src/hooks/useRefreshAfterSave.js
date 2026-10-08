import { useQueryClient } from "@tanstack/react-query";

/** After any change, reload everything that might show it: lists, the open item, queues and dashboards. */
export function useRefreshAfterSave() {
  const queryClient = useQueryClient();
  return () => Promise.all(["obligations", "obligation", "queue", "dashboard"].map((key) => queryClient.invalidateQueries({ queryKey: [key] })));
}
