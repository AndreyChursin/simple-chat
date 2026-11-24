import { useQuery, useQueryClient } from '@tanstack/react-query';
import type { ReactNode } from 'react';
import { Navigate, useLocation, useNavigate } from 'react-router-dom';
import { fetchMe, logout, type Me } from '../api/auth';
import { ME_KEY } from '../queryClient';

export function useMe() {
  return useQuery({ queryKey: ME_KEY, queryFn: fetchMe, staleTime: Infinity });
}

/** Signs out only this browser session, then clears every cached query. */
export function useSignOut(): () => Promise<void> {
  const qc = useQueryClient();
  const navigate = useNavigate();
  return async () => {
    try {
      await logout();
    } finally {
      qc.clear();
      qc.setQueryData<Me | null>(ME_KEY, null);
      navigate('/login', { replace: true });
    }
  };
}

export function useSetMe(): (me: Me | null) => void {
  const qc = useQueryClient();
  return (me) => {
    qc.setQueryData<Me | null>(ME_KEY, me);
  };
}

function FullPageStatus({ text }: { text: string }) {
  return <div className="page-status">{text}</div>;
}

export function RequireAuth({ children }: { children: ReactNode }) {
  const me = useMe();
  const location = useLocation();
  if (me.isPending) return <FullPageStatus text="Loading…" />;
  if (me.isError) return <FullPageStatus text="Server is unavailable. Please retry later." />;
  if (!me.data) {
    const next = encodeURIComponent(location.pathname + location.search + location.hash);
    return <Navigate to={`/login?next=${next}`} replace />;
  }
  return <>{children}</>;
}

export function GuestOnly({ children }: { children: ReactNode }) {
  const me = useMe();
  const location = useLocation();
  if (me.isPending) return <FullPageStatus text="Loading…" />;
  if (me.data) {
    // Also the post-login redirect: as soon as the user is known, leave the guest page for `next`.
    const next = new URLSearchParams(location.search).get('next');
    return <Navigate to={safeNext(next)} replace />;
  }
  return <>{children}</>;
}

/** Only same-origin relative paths are accepted as a post-login redirect (no open redirect). */
export function safeNext(raw: string | null): string {
  if (!raw || !raw.startsWith('/') || raw.startsWith('//')) return '/';
  return raw;
}

const DELETED_KEY = 'chat.accountDeleted';

/** One-shot notice for the sign-in page after the account was deleted in this tab. */
export function markAccountDeleted(): void {
  try {
    sessionStorage.setItem(DELETED_KEY, '1');
  } catch {
    /* storage unavailable: the notice is just skipped */
  }
}

export function takeAccountDeleted(): boolean {
  try {
    const set = sessionStorage.getItem(DELETED_KEY) === '1';
    sessionStorage.removeItem(DELETED_KEY);
    return set;
  } catch {
    return false;
  }
}
