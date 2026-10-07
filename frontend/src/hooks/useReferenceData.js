import { useQuery } from "@tanstack/react-query";
import { listEstates, listObligationTypes, listUsers } from "../api/obligations.js";

const LONG = 5 * 60_000;

export function useReferenceData() {
  const estates = useQuery({ queryKey: ["estates"], queryFn: listEstates, staleTime: LONG });
  const types = useQuery({ queryKey: ["obligation-types"], queryFn: listObligationTypes, staleTime: LONG });
  const users = useQuery({ queryKey: ["users"], queryFn: listUsers, staleTime: LONG });
  return { estates: estates.data || [], types: types.data || [], users: users.data || [] };
}
