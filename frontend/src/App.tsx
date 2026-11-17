import type { ReactNode } from "react";
import { Navigate, Route, Routes } from "react-router-dom";
import type { Endpoints } from "./lib/api";
import { ANALYST_HOME } from "./lib/nav";
import { AppDataProvider } from "./state/AppData";
import { AuthProvider, RequireAuth } from "./state/Auth";
import { ToastProvider } from "./state/Toasts";
import { AppShell } from "./components/layout/AppShell";
import { PublicLayout } from "./components/layout/PublicLayout";
import { HomePage } from "./pages/HomePage";
import { LoginPage } from "./pages/LoginPage";
import { AnalyzePage } from "./pages/AnalyzePage";
import { TicketsPage } from "./pages/TicketsPage";
import { TicketDetailPage } from "./pages/TicketDetailPage";
import { AuditPage } from "./pages/AuditPage";
import { SystemHealthPage } from "./pages/SystemHealthPage";
import { NotFoundPage } from "./pages/NotFoundPage";

export function AppProviders({ children, endpoints }: { children: ReactNode; endpoints?: Endpoints }) {
  return (
    <ToastProvider>
      <AppDataProvider endpoints={endpoints}>
        <AuthProvider>{children}</AuthProvider>
      </AppDataProvider>
    </ToastProvider>
  );
}

/**
 * Two sites in one app:
 *   public (employees): "/", "/login"                 top navigation + footer
 *   analyst console:    "/analyst/*" (signed in only)  sidebar
 * "/analyst/system-health" (not "/health") so the page never collides with the backend's /health endpoint.
 */
export function AppRoutes() {
  return (
    <Routes>
      <Route element={<PublicLayout />}>
        <Route index element={<HomePage />} />
        <Route path="login" element={<LoginPage />} />
      </Route>
      <Route path="analyst" element={<RequireAuth><AppShell /></RequireAuth>}>
        <Route index element={<Navigate to={ANALYST_HOME} replace />} />
        <Route path="analyze" element={<AnalyzePage />} />
        <Route path="tickets" element={<TicketsPage />} />
        <Route path="tickets/:ticketId" element={<TicketDetailPage />} />
        <Route path="audit" element={<AuditPage />} />
        <Route path="system-health" element={<SystemHealthPage />} />
        <Route path="*" element={<NotFoundPage />} />
      </Route>
      <Route element={<PublicLayout />}>
        <Route path="*" element={<NotFoundPage />} />
      </Route>
    </Routes>
  );
}

export default function App() {
  return (
    <AppProviders>
      <AppRoutes />
    </AppProviders>
  );
}
