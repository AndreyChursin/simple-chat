import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { useEffect, useState, type FormEvent } from 'react';
import { inviteUser, roomKeys, searchUsers } from '../api/rooms';
import { apiErrorToFields, FormError, FormNotice } from '../components/forms';
import { Modal } from '../components/Modal';

function useDebounced<T>(value: T, ms: number): T {
  const [debounced, setDebounced] = useState(value);
  useEffect(() => {
    const t = setTimeout(() => setDebounced(value), ms);
    return () => clearTimeout(t);
  }, [value, ms]);
  return debounced;
}

export function InviteUserModal({ roomId, roomName, onClose }: { roomId: number; roomName: string; onClose: () => void }) {
  const qc = useQueryClient();
  const [username, setUsername] = useState('');
  const [notice, setNotice] = useState<string | null>(null);
  const query = useDebounced(username.trim(), 250);

  const suggestions = useQuery({
    queryKey: ['users', 'search', query],
    queryFn: () => searchUsers(query),
    enabled: query.length >= 2,
    staleTime: 10_000,
  });

  const invite = useMutation({
    mutationFn: (name: string) => inviteUser(roomId, name),
    onSuccess: async ({ invitation }) => {
      setNotice(`Invitation sent to ${invitation.invitee.username}`);
      setUsername('');
      await qc.invalidateQueries({ queryKey: roomKeys.invitations(roomId) });
    },
    onMutate: () => setNotice(null),
  });

  function onSubmit(e: FormEvent) {
    e.preventDefault();
    if (username.trim()) invite.mutate(username.trim());
  }

  const options = (suggestions.data ?? []).filter((u) => u.username.toLowerCase() !== username.trim().toLowerCase());

  return (
    <Modal title={`Invite to #${roomName}`} onClose={onClose}>
      <form onSubmit={onSubmit} noValidate>
        <FormError message={invite.error ? apiErrorToFields(invite.error).form : null} />
        <FormNotice message={notice} />
        <div className="field">
          <label htmlFor="f-invite-username">Username</label>
          <input id="f-invite-username" autoFocus autoComplete="off" value={username} maxLength={32}
            onChange={(e) => setUsername(e.target.value)} />
        </div>
        {query.length >= 2 && options.length > 0 && (
          <ul className="suggestions" aria-label="Matching users">
            {options.map((u) => (
              <li key={u.id ?? u.username}>
                <button type="button" className="btn ghost small" onClick={() => setUsername(u.username)}>
                  {u.username}
                  {u.display_name && <span className="muted"> · {u.display_name}</span>}
                </button>
              </li>
            ))}
          </ul>
        )}
        <div className="modal-actions">
          <button type="button" className="btn" onClick={onClose}>Close</button>
          <button type="submit" className="btn primary" disabled={!username.trim() || invite.isPending}>
            Send invitation
          </button>
        </div>
      </form>
    </Modal>
  );
}
