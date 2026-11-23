import { useState } from 'react';
import { formatSize, type Attachment } from '../api/attachments';
import { Modal } from '../components/Modal';

export const PREVIEW_MAX_HEIGHT = 320;
const PREVIEW_MAX_WIDTH = 480;

/** Display size inside 480×320 keeping the aspect ratio, known before the image loads so the feed
 * does not jump (FRONTEND_SPEC §4.3). */
export function previewSize(a: Pick<Attachment, 'width' | 'height'>): { width: number; height: number } | null {
  if (!a.width || !a.height) return null;
  const scale = Math.min(1, PREVIEW_MAX_WIDTH / a.width, PREVIEW_MAX_HEIGHT / a.height);
  return { width: Math.round(a.width * scale), height: Math.round(a.height * scale) };
}

export function MessageAttachments({ attachments }: { attachments: Attachment[] }) {
  const [open, setOpen] = useState<Attachment | null>(null);
  if (!attachments.length) return null;
  return (
    <div className="msg-attachments">
      {attachments.map((a) =>
        a.is_image ? (
          <figure key={a.id} className="att-image">
            <button className="att-image-button" onClick={() => setOpen(a)} aria-label={`Open ${a.original_filename}`}>
              <img src={`${a.download_url}?inline=1`} alt={a.comment ?? a.original_filename} loading="lazy"
                {...(previewSize(a) ?? {})} />
            </button>
            {a.comment && <figcaption>{a.comment}</figcaption>}
          </figure>
        ) : (
          <div key={a.id} className="att-file">
            <span className="att-file-icon" aria-hidden>📄</span>
            <div className="att-file-main">
              <a href={a.download_url} download={a.original_filename} className="att-file-name">{a.original_filename}</a>
              <span className="muted small">{formatSize(a.size_bytes)}</span>
              {a.comment && <span className="att-comment">{a.comment}</span>}
            </div>
            <a className="btn small" href={a.download_url} download={a.original_filename} aria-label={`Download ${a.original_filename}`}>
              ⬇
            </a>
          </div>
        ),
      )}
      {open && (
        <Modal title={open.original_filename} onClose={() => setOpen(null)} wide>
          <div className="lightbox">
            <img src={`${open.download_url}?inline=1`} alt={open.comment ?? open.original_filename} />
            {open.comment && <p>{open.comment}</p>}
            <a className="btn" href={open.download_url} download={open.original_filename}>Download</a>
          </div>
        </Modal>
      )}
    </div>
  );
}
