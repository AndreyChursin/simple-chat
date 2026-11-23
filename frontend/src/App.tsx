import { useQuery } from '@tanstack/react-query';
import { getHealth } from './api/client';

/** Stage 1 placeholder: proves SPA → nginx → backend → PostgreSQL wiring. Replaced in stage 2. */
export default function App() {
  const health = useQuery({ queryKey: ['health'], queryFn: getHealth, retry: false });

  let status: string;
  if (health.isPending) status = 'checking…';
  else if (health.isError) status = 'unavailable';
  else status = health.data.status === 'ok' && health.data.db === 'ok' ? 'ok' : 'degraded';

  return (
    <main className="placeholder">
      <h1>Online Chat</h1>
      <p>
        Backend status: <strong data-testid="health-status">{status}</strong>
      </p>
    </main>
  );
}
