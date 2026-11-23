import { useMutation, useQueryClient } from '@tanstack/react-query';
import { useParams } from 'react-router-dom';
import { ApiError } from '../api/client';
import { joinRoom, roomKeys, type RoomDetail } from '../api/rooms';
import { apiErrorToFields, FormError } from '../components/forms';
import { parseRoomId, useRoomDetail } from '../layout/ChatLayout';
import { useLayout } from '../stores/layout';
import { NavToggle } from './HomePage';

function RoomHeader({ room }: { room: RoomDetail }) {
  const setCtxOpen = useLayout((s) => s.setCtxOpen);
  return (
    <header className="chat-header">
      <NavToggle />
      <div className="chat-header-main">
        <h1>
          <span aria-hidden>{room.visibility === 'public' ? '#' : '🔒'}</span>
          {room.visibility === 'public' ? room.name : ` ${room.name}`}
        </h1>
        {room.description && (
          <p className="chat-header-description" title={room.description}>
            {room.description}
          </p>
        )}
      </div>
      <button className="btn ghost small ctx-toggle" aria-label="Show members" title="Members"
        onClick={() => setCtxOpen(true)} disabled={!room.my_role}>
        👥 {room.member_count}
      </button>
    </header>
  );
}

function JoinPrompt({ room }: { room: RoomDetail }) {
  const qc = useQueryClient();
  const join = useMutation({
    mutationFn: () => joinRoom(room.id),
    onSuccess: async () => {
      await qc.invalidateQueries({ queryKey: roomKeys.detail(room.id) });
      await qc.invalidateQueries({ queryKey: roomKeys.mine });
      await qc.invalidateQueries({ queryKey: roomKeys.catalogAll });
    },
  });
  return (
    <div className="chat-empty">
      <p>You are not a member of this room.</p>
      <FormError message={join.error ? apiErrorToFields(join.error).form : null} />
      <button className="btn primary" disabled={join.isPending} onClick={() => join.mutate()}>
        Join room
      </button>
    </div>
  );
}

export function RoomPage() {
  const roomId = parseRoomId(useParams().roomId);
  const detail = useRoomDetail(roomId);

  if (roomId === null || detail.isError) {
    const banned = detail.error instanceof ApiError && detail.error.code === 'room_banned';
    return (
      <div className="chat-empty">
        <NavToggle />
        <h1>{banned ? 'You are banned from this room' : 'Room not found'}</h1>
        <p className="muted">
          {banned ? 'An admin removed you from this room.' : 'It may have been deleted, or it is private.'}
        </p>
      </div>
    );
  }
  if (detail.isPending) return <div className="chat-empty muted">Loading…</div>;
  const room = detail.data;

  return (
    <div className="chat-window">
      <RoomHeader room={room} />
      {room.my_role ? (
        <div className="chat-feed-placeholder muted">No messages yet.</div>
      ) : (
        <JoinPrompt room={room} />
      )}
    </div>
  );
}
