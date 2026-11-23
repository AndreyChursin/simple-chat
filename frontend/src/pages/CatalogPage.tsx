import { useInfiniteQuery, useMutation, useQueryClient } from '@tanstack/react-query';
import { useEffect, useRef, useState } from 'react';
import { Link, useNavigate, useSearchParams } from 'react-router-dom';
import { ApiError } from '../api/client';
import { fetchCatalog, joinRoom, roomKeys, type CatalogRoom } from '../api/rooms';
import { apiErrorToFields, FormError } from '../components/forms';
import { CreateRoomModal } from '../rooms/CreateRoomModal';
import { toast } from '../stores/toasts';

const SEARCH_DEBOUNCE_MS = 300;

function RoomCard({ room, onJoin, joining }: { room: CatalogRoom; onJoin: () => void; joining: boolean }) {
  return (
    <li className="room-card">
      <div className="room-card-main">
        <h3>#{room.name}</h3>
        <p className="room-card-description">{room.description || <span className="muted">No description</span>}</p>
        <small className="muted">
          {room.member_count} {room.member_count === 1 ? 'member' : 'members'}
        </small>
      </div>
      <div>
        {room.my_role ? (
          <Link className="btn" to={`/rooms/${room.id}`}>Open</Link>
        ) : room.is_banned ? (
          <button className="btn" disabled title="You are banned from this room">Banned</button>
        ) : (
          <button className="btn primary" disabled={joining} onClick={onJoin}>Join</button>
        )}
      </div>
    </li>
  );
}

/** Public Rooms Catalog (FRONTEND_SPEC §5). */
export function CatalogPage() {
  const qc = useQueryClient();
  const navigate = useNavigate();
  const [params, setParams] = useSearchParams();
  const [input, setInput] = useState(params.get('search') ?? '');
  const [search, setSearch] = useState(input.trim());
  const [creating, setCreating] = useState(false);
  const sentinel = useRef<HTMLDivElement>(null);

  useEffect(() => {
    const t = setTimeout(() => {
      const next = input.trim();
      setSearch(next);
      setParams(next ? { search: next } : {}, { replace: true });
    }, SEARCH_DEBOUNCE_MS);
    return () => clearTimeout(t);
  }, [input, setParams]);

  const catalog = useInfiniteQuery({
    queryKey: roomKeys.catalog(search),
    queryFn: ({ pageParam }) => fetchCatalog(search, pageParam),
    initialPageParam: null as number | null,
    getNextPageParam: (last) => (last.has_more ? last.next_cursor : undefined),
    staleTime: 0,
  });

  const { hasNextPage, isFetchingNextPage, fetchNextPage } = catalog;
  useEffect(() => {
    const el = sentinel.current;
    if (!el || typeof IntersectionObserver === 'undefined') return;
    const io = new IntersectionObserver((entries) => {
      if (entries.some((e) => e.isIntersecting) && hasNextPage && !isFetchingNextPage) void fetchNextPage();
    });
    io.observe(el);
    return () => io.disconnect();
  }, [hasNextPage, isFetchingNextPage, fetchNextPage]);

  const join = useMutation({
    mutationFn: (room: CatalogRoom) => joinRoom(room.id),
    onSuccess: async (room) => {
      await qc.invalidateQueries({ queryKey: roomKeys.mine });
      await qc.invalidateQueries({ queryKey: roomKeys.catalogAll });
      navigate(`/rooms/${room.id}`);
    },
    onError: async (err) => {
      if (err instanceof ApiError && err.code === 'room_banned') {
        toast('You are banned from this room', 'error');
        await qc.invalidateQueries({ queryKey: roomKeys.catalogAll });
      }
    },
  });

  const rooms = catalog.data?.pages.flatMap((p) => p.items) ?? [];
  const joinError = join.error && !(join.error instanceof ApiError && join.error.code === 'room_banned') ? join.error : null;

  return (
    <main className="page">
      <div className="page-header">
        <h1>Public rooms</h1>
        <button className="btn" onClick={() => setCreating(true)}>+ Create room</button>
      </div>
      <input className="search-input" type="search" placeholder="Search by name or description…"
        aria-label="Search public rooms" value={input} onChange={(e) => setInput(e.target.value)} />
      <FormError message={joinError ? apiErrorToFields(joinError).form : catalog.error ? apiErrorToFields(catalog.error).form : null} />
      {catalog.isPending ? (
        <p className="muted">Loading…</p>
      ) : rooms.length === 0 ? (
        <p className="muted">{search ? `No public rooms match “${search}”.` : 'No public rooms yet. Create the first one!'}</p>
      ) : (
        <ul className="room-cards">
          {rooms.map((r) => (
            <RoomCard key={r.id} room={r} joining={join.isPending} onJoin={() => join.mutate(r)} />
          ))}
        </ul>
      )}
      <div ref={sentinel} />
      {hasNextPage && (
        <button className="btn wide" disabled={isFetchingNextPage} onClick={() => void fetchNextPage()}>
          {isFetchingNextPage ? 'Loading…' : 'Load more'}
        </button>
      )}
      {creating && <CreateRoomModal onClose={() => setCreating(false)} />}
    </main>
  );
}
