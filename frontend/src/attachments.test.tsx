import { act, fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { formatSize, pastedName, precheck, type Attachment } from './api/attachments';
import { MessageAttachments, previewSize } from './chat/Attachments';
import { Composer } from './chat/Composer';
import { json, mockApi } from './test/utils';

class FakeXHR {
  static all: FakeXHR[] = [];
  method = '';
  url = '';
  body: FormData | null = null;
  status = 0;
  responseText = '';
  upload: { onprogress: ((e: { lengthComputable: boolean; loaded: number; total: number }) => void) | null } = {
    onprogress: null,
  };
  onload: (() => void) | null = null;
  onerror: (() => void) | null = null;
  onabort: (() => void) | null = null;
  aborted = false;
  constructor() {
    FakeXHR.all.push(this);
  }
  open(method: string, url: string) {
    this.method = method;
    this.url = url;
  }
  send(body: FormData) {
    this.body = body;
  }
  abort() {
    this.aborted = true;
    this.onabort?.();
  }
  progress(loaded: number, total: number) {
    this.upload.onprogress?.({ lengthComputable: true, loaded, total });
  }
  respond(status: number, body: unknown) {
    this.status = status;
    this.responseText = JSON.stringify(body);
    this.onload?.();
  }
}

const ATT = (id: number, extra: Partial<Attachment> = {}): Attachment => ({
  id, original_filename: 'notes.txt', mime_type: 'text/plain', size_bytes: 1234, is_image: false, width: null,
  height: null, comment: null, download_url: `/api/attachments/${id}/download`, ...extra,
});

beforeEach(() => {
  FakeXHR.all = [];
  vi.stubGlobal('XMLHttpRequest', FakeXHR);
  vi.stubGlobal('URL', Object.assign(URL, { createObjectURL: () => 'blob:preview', revokeObjectURL: () => {} }));
});
afterEach(() => vi.unstubAllGlobals());

function renderComposer() {
  const onSend = vi.fn();
  render(
    <Composer chat={{ type: 'room', id: 4 }} replyTo={null} editing={null} onSend={onSend} onSaveEdit={() => {}}
      onCancel={() => {}} onEditLast={() => {}} onTyping={() => {}} />,
  );
  return { onSend, input: screen.getByLabelText('Message') as HTMLTextAreaElement };
}

const file = (name: string, size: number, type: string) => new File([new Uint8Array(size)], name, { type });

describe('helpers', () => {
  it('pre-checks limits like the server', () => {
    expect(precheck({ size: 3 * 1024 * 1024 + 1, type: 'image/png' })).toBe('Image exceeds 3 MB');
    expect(precheck({ size: 3 * 1024 * 1024 + 1, type: 'application/zip' })).toBeNull();
    expect(precheck({ size: 20 * 1024 * 1024 + 1, type: 'application/zip' })).toBe('File exceeds 20 MB');
    expect(precheck({ size: 0, type: 'text/plain' })).toBe('File is empty');
  });

  it('names pasted images and formats sizes', () => {
    const now = new Date(2026, 10, 23, 9, 5, 7);
    expect(pastedName({ name: 'image.png', type: 'image/png' }, now)).toBe('pasted-image-20261123-090507.png');
    expect(pastedName({ name: '', type: 'image/jpeg' }, now)).toBe('pasted-image-20261123-090507.jpg');
    expect(pastedName({ name: 'scan.pdf', type: 'application/pdf' }, now)).toBe('scan.pdf');
    expect(formatSize(512)).toBe('512 B');
    expect(formatSize(1536)).toBe('1.5 KB');
    expect(formatSize(5 * 1024 * 1024)).toBe('5.0 MB');
  });

  it('reserves the preview size within 480×320', () => {
    expect(previewSize({ width: 1600, height: 1200 })).toEqual({ width: 427, height: 320 });
    expect(previewSize({ width: 100, height: 50 })).toEqual({ width: 100, height: 50 });
    expect(previewSize({ width: null, height: null })).toBeNull();
  });
});

describe('composer attachments', () => {
  it('uploads at once with progress, blocks Send until done, saves the comment and sends ids', async () => {
    const calls = mockApi({ 'PATCH /api/attachments/9': () => json(ATT(9, { comment: 'read me' })) });
    const { onSend, input } = renderComposer();
    fireEvent.change(input, { target: { value: 'see attached' } });
    fireEvent.change(screen.getByTestId('file-input'), { target: { files: [file('notes.txt', 1234, 'text/plain')] } });

    const xhr = FakeXHR.all[0]!;
    expect(xhr.method).toBe('POST');
    expect(xhr.url).toBe('/api/rooms/4/attachments');
    expect((xhr.body!.get('file') as File).name).toBe('notes.txt');
    act(() => xhr.progress(617, 1234));
    expect(screen.getByText(/50%/)).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Send' })).toBeDisabled();

    act(() => xhr.respond(201, ATT(9)));
    expect(await screen.findByLabelText('Comment for notes.txt')).toBeInTheDocument();
    fireEvent.change(screen.getByLabelText('Comment for notes.txt'), { target: { value: 'read me' } });
    fireEvent.click(screen.getByRole('button', { name: 'Send' }));

    await waitFor(() => expect(onSend).toHaveBeenCalled());
    expect(onSend).toHaveBeenCalledWith('see attached', [ATT(9, { comment: 'read me' })]);
    expect(JSON.parse(String(calls.find((c) => c.key === 'PATCH /api/attachments/9')?.init?.body))).toEqual({ comment: 'read me' });
    expect(screen.queryByRole('list', { name: 'Attachments' })).not.toBeInTheDocument();
  });

  it('sends files without text', async () => {
    const { onSend } = renderComposer();
    fireEvent.change(screen.getByTestId('file-input'), { target: { files: [file('a.txt', 3, 'text/plain')] } });
    act(() => FakeXHR.all[0]!.respond(201, ATT(1)));
    await screen.findByLabelText('Comment for a.txt');
    fireEvent.click(screen.getByRole('button', { name: 'Send' }));
    expect(onSend).toHaveBeenCalledWith('', [ATT(1)]);
  });

  it('rejects too large files before uploading and shows server errors on the chip', async () => {
    renderComposer();
    fireEvent.change(screen.getByTestId('file-input'), {
      target: { files: [file('big.png', 3 * 1024 * 1024 + 1, 'image/png'), file('doc.bin', 10, 'application/octet-stream')] },
    });
    expect(screen.getByRole('alert')).toHaveTextContent('Image exceeds 3 MB');
    expect(FakeXHR.all).toHaveLength(1); // only doc.bin went up
    act(() => FakeXHR.all[0]!.respond(413, { error: { code: 'image_too_large', message: 'Image exceeds 3 MB' } }));
    await waitFor(() => expect(screen.getAllByRole('alert')).toHaveLength(2));
  });

  it('uploads a pasted image under a generated name; plain text pastes normally', () => {
    const { input } = renderComposer();
    const img = file('image.png', 50, 'image/png');
    fireEvent.paste(input, { clipboardData: { items: [{ kind: 'file', getAsFile: () => img }], files: [img] } });
    expect((FakeXHR.all[0]!.body!.get('file') as File).name).toMatch(/^pasted-image-\d{8}-\d{6}\.png$/);
    fireEvent.paste(input, { clipboardData: { items: [{ kind: 'string', getAsFile: () => null }], files: [] } });
    expect(FakeXHR.all).toHaveLength(1);
  });

  it('removing a chip aborts the upload or deletes the pending attachment', async () => {
    const calls = mockApi({ 'DELETE /api/attachments/5': () => json(undefined, 204) });
    renderComposer();
    fireEvent.change(screen.getByTestId('file-input'), {
      target: { files: [file('one.txt', 1, 'text/plain'), file('two.txt', 1, 'text/plain')] },
    });
    fireEvent.click(screen.getByRole('button', { name: 'Remove one.txt' }));
    expect(FakeXHR.all[0]!.aborted).toBe(true);
    act(() => FakeXHR.all[1]!.respond(201, ATT(5, { original_filename: 'two.txt' })));
    await screen.findByLabelText('Comment for two.txt');
    fireEvent.click(screen.getByRole('button', { name: 'Remove two.txt' }));
    await waitFor(() => expect(calls.some((c) => c.key === 'DELETE /api/attachments/5')).toBe(true));
    expect(screen.queryByRole('list', { name: 'Attachments' })).not.toBeInTheDocument();
  });

  it('allows at most 10 attachments', () => {
    renderComposer();
    const files = Array.from({ length: 12 }, (_, i) => file(`${i}.txt`, 1, 'text/plain'));
    fireEvent.change(screen.getByTestId('file-input'), { target: { files } });
    expect(FakeXHR.all).toHaveLength(10);
    expect(screen.getByRole('alert')).toHaveTextContent('At most 10 attachments per message');
  });
});

describe('attachments in the feed', () => {
  it('shows image previews with a reserved size and a lightbox, files as download cards', () => {
    render(
      <MessageAttachments
        attachments={[
          ATT(1, { is_image: true, mime_type: 'image/png', original_filename: 'cat.png', width: 960, height: 640, comment: 'my cat' }),
          ATT(2, { original_filename: 'отчёт.pdf', size_bytes: 2 * 1024 * 1024 }),
        ]}
      />,
    );
    const img = screen.getByRole('img', { name: 'my cat' });
    expect(img).toHaveAttribute('src', '/api/attachments/1/download?inline=1');
    expect(img).toHaveAttribute('width', '480');
    expect(img).toHaveAttribute('height', '320');
    const link = screen.getByRole('link', { name: 'отчёт.pdf' });
    expect(link).toHaveAttribute('href', '/api/attachments/2/download');
    expect(link).toHaveAttribute('download', 'отчёт.pdf');
    expect(screen.getByText('2.0 MB')).toBeInTheDocument();

    fireEvent.click(screen.getByRole('button', { name: 'Open cat.png' }));
    const dialog = screen.getByRole('dialog', { name: 'cat.png' });
    expect(within(dialog).getByRole('link', { name: 'Download' })).toHaveAttribute('href', '/api/attachments/1/download');
  });
});
