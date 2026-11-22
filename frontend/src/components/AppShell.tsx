import { NavLink, Outlet } from 'react-router';

export function AppShell() {
    return (
        <>
            <a href="#main-content">Skip to content</a>
            <header>
                <span>Task Management</span>
                <nav aria-label="Main">
                    <NavLink to="/" end>Home</NavLink>
                    <NavLink to="/workspaces">Workspaces</NavLink>
                    <NavLink to="/notifications">Notifications</NavLink>
                </nav>
            </header>
            <main id="main-content" tabIndex={-1}>
                <Outlet />
            </main>
        </>
    );
}