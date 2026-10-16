import { Link } from "react-router-dom";
import { useAuth } from "../../context/AuthContext.jsx";
import { ROLE_LABELS } from "../../utils/labels.js";

export default function Topbar() {
  const { user, logout } = useAuth();
  return (
    <header className="topbar">
      <div className="topbar-company">{user.company.name}</div>
      <div className="topbar-user">
        <span className="topbar-name">{user.full_name}</span>
        <span className="topbar-role">{ROLE_LABELS[user.role] || user.role}</span>
        <Link className="btn btn-quiet" to="/account/password">Change password</Link>
        <button type="button" className="btn btn-quiet" onClick={() => logout()}>Sign out</button>
      </div>
    </header>
  );
}
