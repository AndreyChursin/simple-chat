import { MutationCache, QueryCache, QueryClient } from '@tanstack/react-query';
import { ApiError } from './api/client';

export const ME_KEY = ['me'] as const;

/** Any request that reports a dead session (expired, revoked from another device, signed out in
 * another tab) drops the cached user, which makes <RequireAuth> redirect to /login. */
export function createQueryClient(): QueryClient {
  const onError = (error: unknown): void => {
    if (error instanceof ApiError && error.status === 401 && error.code === 'not_authenticated') {
      client.setQueryData(ME_KEY, null);
    }
  };
  const client: QueryClient = new QueryClient({
    queryCache: new QueryCache({ onError }),
    mutationCache: new MutationCache({ onError }),
    defaultOptions: {
      queries: {
        refetchOnWindowFocus: false,
        staleTime: 30_000,
        retry: (count, error) => !(error instanceof ApiError && error.status < 500) && count < 2,
      },
    },
  });
  return client;
}
