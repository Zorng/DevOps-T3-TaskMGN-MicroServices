import { Route, Routes } from 'react-router';
import { AppShell } from './components/AppShell';
import { RequireAuth } from './components/RequireAuth';
import { HomePage } from './pages/HomePage';
import { NotFoundPage, NotificationsPage } from './pages/PlaceholderPages';
import { WorkspacesPage } from './workspaces/WorkspacesPage';
import { SignInPage } from './pages/SignInPage';

export function App() {
  return (
    <Routes>
      <Route element={<AppShell />}>
        <Route path="sign-in" element={<SignInPage />} />
        <Route element={<RequireAuth />}>
          <Route index element={<HomePage />} />
          <Route path="workspaces" element={<WorkspacesPage />} />
          <Route path="notifications" element={<NotificationsPage />} />
        </Route>
        <Route path="*" element={<NotFoundPage />} />
      </Route>
    </Routes>
  );
}