import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { useState, type FormEvent } from 'react';
import { Link, useNavigate, useSearchParams } from 'react-router-dom';
import type { UserPublic } from '../api/auth';
import {
  acceptFriendRequest,
  banUser,
  cancelFriendRequest,
  contactKeys,
  declineFriendRequest,
  fetchBans,
  fetchContacts,
  removeFriend,
  sendFriendRequest,
  unbanUser,
} from '../api/contacts';
import { apiErrorToFields, Field, FormError, FormNotice } from '../components/forms';
import { Modal } from '../components/Modal';
import { UserPresence } from '../components/Presence';
import { BAN_WARNING } from '../layout/PeerPanel';
import { toast } from '../stores/toasts';

type Tab = 'friends' | 'requests' | 'add' | 'blocked' | 'past';
const TABS: { id: Tab; label: string }[] = [
  { id: 'friends', label: 'Friends' },
  { id: 'requests', label: 'Requests' },
  { id: 'add', label: 'Add friend' },
  { id: 'blocked', label: 'Blocked' },
  { id: 'past', label: 'Past conversations' },
];

export function useContacts() {
  return useQuery({ queryKey: contactKeys.all, queryFn: fetchContacts });
}

function useRefreshContacts() {
  const qc = useQueryClient();
  return async () => {
    await qc.invalidateQueries({ queryKey: contactKeys.all });
    await qc.invalidateQueries({ queryKey: contactKeys.bans });
    await qc.invalidateQueries({ queryKey: ['dm'] });
    await qc.invalidateQueries({ queryKey: ['user'] });
  };
}

/** Contacts (FRONTEND_SPEC §7): friends, requests, add friend, blocked users, past conversations. */
export function ContactsPage() {
  const [params, setParams] = useSearchParams();
  const tab = (TABS.find((t) => t.id === params.get('tab'))?.id ?? 'friends') as Tab;
  const contacts = useContacts();
  const incoming = contacts.data?.incoming.length ?? 0;

  return (
    <main className="page">
      <h1>Contacts</h1>
      <div className="tabs" role="tablist">
        {TABS.map((t) => (
          <button key={t.id} role="tab" aria-selected={tab === t.id} className={`tab${tab === t.id ? ' active' : ''}`}
            onClick={() => setParams(t.id === 'friends' ? {} : { tab: t.id }, { replace: true })}>
            {t.label}
            {t.id === 'requests' && incoming > 0 && <span className="badge">{incoming}</span>}
          </button>
        ))}
      </div>
      {tab === 'friends' && <FriendsTab />}
      {tab === 'requests' && <RequestsTab />}
      {tab === 'add' && <AddFriendTab />}
      {tab === 'blocked' && <BlockedTab />}
      {tab === 'past' && <PastTab />}
    </main>
  );
}

function FriendsTab() {
  const contacts = useContacts();
  const refresh = useRefreshContacts();
  const [confirm, setConfirm] = useState<{ user: UserPublic; action: 'ban' | 'remove' } | null>(null);
  const remove = useMutation({ mutationFn: (id: number) => removeFriend(id), onSuccess: refresh });
  const ban = useMutation({ mutationFn: (id: number) => banUser(id), onSuccess: refresh });
  const friends = contacts.data?.friends ?? [];
  const error = remove.error ?? ban.error;

  if (contacts.isPending) return <p className="muted">Loading…</p>;
  return (
    <>
      <FormError message={error ? apiErrorToFields(error).form : null} />
      {friends.length === 0 ? (
        <p className="muted">
          No friends yet. <Link to="/contacts?tab=add">Add a friend</Link>.
        </p>
      ) : (
        <ul className="room-cards">
          {friends.map((f) => (
            <li key={f.id ?? f.username} className="room-card">
              <div className="room-card-main">
                <h3>
                  <UserPresence user={f} /> {f.username}
                </h3>
                {f.display_name && <small className="muted">{f.display_name}</small>}
              </div>
              <div className="row-actions">
                {f.dm_id !== null && <Link className="btn primary" to={`/dm/${f.dm_id}`}>Message</Link>}
                <button className="btn" onClick={() => setConfirm({ user: f, action: 'remove' })}>Remove friend</button>
                <button className="btn" onClick={() => setConfirm({ user: f, action: 'ban' })}>Ban</button>
              </div>
            </li>
          ))}
        </ul>
      )}
      {confirm && (
        <Modal
          title={confirm.action === 'ban' ? `Ban ${confirm.user.username}?` : `Remove ${confirm.user.username} from friends?`}
          onClose={() => setConfirm(null)}
        >
          <p>{confirm.action === 'ban' ? BAN_WARNING : 'Your conversation will become read-only until you are friends again.'}</p>
          <div className="modal-actions">
            <button className="btn" onClick={() => setConfirm(null)}>Cancel</button>
            <button
              className="btn danger"
              onClick={() => {
                const id = confirm.user.id!;
                setConfirm(null);
                if (confirm.action === 'ban') ban.mutate(id);
                else remove.mutate(id);
              }}
            >
              {confirm.action === 'ban' ? 'Ban' : 'Remove'}
            </button>
          </div>
        </Modal>
      )}
    </>
  );
}

