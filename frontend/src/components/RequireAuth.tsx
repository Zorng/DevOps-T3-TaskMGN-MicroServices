import { Navigate, Outlet, useLocation } from 'react-router';
import { useAuth } from '../auth/AuthProvider';

/** Wraps routes that need a session. A signed-out visitor is sent to /sign-in and remembered. */
export function RequireAuth() {
    const { snapshot } = useAuth();
    const location = useLocation();
    if (snapshot.status === 'signed_out') {
        return <Navigate to="/sign-in" replace state={{ from: location }} />;
    }
    return <Outlet />;
}