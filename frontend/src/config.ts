export interface AppConfig{
    apiBaseUrl: string,
    googleClientId: string,
}

type EnvSource = Partial<Record<'VITE_API_BASE_URL' | 'VITE_GOOGLE_CLIENT_ID', string>>;

export const DEFAULT_API_BASE_URL = 'http://localhost:8080';

export function readConfig(env: EnvSource = import.meta.env): AppConfig{
    const rawBase = env.VITE_API_BASE_URL?.trim() || DEFAULT_API_BASE_URL;
    return {
        apiBaseUrl: rawBase.replace(/\/+$/, ''),
        googleClientId: env.VITE_GOOGLE_CLIENT_ID?.trim() ?? '',
    };
}
