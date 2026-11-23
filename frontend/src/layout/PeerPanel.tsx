import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { useState } from 'react';
import { banUser, contactKeys, fetchProfile, FROZEN_TEXT, removeFriend, unbanUser, type Dm } from '../api/contacts';
import { apiErrorToFields, FormError } from '../components/forms';
import { Modal } from '../components/Modal';
import { UserPresence } from '../components/Presence';
import { usePresence } from '../stores/presence';
import { useLayout } from '../stores/layout';

export const BAN_WARNING =
  "They won't be able to contact you. Your friendship will end and your conversation will become read-only.";

/** Right panel of a DM (FRONTEND_SPEC §4.1 rule 3): the peer, presence, friendship actions. */
export function PeerPanel({ dm }: { dm: Dm }) {
  const qc = useQueryClient();
  const setCtxOpen = useLayout((s) => s.setCtxOpen);
  const peer = dm.peer;
  const presence = usePresence(peer);
  const [confirm, setConfirm] = useState<'ban' | 'remove' | null>(null);
  const profile = useQuery({
    queryKey: contactKeys.profile(peer.username),
    queryFn: () => fetchProfile(peer.username),
    enabled: peer.id !== null,
  });

  const refresh = async () => {
    setConfirm(null);
    await qc.invalidateQueries({ queryKey: contactKeys.all });
    await qc.invalidateQueries({ queryKey: contactKeys.dm(dm.dm_id) });
    await qc.invalidateQueries({ queryKey: contactKeys.profile(peer.username) });
    await qc.invalidateQueries({ queryKey: contactKeys.bans });
  };
  const remove = useMutation({ mutationFn: () => removeFriend(peer.id!), onSuccess: refresh });
  const ban = useMutation({ mutationFn: () => banUser(peer.id!), onSuccess: refresh });
  const unban = useMutation({ mutationFn: () => unbanUser(peer.id!), onSuccess: refresh });
  const error = remove.error ?? ban.error ?? unban.error;
  const rel = profile.data;

  return (
    <aside className="ctx-panel" aria-label="Conversation details">
      <button className="btn ghost small ctx-close" aria-label="Close details" onClick={() => setCtxOpen(false)}>
        ✕
      </button>
      <section className="ctx-group peer-card">
        <span className="avatar static" aria-hidden>{peer.username.slice(0, 2).toUpperCase()}</span>
        <h2 className="ctx-title">{peer.id === null ? <em>{peer.username}</em> : peer.username}</h2>
        {peer.display_name && <p className="muted">{peer.display_name}</p>}
        {/* A frozen DM's peer is no longer in our presence audience: their last state would be stale. */}
        {peer.id !== null && !dm.frozen && (
          <p>
            <UserPresence user={peer} /> {presence}
          </p>
        )}
        {dm.frozen && dm.frozen_reason && (
          <p className="frozen-note">🔒 Read-only: {FROZEN_TEXT[dm.frozen_reason]}</p>
        )}
      </section>
      <FormError message={error ? apiErrorToFields(error).form : null} />
      {peer.id !== null && rel && (
        <div className="ctx-actions column">
          {rel.friendship === 'friends' && (
            <button className="btn small" onClick={() => setConfirm('remove')}>Remove friend</button>
          )}
          {rel.banned_by_me ? (
            <button className="btn small" disabled={unban.isPending} onClick={() => unban.mutate()}>Unban user</button>
          ) : (
            <button className="btn small danger-outline" onClick={() => setConfirm('ban')}>Ban user</button>
          )}
        </div>
      )}
      {confirm && (
        <Modal title={confirm === 'ban' ? `Ban ${peer.username}?` : `Remove ${peer.username} from friends?`} onClose={() => setConfirm(null)}>
          <p>{confirm === 'ban' ? BAN_WARNING : 'Your conversation will become read-only until you are friends again.'}</p>
          <div className="modal-actions">
            <button className="btn" onClick={() => setConfirm(null)}>Cancel</button>
            <button className="btn danger" disabled={ban.isPending || remove.isPending}
              onClick={() => (confirm === 'ban' ? ban.mutate() : remove.mutate())}>
              {confirm === 'ban' ? 'Ban' : 'Remove'}
            </button>
          </div>
        </Modal>
      )}
    </aside>
  );
}
