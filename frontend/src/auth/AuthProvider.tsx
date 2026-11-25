import { createContext, useContext, useSyncExternalStore, type ReactNode } from 'react';
import { createApiClient, type ApiClient } from '../api/client';
import type { AppConfig } from '../config';
import { AuthService } from './authService';
import { loadGoogleIdentity, type GoogleIdentity } from './gis';
import { SessionStore, type SessionSnapshot } from './session';

export interface AuthServices {
    config: AppConfig;
    store: SessionStore;
    client: ApiClient;
    service: AuthService;
    /** Injected so tests can supply a fake Google. */
    loadGoogle: () => Promise<GoogleIdentity>;
}

/** Builds the session store, the gateway client wired to it, and the sign-in service. */
export function createAuthServices(
    config: AppConfig,
    loadGoogle: () => Promise<GoogleIdentity> = loadGoogleIdentity,
): AuthServices {
    const store = new SessionStore();
    const client = createApiClient({
        baseUrl: config.apiBaseUrl,
        getToken: () => store.getToken(),
        onUnauthorized: () => store.signOut(),
    });
    return { config, store, client, service: new AuthService(client, store), loadGoogle };
}

const AuthContext = createContext<AuthServices | null>(null);

export function AuthProvider({ services, children }: { services: AuthServices; children: ReactNode }) {
    return <AuthContext.Provider value={services}>{children}</AuthContext.Provider>;
}

export function useAuth(): AuthServices & { snapshot: SessionSnapshot } {
    const services = useContext(AuthContext);
    if (!services) throw new Error('useAuth must be used inside <AuthProvider>.');
    const snapshot = useSyncExternalStore(services.store.subscribe, services.store.getSnapshot);
    return { ...services, snapshot };
}