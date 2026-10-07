import { Route, Routes } from "react-router-dom";
import RequireAuth from "./RequireAuth.jsx";
import RequireRole from "./RequireRole.jsx";
import LoginPage from "../pages/LoginPage.jsx";
import HomePage from "../pages/HomePage.jsx";
import ObligationsPage from "../pages/ObligationsPage.jsx";
import ObligationDetailPage from "../pages/ObligationDetailPage.jsx";
import MyQueuePage from "../pages/MyQueuePage.jsx";
import NotFoundPage from "../pages/NotFoundPage.jsx";

export default function AppRoutes() {
  return (
    <Routes>
      <Route path="/login" element={<LoginPage />} />
      <Route element={<RequireAuth />}>
        <Route index element={<HomePage />} />
        <Route path="obligations" element={<RequireRole action="view_obligations"><ObligationsPage /></RequireRole>} />
        <Route path="obligations/:id" element={<RequireRole action="view_obligations"><ObligationDetailPage /></RequireRole>} />
        <Route path="queue" element={<RequireRole action="view_my_queue"><MyQueuePage /></RequireRole>} />
        <Route path="*" element={<NotFoundPage />} />
      </Route>
    </Routes>
  );
}
