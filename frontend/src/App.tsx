import { Route, Routes } from 'react-router';
import { AppShell } from './components/AppShell';
import type { AppConfig } from './config';
import { HomePage } from './pages/HomePage';
import { NotFoundPage, NotificationsPage, WorkspacesPage } from './pages/PlaceholderPages';

export function App({ config }: { config: AppConfig }) {
    return (
        <Routes>
            <Route element={<AppShell />}>
                <Route index element={<HomePage config={config} />} />
                <Route path="workspaces" element={<WorkspacesPage />} />
                <Route path="notifications" element={<NotificationsPage />} />
                <Route path="*" element={<NotFoundPage />} />
            </Route>
        </Routes>
    );
}