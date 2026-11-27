import { Route, Routes } from 'react-router';
import { AppShell } from './components/AppShell';
import { RequireAuth } from './components/RequireAuth';
import { HomePage } from './pages/HomePage';
import { NotFoundPage, NotificationsPage } from './pages/PlaceholderPages';
import { WorkspacesPage } from './workspaces/WorkspacesPage';
import { WorkspacePage } from './workspaces/WorkspacePage';
import { SignInPage } from './pages/SignInPage';
import { MyInvitationsPage } from './workspaces/InvitationsPage';
import { BoardPage } from './boards/BoardPage';
import { TaskPage } from './tasks/TaskPage';

export function App() {
    return (
        <Routes>
            <Route element={<AppShell />}>
                <Route path="sign-in" element={<SignInPage />} />
                <Route element={<RequireAuth />}>
                    <Route index element={<HomePage />} />
                    <Route path="workspaces" element={<WorkspacesPage />} />
                    <Route path="workspaces/:workspaceId" element={<WorkspacePage />} />
                    <Route path="workspaces/:workspaceId/boards/:boardId" element={<BoardPage />} />
                    <Route path="workspaces/:workspaceId/tasks/:taskId" element={<TaskPage />} />
                    <Route path="invitations" element={<MyInvitationsPage />} />
                    <Route path="notifications" element={<NotificationsPage />} />
                </Route>
                <Route path="*" element={<NotFoundPage />} />
            </Route>
        </Routes>
    );
}