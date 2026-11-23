import { useQueryClient } from '@tanstack/react-query';
import { useEffect, useRef } from 'react';
import { useLocation, useNavigate } from 'react-router-dom';
import type { Me, Presence, UserPublic } from '../api/auth';
import { contactKeys } from '../api/contacts';
import { chatKey, fetchMessages, type ChatRef, type Message } from '../api/messages';
import { roomKeys } from '../api/rooms';
import { useMessages } from '../chat/messagesStore';
import { useTyping } from '../chat/typingStore';
import { ME_KEY } from '../queryClient';
import { usePresenceStore } from '../stores/presence';
import { toast } from '../stores/toasts';
import { socket, type ServerEvent } from './socket';

const CATCH_UP_LIMIT = 100;
const CATCH_UP_MAX_PAGES = 2; // then reload the latest page instead (API_AND_WS_SPEC §12.4)

/** After a reconnect: fetch what was missed for every chat held in memory. */
export async function catchUp(): Promise<void> {
  const store = useMessages.getState();
  for (const [key, chat] of Object.entries(store.chats)) {
    if (!chat.loaded || chat.hasMoreAfter) continue;
    const [type, id] = key.split(':');
    const ref: ChatRef = { type: type as ChatRef['type'], id: Number(id) };
    let after = chat.messages.filter((m) => m.id > 0).at(-1)?.id ?? 0;
    try {
      for (let page = 0; ; page++) {
        if (page === CATCH_UP_MAX_PAGES) {
          const latest = await fetchMessages(ref);
          store.setLatest(key, latest.items, latest.has_more);
          break;
        }
        const res = await fetchMessages(ref, { after_id: after }, CATCH_UP_LIMIT);
        store.appendNewer(key, res.items, false);
        if (!res.has_more || res.next_cursor === null) break;
        after = res.next_cursor;
      }
    } catch {
      store.drop(key); // lost access meanwhile or server trouble: reload when opened again
    }
  }
}

/** Owns the tab's WebSocket while signed in and routes server events (API_AND_WS_SPEC §12.3). */
export function SocketBridge() {
  const qc = useQueryClient();
  const navigate = useNavigate();
  const location = useLocation();
  const pathRef = useRef(location.pathname);
  pathRef.current = location.pathname;
  // useNavigate() changes identity on every route change; the socket must not restart with it.
  const navigateRef = useRef(navigate);
  navigateRef.current = navigate;

  useEffect(() => {
    const messages = useMessages.getState();
    const roomGone = (roomId: number, text: string | null) => {
      messages.drop(chatKey({ type: 'room', id: roomId }));
      qc.removeQueries({ queryKey: roomKeys.detail(roomId) });
      void qc.invalidateQueries({ queryKey: roomKeys.mine });
      void qc.invalidateQueries({ queryKey: roomKeys.catalogAll });
      if (pathRef.current === `/rooms/${roomId}`) {
        navigateRef.current('/', { replace: true });
        if (text) toast(text);
      }
    };

    const onEvent = (e: ServerEvent) => {
      switch (e.type) {
        case 'new_message':
        case 'message_edited': {
          const m = e.message as Message;
          const key = chatKey(m.chat);
          messages.upsert(key, m);
          if (e.type === 'new_message' && m.author.id !== null) useTyping.getState().stopped(key, m.author.id);
          break;
        }
        case 'message_deleted':
          messages.markDeleted(chatKey(e.chat as ChatRef), Number(e.message_id));
          break;
        case 'typing': {
          const user = e.user as { id: number; username: string };
          useTyping.getState().typing(chatKey(e.chat as ChatRef), user.id, user.username);
          break;
        }
        case 'presence_snapshot':
          usePresenceStore.getState().snapshot(e.users as { user_id: number; presence: Presence }[]);
          break;
        case 'presence_update':
          usePresenceStore.getState().update(Number(e.user_id), e.presence as Presence);
          break;
        case 'room_access_revoked': {
          const roomId = Number(e.room_id);
          const name = qc.getQueryData<{ name: string }>(roomKeys.detail(roomId))?.name;
          const label = name ? `#${name}` : 'this room';
          const text =
            e.reason === 'banned'
              ? `You were banned from ${label}`
              : e.reason === 'room_deleted'
                ? `Room ${label} was deleted`
                : null;
          roomGone(roomId, text);
          void qc.invalidateQueries({ queryKey: roomKeys.myInvitations });
          break;
        }
        case 'room_joined':
        case 'room_updated': {
          const room = e.room as { id: number };
          void qc.invalidateQueries({ queryKey: roomKeys.mine });
          void qc.invalidateQueries({ queryKey: roomKeys.detail(room.id) });
          void qc.invalidateQueries({ queryKey: roomKeys.catalogAll });
          break;
        }
        case 'member_joined':
        case 'member_removed':
        case 'member_role_changed': {
          const roomId = Number(e.room_id);
          void qc.invalidateQueries({ queryKey: roomKeys.members(roomId) });
          void qc.invalidateQueries({ queryKey: roomKeys.detail(roomId) });
          if (e.type === 'member_removed' && e.reason === 'banned') {
            void qc.invalidateQueries({ queryKey: roomKeys.bans(roomId) });
          }
          if (e.type === 'member_role_changed') void qc.invalidateQueries({ queryKey: roomKeys.mine });
          break;
        }
        case 'friend_request_received': {
          const req = e.request as { from: { username: string } };
          void qc.invalidateQueries({ queryKey: contactKeys.all });
          toast(`${req.from.username} sent you a friend request`);
          break;
        }
        case 'friend_request_resolved':
          void qc.invalidateQueries({ queryKey: contactKeys.all });
          void qc.invalidateQueries({ queryKey: ['user'] });
          break;
        case 'friend_added': {
          const friend = e.user as UserPublic;
          if (friend.id !== null) usePresenceStore.getState().update(friend.id, friend.presence);
          void qc.invalidateQueries({ queryKey: contactKeys.all });
          void qc.invalidateQueries({ queryKey: ['user'] });
          break;
        }
        case 'friend_removed':
          void qc.invalidateQueries({ queryKey: contactKeys.all });
          void qc.invalidateQueries({ queryKey: ['user'] });
          break;
        case 'dm_frozen':
        case 'dm_unfrozen':
          void qc.invalidateQueries({ queryKey: contactKeys.dm(Number(e.dm_id)) });
          void qc.invalidateQueries({ queryKey: contactKeys.all });
          break;
        case 'invitation_received': {
          const inv = e.invitation as { room: { name: string }; inviter: { username: string } };
          void qc.invalidateQueries({ queryKey: roomKeys.myInvitations });
          toast(`${inv.inviter.username} invited you to #${inv.room.name}`);
          break;
        }
        default:
          break;
      }
    };

    socket.onOpen = (reconnected) => {
      if (!reconnected) return;
      void qc.invalidateQueries({ queryKey: roomKeys.mine });
      void qc.invalidateQueries({ queryKey: roomKeys.myInvitations });
      void qc.invalidateQueries({ queryKey: contactKeys.all });
      void qc.invalidateQueries({ queryKey: ['dm'] });
      void catchUp();
    };
    socket.onUnauthenticated = () => {
      messages.reset();
      qc.setQueryData<Me | null>(ME_KEY, null);
    };
    const unsubscribe = socket.subscribe(onEvent);
    socket.start();
    const prune = setInterval(() => useTyping.getState().prune(), 1000);
    return () => {
      clearInterval(prune);
      unsubscribe();
      socket.stop();
      messages.reset();
      usePresenceStore.getState().reset();
    };
  }, [qc]);

  return null;
}
