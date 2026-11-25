export const GIS_SCRIPT_URL = 'https://accounts.google.com/gsi/client';

export interface CredentialResponse {
  /** The Google ID token (a JWT). */
    credential?: string;
}

export interface GoogleIdConfiguration {
    client_id: string;
    callback: (response: CredentialResponse) => void;
    /** The server-issued nonce from the sign-in challenge. */
    nonce?: string;
    auto_select?: boolean;
}

export interface GoogleButtonOptions {
    type?: 'standard' | 'icon';
    theme?: 'outline' | 'filled_blue' | 'filled_black';
    size?: 'large' | 'medium' | 'small';
    text?: 'signin_with' | 'signin' | 'continue_with';
    shape?: 'rectangular' | 'pill';
    width?: number;
}

export interface GoogleIdentity {
    initialize(config: GoogleIdConfiguration): void;
    renderButton(parent: HTMLElement, options: GoogleButtonOptions): void;
    disableAutoSelect(): void;
    cancel(): void;
}

declare global {
    interface Window {
        google?: { accounts?: { id?: GoogleIdentity } };
    }
}

let pending: Promise<GoogleIdentity> | undefined;

/** Loads the Google script once and resolves with its identity API. Safe to call repeatedly. */
export function loadGoogleIdentity(): Promise<GoogleIdentity> {
    const existing = window.google?.accounts?.id;
    if (existing) return Promise.resolve(existing);
    if (pending) return pending;

    pending = new Promise<GoogleIdentity>((resolve, reject) => {
        const script = document.createElement('script');
        script.src = GIS_SCRIPT_URL;
        script.async = true;
        script.defer = true;
        script.onload = () => {
        pending = undefined;
        const api = window.google?.accounts?.id;
        if (api) resolve(api);
        else reject(new Error('Google Identity Services did not initialize.'));
        };
        script.onerror = () => {
        pending = undefined;
        script.remove();
        reject(new Error('Google Identity Services could not be loaded.'));
        };
        document.head.appendChild(script);
    });
    return pending;
}