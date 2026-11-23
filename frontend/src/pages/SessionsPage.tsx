import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { listSessions, revokeOtherSessions, revokeSession, type SessionInfo } from '../api/auth';
import { useSignOut } from '../auth/session';
import { FormError } from '../components/forms';
import { apiErrorToFields } from '../components/forms';

const DEVICE_ICON: Record<SessionInfo['device'], string> = {
  Desktop: '🖥️',
  Mobile: '📱',
  Tablet: '📱',
  Bot: '🤖',
  Other: '❔',
};

const fmt = (iso: string) => new Date(iso).toLocaleString();

export function SessionsPage() {
  const qc = useQueryClient();
  const signOut = useSignOut();
  const sessions = useQuery({ queryKey: ['sessions'], queryFn: listSessions, staleTime: 0 });

  const revoke = useMutation({
    mutationFn: (s: SessionInfo) => revokeSession(s.id),
    onSuccess: async (_, s) => {
      if (s.is_current) return void signOut();
      await qc.invalidateQueries({ queryKey: ['sessions'] });
    },
  });
  const revokeOthers = useMutation({
    mutationFn: revokeOtherSessions,
    onSuccess: () => qc.invalidateQueries({ queryKey: ['sessions'] }),
  });

  const items = sessions.data ?? [];
  const error = revoke.error ?? revokeOthers.error ?? sessions.error;

  return (
    <main className="page">
      <div className="page-header">
        <h1>Active sessions</h1>
        <button
          className="btn"
          disabled={items.length < 2 || revokeOthers.isPending}
          onClick={() => revokeOthers.mutate()}
        >
          Sign out all other sessions
        </button>
      </div>
      <p className="muted">
        Every browser where you are signed in. Signing out a session does not affect the others.
      </p>
      <FormError message={error ? apiErrorToFields(error).form : null} />
      {sessions.isPending ? (
        <p className="muted">Loading…</p>
      ) : (
        <table className="table">
          <thead>
            <tr>
              <th>Device</th>
              <th>IP address</th>
              <th>Signed in</th>
              <th>Last active</th>
              <th />
            </tr>
          </thead>
          <tbody>
            {items.map((s) => (
              <tr key={s.id}>
                <td title={s.user_agent}>
                  <span aria-hidden>{DEVICE_ICON[s.device]} </span>
                  <strong>{s.browser}</strong> · {s.os}
                  {s.is_current && <span className="badge">This device</span>}
                  {s.persistent && <span className="badge subtle" title="Keep me signed in">Remembered</span>}
                </td>
                <td>{s.ip ?? '—'}</td>
                <td>{fmt(s.created_at)}</td>
                <td>{s.is_current ? 'Now' : fmt(s.last_seen_at)}</td>
                <td className="right">
                  <button className="btn small" disabled={revoke.isPending} onClick={() => revoke.mutate(s)}>
                    Sign out
                  </button>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
    </main>
  );
}
