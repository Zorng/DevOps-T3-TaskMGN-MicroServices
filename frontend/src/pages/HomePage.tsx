import type { AppConfig } from '../config';

export function HomePage({ config }: { config: AppConfig }) {
    return (
        <>
            <h1>Task Management</h1>
            <p>Frontend scaffold is running.</p>
            {config.googleClientId === '' && (
                <p role="alert">Google sign-in is not configured: set VITE_GOOGLE_CLIENT_ID.</p>
            )}
        </>
    );
}