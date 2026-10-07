import { useQuery } from "@tanstack/react-query";
import { getHome } from "../api/dashboard.js";
import { useAuth } from "../context/AuthContext.jsx";
import PageHeader from "../components/layout/PageHeader.jsx";
import LoadingState from "../components/common/LoadingState.jsx";
import ErrorState from "../components/common/ErrorState.jsx";
import MDHome from "./MDHome.jsx";
import ManagerHome from "./ManagerHome.jsx";
import StaffHome from "./StaffHome.jsx";
import SalesHome from "./SalesHome.jsx";
import { formatDate } from "../utils/format.js";

export default function HomePage() {
  const { user } = useAuth();
  const query = useQuery({ queryKey: ["dashboard", "home"], queryFn: getHome });
  const first = (user.full_name || user.username).split(" ")[0];

  let body;
  if (query.isPending) body = <LoadingState />;
  else if (query.isError) body = <ErrorState error={query.error} onRetry={() => query.refetch()} />;
  else {
    const data = query.data;
    // MD and Viewer share the executive layout; the Viewer simply cannot act on anything.
    body = { manager: <ManagerHome data={data} />, staff: <StaffHome data={data} />, sales: <SalesHome /> }[data.role]
      || <MDHome data={data} />;
  }
  const asOf = query.data?.as_of ? `As of ${formatDate(query.data.as_of)}` : undefined;
  return (
    <>
      <PageHeader title={`Welcome, ${first}`} subtitle={asOf} />
      {body}
    </>
  );
}
