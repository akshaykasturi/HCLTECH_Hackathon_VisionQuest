import type { ReactNode } from "react";
import { Route, Routes } from "react-router-dom";
import type { Endpoints } from "./lib/api";
import { AppDataProvider } from "./state/AppData";
import { ToastProvider } from "./state/Toasts";
import { AppShell } from "./components/layout/AppShell";
import { HomePage } from "./pages/HomePage";
import { AuditPage } from "./pages/AuditPage";
import { SystemHealthPage } from "./pages/SystemHealthPage";
import { NotFoundPage } from "./pages/NotFoundPage";

export function AppProviders({ children, endpoints }: { children: ReactNode; endpoints?: Endpoints }) {
  return (
    <ToastProvider>
      <AppDataProvider endpoints={endpoints}>{children}</AppDataProvider>
    </ToastProvider>
  );
}

/** Routes. "/system-health" (not "/health") so the page never collides with the backend's /health endpoint. */
export function AppRoutes() {
  return (
    <Routes>
      <Route element={<AppShell />}>
        <Route index element={<HomePage />} />
        <Route path="audit" element={<AuditPage />} />
        <Route path="system-health" element={<SystemHealthPage />} />
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
