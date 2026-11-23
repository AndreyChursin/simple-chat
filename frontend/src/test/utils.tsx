import { QueryClientProvider } from '@tanstack/react-query';
import { render } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';
import { vi } from 'vitest';
import App from '../App';
import { createQueryClient } from '../queryClient';

type Handler = (init: RequestInit | undefined) => Response | Promise<Response>;

export const json = (body: unknown, status = 200): Response =>
  new Response(body === undefined ? null : JSON.stringify(body), { status });

export const apiError = (status: number, code: string, message = code): Response =>
  json({ error: { code, message, details: {} } }, status);

/** Stubs fetch with handlers keyed by "METHOD /api/path". Unknown routes fail the test loudly. */
/** Requests every signed-in screen makes (top menu badge, nav sidebar); tests may override them. */
export const DEFAULT_ROUTES: Record<string, Handler> = {
  'GET /api/invitations': () => json({ items: [] }),
  'GET /api/rooms/mine': () => json({ public: [], private: [] }),
  'GET /api/contacts': () => json({ friends: [], incoming: [], outgoing: [], past_dms: [] }),
};

export function mockApi(overrides: Record<string, Handler>) {
  const routes = { ...DEFAULT_ROUTES, ...overrides };
  const calls: { key: string; init: RequestInit | undefined }[] = [];
  const fetchMock = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
    const key = `${(init?.method ?? 'GET').toUpperCase()} ${String(input)}`;
    calls.push({ key, init });
    const handler = routes[key];
    if (!handler) throw new Error(`Unexpected request: ${key}`);
    return handler(init);
  });
  vi.stubGlobal('fetch', fetchMock);
  return calls;
}

export const ME = {
  id: 1,
  email: 'alice@example.com',
  username: 'alice',
  display_name: null,
  created_at: '2026-11-23T10:00:00Z',
};

export function renderApp(path: string) {
  const client = createQueryClient();
  return render(
    <QueryClientProvider client={client}>
      <MemoryRouter initialEntries={[path]} future={{ v7_startTransition: true, v7_relativeSplatPath: true }}>
        <App />
      </MemoryRouter>
    </QueryClientProvider>,
  );
}
