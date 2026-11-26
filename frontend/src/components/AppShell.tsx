import { NavLink, Outlet } from 'react-router';
import { useAuth } from '../auth/AuthProvider';
import { AuthControls } from './AuthControls';

export function AppShell() {
  const { snapshot } = useAuth();
    return (
        <>
            <a href="#main-content">Skip to content</a>
            <header>
                <span>Task Management</span>
                {snapshot.status === 'signed_in' && (
                    <nav aria-label="Main">
                        <NavLink to="/" end>
                            Home
                        </NavLink>
                        <NavLink to="/workspaces">Workspaces</NavLink>
                        <NavLink to="/notifications">Notifications</NavLink>
                    </nav>
                )}
                <AuthControls />
            </header>
            <main id="main-content" tabIndex={-1}>
                <Outlet />
            </main>
        </>
    );
}