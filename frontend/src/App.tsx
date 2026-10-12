import { Navigate, Route, Routes } from "react-router-dom";
import { Layout } from "./components/Layout";
import { CalendarPage } from "./pages/CalendarPage";
import { ChannelsPage } from "./pages/ChannelsPage";
import { DashboardPage } from "./pages/DashboardPage";
import { EditorPage } from "./pages/EditorPage";
import { MediaLibraryPage } from "./pages/MediaLibraryPage";
import { QueuePage } from "./pages/QueuePage";
import { ReportsPage } from "./pages/ReportsPage";
import { SettingsPage } from "./pages/SettingsPage";

/**
 * Routes.
 *
 * The seven sections are the seven navigation entries and nothing else; the editor is
 * mounted at `/new` and `/edit/:jobId` rather than under a section of its own, because
 * it is a place you visit from the primary action and from a row, not a place you
 * browse. One component serves creation and editing so the validation path is
 * identical for both and the two modes cannot drift apart.
 *
 * The legacy `/content` paths are redirected rather than removed outright: a bookmark
 * an operator saved during Phase 2 should keep working, and a redirect is cheaper and
 * more honest than a 404 for a URL this application itself used to hand out.
 */
export function App() {
  return (
    <Routes>
      <Route element={<Layout />}>
        <Route index element={<DashboardPage />} />
        <Route path="calendar" element={<CalendarPage />} />
        <Route path="queue" element={<QueuePage />} />
        <Route path="media" element={<MediaLibraryPage />} />
        <Route path="channels" element={<ChannelsPage />} />
        <Route path="reports" element={<ReportsPage />} />
        <Route path="settings" element={<SettingsPage />} />
        <Route path="new" element={<EditorPage />} />
        <Route path="edit/:jobId" element={<EditorPage />} />

        <Route path="content" element={<Navigate to="/calendar" replace />} />
        <Route path="content/new" element={<Navigate to="/new" replace />} />
        <Route path="content/:jobId/edit" element={<EditRedirect />} />

        <Route path="*" element={<Navigate to="/" replace />} />
      </Route>
    </Routes>
  );
}

/** `/content/12/edit` → `/edit/12`, preserving the ID instead of dropping it. */
function EditRedirect() {
  const match = /^\/content\/(\d+)\/edit/.exec(window.location.pathname);
  const id = match?.[1];
  return <Navigate to={id ? `/edit/${id}` : "/calendar"} replace />;
}
