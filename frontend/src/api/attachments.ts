/** Attachments (docs/API_AND_WS_SPEC.md §10). Uploads use XHR for progress events. */
import { ApiError, apiRequest } from './client';
import type { ChatRef } from './messages';

export interface Attachment {
  id: number;
  original_filename: string;
  mime_type: string;
  size_bytes: number;
  is_image: boolean;
  width: number | null;
  height: number | null;
  comment: string | null;
  download_url: string;
}

export const FILE_MAX_BYTES = 20 * 1024 * 1024;
export const IMAGE_MAX_BYTES = 3 * 1024 * 1024;
export const MAX_ATTACHMENTS = 10;

/** Client-side pre-check (the server checks again, by content). */
export function precheck(file: { size: number; type: string }): string | null {
  if (file.size === 0) return 'File is empty';
  if (file.size > FILE_MAX_BYTES) return 'File exceeds 20 MB';
  if (file.type.startsWith('image/') && file.size > IMAGE_MAX_BYTES) return 'Image exceeds 3 MB';
  return null;
}

/** Pasted screenshots come without a real name (`image.png` or empty). */
export function pastedName(file: { name: string; type: string }, now = new Date()): string {
  if (file.name && file.name !== 'image.png') return file.name;
  const pad = (n: number) => String(n).padStart(2, '0');
  const stamp = `${now.getFullYear()}${pad(now.getMonth() + 1)}${pad(now.getDate())}-${pad(now.getHours())}${pad(now.getMinutes())}${pad(now.getSeconds())}`;
  const ext = file.type.split('/')[1]?.replace('jpeg', 'jpg') || 'png';
  return `pasted-image-${stamp}.${ext}`;
}

export function formatSize(bytes: number): string {
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(bytes < 10 * 1024 ? 1 : 0)} KB`;
  return `${(bytes / 1024 / 1024).toFixed(1)} MB`;
}

export interface Upload {
  promise: Promise<Attachment>;
  abort: () => void;
}

export function uploadAttachment(
  chat: ChatRef,
  file: Blob,
  name: string,
  onProgress: (fraction: number) => void,
): Upload {
  const xhr = new XMLHttpRequest();
  const promise = new Promise<Attachment>((resolve, reject) => {
    xhr.open('POST', `/api/${chat.type === 'room' ? 'rooms' : 'dms'}/${chat.id}/attachments`);
    xhr.upload.onprogress = (e) => {
      if (e.lengthComputable) onProgress(e.loaded / e.total);
    };
    xhr.onload = () => {
      let body: { error?: { code?: string; message?: string } } | Attachment | null = null;
      try {
        body = JSON.parse(xhr.responseText);
      } catch {
        body = null;
      }
      if (xhr.status === 201 && body) return resolve(body as Attachment);
      const err = (body as { error?: { code?: string; message?: string } } | null)?.error;
      reject(new ApiError(xhr.status, err?.code ?? `http_${xhr.status}`, err?.message ?? 'Upload failed'));
    };
    xhr.onerror = () => reject(new ApiError(0, 'network_error', 'Upload failed: network error'));
    xhr.onabort = () => reject(new ApiError(0, 'aborted', 'Upload cancelled'));
    const form = new FormData();
    form.append('file', file, name);
    xhr.send(form);
  });
  return { promise, abort: () => xhr.abort() };
}

export const setAttachmentComment = (id: number, comment: string) =>
  apiRequest<Attachment>(`/attachments/${id}`, { method: 'PATCH', body: JSON.stringify({ comment: comment.trim() || null }) });

export const cancelAttachment = (id: number): Promise<void> =>
  apiRequest<void>(`/attachments/${id}`, { method: 'DELETE' });
