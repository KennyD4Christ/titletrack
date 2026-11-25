import { useQueryClient } from "@tanstack/react-query";

/** After any change, reload everything that might show it: lists, the open item, queues, dashboards, sales, the
 *  plot boards (a sale, payment, cancellation or reinstatement changes a plot's state) and the estate lists
 *  (a sale or import can create an estate, and the inventory screens add and rename them). */
export function useRefreshAfterSave() {
  const queryClient = useQueryClient();
  return () => Promise.all(
    ["obligations", "obligation", "queue", "dashboard", "sales", "sale", "plot-board", "estates"]
      .map((key) => queryClient.invalidateQueries({ queryKey: [key] })),
  );
}
