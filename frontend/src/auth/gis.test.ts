import { afterEach, describe, expect, it } from 'vitest';
import { GIS_SCRIPT_URL, loadGoogleIdentity, type GoogleIdentity } from './gis';

function fakeIdentity(): GoogleIdentity {
    return {
        initialize: () => undefined,
        renderButton: () => undefined,
        disableAutoSelect: () => undefined,
        cancel: () => undefined,
    };
}

function gisScripts() {
    return Array.from(document.head.querySelectorAll<HTMLScriptElement>(`script[src="${GIS_SCRIPT_URL}"]`));
}

afterEach(() => {
    delete window.google;
    for (const script of gisScripts()) script.remove();
});

describe('loadGoogleIdentity', () => {
    it('resolves immediately when Google is already loaded, without adding a script', async () => {
        const api = fakeIdentity();
        window.google = { accounts: { id: api } };
        await expect(loadGoogleIdentity()).resolves.toBe(api);
        expect(gisScripts()).toHaveLength(0);
    });

    it('adds the script once for concurrent calls and resolves when it loads', async () => {
        const first = loadGoogleIdentity();
        const second = loadGoogleIdentity();
        expect(gisScripts()).toHaveLength(1);

        const api = fakeIdentity();
        window.google = { accounts: { id: api } };
        gisScripts()[0]?.onload?.(new Event('load'));

        await expect(first).resolves.toBe(api);
        await expect(second).resolves.toBe(api);
    });

    it('rejects when the script fails to load, and allows a fresh attempt', async () => {
        const failed = loadGoogleIdentity();
        gisScripts()[0]?.onerror?.(new Event('error'));
        await expect(failed).rejects.toThrow('could not be loaded');

        const retry = loadGoogleIdentity();
        expect(gisScripts()).toHaveLength(1);
        const api = fakeIdentity();
        window.google = { accounts: { id: api } };
        gisScripts()[0]?.onload?.(new Event('load'));
        await expect(retry).resolves.toBe(api);
    });

    it('rejects when the script loads but the API is missing', async () => {
        const result = loadGoogleIdentity();
        gisScripts()[0]?.onload?.(new Event('load'));
        await expect(result).rejects.toThrow('did not initialize');
    });
});