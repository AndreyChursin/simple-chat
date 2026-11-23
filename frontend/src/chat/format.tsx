import type { ReactNode } from 'react';

const URL_RE = /\bhttps?:\/\/[^\s<>"']+[^\s<>"'.,;:!?)\]}]/gi;

/** Plain text with clickable http(s) links. Never renders HTML or Markdown (XSS-safe). */
export function linkify(text: string): ReactNode[] {
  const out: ReactNode[] = [];
  let last = 0;
  for (const match of text.matchAll(URL_RE)) {
    const start = match.index ?? 0;
    if (start > last) out.push(text.slice(last, start));
    out.push(
      <a key={start} href={match[0]} target="_blank" rel="noopener noreferrer">
        {match[0]}
      </a>,
    );
    last = start + match[0].length;
  }
  if (last < text.length) out.push(text.slice(last));
  return out;
}

export const GROUP_WINDOW_MS = 5 * 60 * 1000;

export function sameDay(a: string, b: string): boolean {
  return new Date(a).toDateString() === new Date(b).toDateString();
}

/** Consecutive messages of one author within 5 minutes (and the same day) share a header. */
export function isGrouped(
  prev: { author: { id: number | null }; created_at: string; deleted?: boolean } | undefined,
  cur: { author: { id: number | null }; created_at: string },
): boolean {
  if (!prev || prev.author.id === null || prev.author.id !== cur.author.id) return false;
  if (!sameDay(prev.created_at, cur.created_at)) return false;
  return new Date(cur.created_at).getTime() - new Date(prev.created_at).getTime() < GROUP_WINDOW_MS;
}

export const timeOf = (iso: string): string =>
  new Date(iso).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' });

export function dayLabel(iso: string, now = new Date()): string {
  const d = new Date(iso);
  if (d.toDateString() === now.toDateString()) return 'Today';
  const yesterday = new Date(now);
  yesterday.setDate(now.getDate() - 1);
  if (d.toDateString() === yesterday.toDateString()) return 'Yesterday';
  return d.toLocaleDateString([], { year: 'numeric', month: 'long', day: 'numeric' });
}

export function initials(name: string): string {
  return name.replace(/[^A-Za-z0-9]/g, '').slice(0, 2).toUpperCase() || '?';
}
