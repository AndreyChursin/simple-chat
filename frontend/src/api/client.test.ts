import { afterEach, describe, expect, it, vi } from 'vitest';
import { ApiError, apiRequest } from './client';

function mockFetch(status: number, body: unknown): void {
  vi.stubGlobal(
    'fetch',
    vi.fn(async () => new Response(body === undefined ? null : JSON.stringify(body), { status })),
  );
}

afterEach(() => {
  vi.unstubAllGlobals();
});

describe('apiRequest', () => {
  it('returns parsed JSON on success', async () => {
    mockFetch(200, { status: 'ok', db: 'ok' });
    await expect(apiRequest('/health')).resolves.toEqual({ status: 'ok', db: 'ok' });
  });

  it('returns undefined for a success without a body (201 after an action)', async () => {
    mockFetch(201, undefined);
    await expect(apiRequest('/user-bans', { method: 'POST' })).resolves.toBeUndefined();
  });

  it('returns undefined for 204', async () => {
    mockFetch(204, undefined);
    await expect(apiRequest('/auth/logout', { method: 'POST' })).resolves.toBeUndefined();
  });

  it('maps the unified error body to ApiError', async () => {
    mockFetch(413, { error: { code: 'message_too_large', message: 'Too large', details: { max: 3072 } } });
    const err = await apiRequest('/x').catch((e: unknown) => e);
    expect(err).toBeInstanceOf(ApiError);
    expect(err).toMatchObject({ status: 413, code: 'message_too_large', details: { max: 3072 } });
  });

  it('falls back to http_<status> when body is not JSON', async () => {
    vi.stubGlobal('fetch', vi.fn(async () => new Response('oops', { status: 502 })));
    const err = await apiRequest('/x').catch((e: unknown) => e);
    expect(err).toMatchObject({ status: 502, code: 'http_502' });
  });
});
