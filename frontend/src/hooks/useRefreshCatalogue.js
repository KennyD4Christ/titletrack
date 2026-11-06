import { useQueryClient } from "@tanstack/react-query";

/** After a change to the catalogue, reload the catalogue and the work-type list that the Obligations filters use:
 *  creating or renaming an entry also creates a work type (an obligation type). */
export function useRefreshCatalogue() {
  const queryClient = useQueryClient();
  return () => Promise.all(
    ["document-types", "obligation-types"].map((key) => queryClient.invalidateQueries({ queryKey: [key] })),
  );
}
