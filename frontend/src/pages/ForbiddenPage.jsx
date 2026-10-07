import { Link } from "react-router-dom";
import PageHeader from "../components/layout/PageHeader.jsx";

export default function ForbiddenPage() {
  return (
    <>
      <PageHeader title="You do not have access to this page" />
      <p><Link to="/">Back to home</Link></p>
    </>
  );
}
