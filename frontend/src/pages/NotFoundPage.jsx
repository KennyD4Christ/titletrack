import { Link } from "react-router-dom";
import PageHeader from "../components/layout/PageHeader.jsx";

export default function NotFoundPage() {
  return (
    <>
      <PageHeader title="Page not found" />
      <p><Link to="/">Back to home</Link></p>
    </>
  );
}
