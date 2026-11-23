import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { render, screen } from '@testing-library/react';
import { afterEach, expect, it, vi } from 'vitest';
import App from './App';

afterEach(() => {
  vi.unstubAllGlobals();
});

function renderApp() {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  render(
    <QueryClientProvider client={qc}>
      <App />
    </QueryClientProvider>,
  );
}

it('shows ok when backend and db are healthy', async () => {
  vi.stubGlobal('fetch', vi.fn(async () => new Response(JSON.stringify({ status: 'ok', db: 'ok' }))));
  renderApp();
  expect(await screen.findByText('ok')).toBeInTheDocument();
});

it('shows unavailable when health check fails', async () => {
  vi.stubGlobal(
    'fetch',
    vi.fn(async () => new Response(JSON.stringify({ status: 'error', db: 'error' }), { status: 503 })),
  );
  renderApp();
  expect(await screen.findByText('unavailable')).toBeInTheDocument();
});
