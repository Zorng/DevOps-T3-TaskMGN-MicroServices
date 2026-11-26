export function liveness() {
  return { status: 'ok' } as const;
}

export async function readiness(checkDependencies: () => Promise<void>) {
  try {
    await checkDependencies();
    return { code: 200, body: { status: 'ok' } } as const;
  } catch {
    return { code: 503, body: { status: 'unavailable' } } as const;
  }
}
