import { useQueryClient } from '@tanstack/react-query';
import { useNavigate } from 'react-router-dom';
import { chatKey } from '../api/messages';
import { roomKeys } from '../api/rooms';
import { useMessages } from '../chat/messagesStore';
import { toast } from '../stores/toasts';

/** Access to a room is gone (left / deleted / banned): drop its cached data, leave the room screen,
 * tell the user (FRONTEND_SPEC §4.4 "Потеря доступа"). The WS `room_access_revoked` handler reuses it. */
export function useRoomGone(): (roomId: number, message: string) => void {
  const qc = useQueryClient();
  const navigate = useNavigate();
  return (roomId, message) => {
    useMessages.getState().drop(chatKey({ type: 'room', id: roomId }));
    qc.removeQueries({ queryKey: roomKeys.detail(roomId) });
    void qc.invalidateQueries({ queryKey: roomKeys.mine });
    void qc.invalidateQueries({ queryKey: roomKeys.catalogAll });
    navigate('/', { replace: true });
    toast(message);
  };
}
