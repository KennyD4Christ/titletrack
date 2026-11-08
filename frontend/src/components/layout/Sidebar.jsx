import { NavLink } from "react-router-dom";
import { useAuth } from "../../context/AuthContext.jsx";
import { can } from "../../utils/permissions.js";

const ITEMS = [
  { to: "/", label: "Home", action: "view_home", end: true },
  { to: "/queue", label: "My Queue", action: "view_my_queue" },
  { to: "/obligations", label: "Obligations", action: "view_obligations" },
  { to: "/sales", label: "Sales", action: "view_sales" },
  { to: "/import", label: "Import", action: "import_data" },
  { to: "/staff", label: "Staff", action: "manage_users" },
];

// Configuration pages sit under their own heading, apart from the everyday pages above.
const SETTINGS_ITEMS = [
  { to: "/settings/document-catalogue", label: "Document catalogue", action: "manage_document_catalogue" },
];

export default function Sidebar() {
  const { user } = useAuth();
  const settings = SETTINGS_ITEMS.filter((item) => can(user, item.action));
  return (
    <nav className="sidebar" aria-label="Main">
      <div className="brand">TitleTrack</div>
      <ul>
        {ITEMS.filter((item) => can(user, item.action)).map((item) => (
          <li key={item.to}>
            <NavLink to={item.to} end={item.end} className={({ isActive }) => (isActive ? "nav-link active" : "nav-link")}>
              {item.label}
            </NavLink>
          </li>
        ))}
      </ul>
      {settings.length > 0 ? (
        <>
          <p className="nav-heading" id="nav-settings">Settings</p>
          <ul aria-labelledby="nav-settings">
            {settings.map((item) => (
              <li key={item.to}>
                <NavLink to={item.to} className={({ isActive }) => (isActive ? "nav-link active" : "nav-link")}>
                  {item.label}
                </NavLink>
              </li>
            ))}
          </ul>
        </>
      ) : null}
    </nav>
  );
}
