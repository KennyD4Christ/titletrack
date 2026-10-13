import { Route, Routes } from "react-router-dom";
import RequireAuth from "./RequireAuth.jsx";
import RequireRole from "./RequireRole.jsx";
import LoginPage from "../pages/LoginPage.jsx";
import HomePage from "../pages/HomePage.jsx";
import ObligationsPage from "../pages/ObligationsPage.jsx";
import ObligationDetailPage from "../pages/ObligationDetailPage.jsx";
import SalesPage from "../pages/SalesPage.jsx";
import SaleDetailPage from "../pages/SaleDetailPage.jsx";
import MyQueuePage from "../pages/MyQueuePage.jsx";
import ImportWizardPage from "../pages/ImportWizardPage.jsx";
import ImportHistoryPage from "../pages/ImportHistoryPage.jsx";
import NotFoundPage from "../pages/NotFoundPage.jsx";

export default function AppRoutes() {
  return (
    <Routes>
      <Route path="/login" element={<LoginPage />} />
      <Route element={<RequireAuth />}>
        <Route index element={<HomePage />} />
        <Route path="obligations" element={<RequireRole action="view_obligations"><ObligationsPage /></RequireRole>} />
        <Route path="obligations/:id" element={<RequireRole action="view_obligations"><ObligationDetailPage /></RequireRole>} />
        <Route path="sales" element={<RequireRole action="view_sales"><SalesPage /></RequireRole>} />
        <Route path="sales/:id" element={<RequireRole action="view_sales"><SaleDetailPage /></RequireRole>} />
        <Route path="import" element={<RequireRole action="import_data"><ImportWizardPage /></RequireRole>} />
        <Route path="import/history" element={<RequireRole action="import_data"><ImportHistoryPage /></RequireRole>} />
        <Route path="queue" element={<RequireRole action="view_my_queue"><MyQueuePage /></RequireRole>} />
        <Route path="*" element={<NotFoundPage />} />
      </Route>
    </Routes>
  );
}
