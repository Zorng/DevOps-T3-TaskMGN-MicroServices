import { describe, expect, it } from 'vitest';
import { DEFAULT_API_BASE_URL, readConfig } from './config';

describe('readConfig', () => {
    it('uses the local gateway and an empty client ID when nothing is set', () => {
        expect(readConfig({})).toEqual({ apiBaseUrl: DEFAULT_API_BASE_URL, googleClientId: '' });
    });

    it('strips trailing slashes and whitespace from the gateway URL', () => {
        const config = readConfig({ VITE_API_BASE_URL: '  https://gateway.example.com//  ' });
        expect(config.apiBaseUrl).toBe('https://gateway.example.com');
    });

    it('reads the Google client ID', () => {
        const config = readConfig({ VITE_GOOGLE_CLIENT_ID: ' client-id.apps.googleusercontent.com ' });
        expect(config.googleClientId).toBe('client-id.apps.googleusercontent.com');
    });
});