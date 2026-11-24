import { useEffect } from 'react';
import { totalUnread, useUnread } from '../stores/unread';

const ICON = (dot: boolean) =>
  'data:image/svg+xml,' +
  encodeURIComponent(
    '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 32 32"><rect width="32" height="32" rx="7" fill="#2f6fde"/>' +
      '<path d="M8 10h16v9H14l-5 4v-4H8z" fill="#fff"/>' +
      (dot ? '<circle cx="25" cy="7" r="6.5" fill="#e5484d" stroke="#fff" stroke-width="1.5"/>' : '') +
      '</svg>',
  );

/** `document.title` = `(N) Chat` and a favicon with a dot while anything is unread (FRONTEND_SPEC §10). */
export function UnreadTitle() {
  const total = useUnread((s) => totalUnread(s.byChat));
  useEffect(() => {
    document.title = total > 0 ? `(${total > 99 ? '99+' : total}) Chat` : 'Chat';
    const link = document.querySelector<HTMLLinkElement>('link[rel="icon"]');
    if (link) link.href = total > 0 ? ICON(true) : '/favicon.svg';
  }, [total]);
  useEffect(
    () => () => {
      document.title = 'Chat';
    },
    [],
  );
  return null;
}