function RequestsTab() {
  const contacts = useContacts();
  const refresh = useRefreshContacts();
  const navigate = useNavigate();
  const accept = useMutation({
    mutationFn: acceptFriendRequest,
    onSuccess: async (res) => {
      await refresh();
      toast(`You and ${res.friend.username} are now friends`);
      navigate(`/dm/${res.dm_id}`);
    },
    onError: refresh,
  });
  const decline = useMutation({ mutationFn: declineFriendRequest, onSettled: refresh });
  const cancel = useMutation({ mutationFn: cancelFriendRequest, onSettled: refresh });
  const incoming = contacts.data?.incoming ?? [];
  const outgoing = contacts.data?.outgoing ?? [];
  const error = accept.error ?? decline.error ?? cancel.error;

  return (
    <>
      <FormError message={error ? apiErrorToFields(error).form : null} />
      <section className="card">
        <h2>Incoming</h2>
        {incoming.length === 0 ? (
          <p className="muted">No incoming requests.</p>
        ) : (
          <ul className="room-cards">
            {incoming.map((r) => (
              <li key={r.id} className="room-card">
                <div className="room-card-main">
                  <h3>{r.from.username}</h3>
                  {r.message && <p className="room-card-description">“{r.message}”</p>}
                  <small className="muted">{new Date(r.created_at).toLocaleString()}</small>
                </div>
                <div className="row-actions">
                  <button className="btn primary" disabled={accept.isPending} onClick={() => accept.mutate(r.id)}>Accept</button>
                  <button className="btn" disabled={decline.isPending} onClick={() => decline.mutate(r.id)}>Decline</button>
                </div>
              </li>
            ))}
          </ul>
        )}
      </section>
      <section className="card">
        <h2>Sent</h2>
        {outgoing.length === 0 ? (
          <p className="muted">No pending requests.</p>
        ) : (
          <ul className="room-cards">
            {outgoing.map((r) => (
              <li key={r.id} className="room-card">
                <div className="room-card-main">
                  <h3>{r.to.username}</h3>
                  {r.message && <p className="room-card-description">“{r.message}”</p>}
                </div>
                <button className="btn" disabled={cancel.isPending} onClick={() => cancel.mutate(r.id)}>Cancel</button>
              </li>
            ))}
          </ul>
        )}
      </section>
    </>
  );
}

export function FriendRequestForm({ initialUsername = '', onDone }: { initialUsername?: string; onDone?: () => void }) {
  const refresh = useRefreshContacts();
  const [username, setUsername] = useState(initialUsername);
  const [message, setMessage] = useState('');
  const [notice, setNotice] = useState<string | null>(null);
  const send = useMutation({
    mutationFn: () => sendFriendRequest(username.trim(), message),
    onMutate: () => setNotice(null),
    onSuccess: async (res) => {
      setNotice('friendship' in res ? `You and ${res.friend.username} are now friends` : `Friend request sent to ${res.request.to.username}`);
      if (!initialUsername) setUsername('');
      setMessage('');
      await refresh();
      onDone?.();
    },
  });

  function onSubmit(e: FormEvent) {
    e.preventDefault();
    if (username.trim()) send.mutate();
  }

  return (
    <form onSubmit={onSubmit} noValidate>
      <FormError message={send.error ? apiErrorToFields(send.error).form : null} />
      <FormNotice message={notice} />
      {!initialUsername && (
        <Field label="Username" name="friend-username" value={username} maxLength={32} onChange={(e) => setUsername(e.target.value)} />
      )}
      <div className="field">
        <label htmlFor="f-friend-message">Message (optional)</label>
        <textarea id="f-friend-message" rows={2} maxLength={500} value={message} onChange={(e) => setMessage(e.target.value)} />
      </div>
      <button className="btn primary" type="submit" disabled={!username.trim() || send.isPending}>
        Send friend request
      </button>
    </form>
  );
}

function AddFriendTab() {
  return (
    <section className="card narrow-card">
      <h2>Add a friend</h2>
      <p className="muted">They need to accept your request before you can message each other.</p>
      <FriendRequestForm />
    </section>
  );
}

function BlockedTab() {
  const refresh = useRefreshContacts();
  const bans = useQuery({ queryKey: contactKeys.bans, queryFn: fetchBans });
  const unban = useMutation({ mutationFn: unbanUser, onSettled: refresh });
  const items = bans.data ?? [];
  if (bans.isPending) return <p className="muted">Loading…</p>;
  return (
    <>
      <FormError message={unban.error ? apiErrorToFields(unban.error).form : null} />
      {items.length === 0 ? (
        <p className="muted">You have not blocked anyone.</p>
      ) : (
        <ul className="room-cards">
          {items.map((b) => (
            <li key={b.user.id ?? b.user.username} className="room-card">
              <div className="room-card-main">
                <h3>{b.user.username}</h3>
                <small className="muted">Blocked {new Date(b.created_at).toLocaleDateString()}</small>
              </div>
              <button className="btn" disabled={unban.isPending} onClick={() => unban.mutate(b.user.id!)}>Unban</button>
            </li>
          ))}
        </ul>
      )}
      <p className="muted small">Unbanning does not restore the friendship; send a new friend request to talk again.</p>
    </>
  );
}

function PastTab() {
  const contacts = useContacts();
  const past = contacts.data?.past_dms ?? [];
  if (contacts.isPending) return <p className="muted">Loading…</p>;
  return past.length === 0 ? (
    <p className="muted">No past conversations.</p>
  ) : (
    <ul className="room-cards">
      {past.map((d) => (
        <li key={d.dm_id} className="room-card">
          <div className="room-card-main">
            <h3>🔒 {d.peer.id === null ? <em>{d.peer.username}</em> : d.peer.username}</h3>
            <small className="muted">Read-only history</small>
          </div>
          <Link className="btn" to={`/dm/${d.dm_id}`}>Open</Link>
        </li>
      ))}
    </ul>
  );
}
