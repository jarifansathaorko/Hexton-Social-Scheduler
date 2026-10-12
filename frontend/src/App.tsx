import { Navigate, Route, Routes } from "react-router-dom";
import { Layout } from "./components/Layout";
import { CalendarPage } from "./pages/CalendarPage";
import { ContentPage } from "./pages/ContentPage";
import { DashboardPage } from "./pages/DashboardPage";
import { EditorPage } from "./pages/EditorPage";
import { QueuePage } from "./pages/QueuePage";
import { SettingsPage } from "./pages/SettingsPage";

/**
 * Routes.
 *
 * The editor is mounted at both `/content/new` and `/content/:jobId/edit` so one
 * component serves creation and editing; they differ only in whether an ID is
 * present, which keeps the validation path identical for both and stops the two modes
 * drifting apart.
 */
export function App() {
  return (
    <Routes>
      <Route element={<Layout />}>
        <Route index element={<DashboardPage />} />
        <Route path="content" element={<ContentPage />} />
        <Route path="content/new" element={<EditorPage />} />
        <Route path="content/:jobId/edit" element={<EditorPage />} />
        <Route path="calendar" element={<CalendarPage />} />
        <Route path="queue" element={<QueuePage />} />
        <Route path="settings" element={<SettingsPage />} />
        <Route path="*" element={<Navigate to="/" replace />} />
      </Route>
    </Routes>
  );
}
