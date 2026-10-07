import { useAuth } from "../context/AuthContext.jsx";
import { can } from "../utils/permissions.js";
import ForbiddenPage from "../pages/ForbiddenPage.jsx";

/** Shows the page only if the signed-in role may do `action`. (The API enforces this too.) */
export default function RequireRole({ action, children }) {
  const { user } = useAuth();
  return can(user, action) ? children : <ForbiddenPage />;
}
