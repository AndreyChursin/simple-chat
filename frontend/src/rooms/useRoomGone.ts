import { useQueryClient } from '@tanstack/react-query';
import { useNavigate } from 'react-router-dom';
import { roomKeys } from '../api/rooms';
import { toast } from '../stores/toasts';

/** Access to a room is gone (left / deleted / banned): drop its cached data, leave the room screen,
 * tell the user (FRONTEND_SPEC §4.4 "Потеря доступа"). The WS `room_access_revoked` handler reuses it. */
export function useRoomGone(): (roomId: number, message: string) => void {
  const qc = useQueryClient();
  const navigate = useNavigate();
  return (roomId, message) => {
    qc.removeQueries({ queryKey: roomKeys.detail(roomId) });
    void qc.invalidateQueries({ queryKey: roomKeys.mine });
    void qc.invalidateQueries({ queryKey: roomKeys.catalogAll });
    navigate('/', { replace: true });
    toast(message);
  };
}
