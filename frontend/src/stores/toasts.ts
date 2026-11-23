import { create } from 'zustand';

export interface Toast {
  id: number;
  text: string;
  kind: 'info' | 'error';
}

interface ToastState {
  toasts: Toast[];
  push: (text: string, kind?: Toast['kind']) => void;
  dismiss: (id: number) => void;
}

let nextId = 1;
const TTL_MS = 5000;

export const useToasts = create<ToastState>((set, get) => ({
  toasts: [],
  push: (text, kind = 'info') => {
    const id = nextId++;
    set({ toasts: [...get().toasts, { id, text, kind }] });
    setTimeout(() => get().dismiss(id), TTL_MS);
  },
  dismiss: (id) => set({ toasts: get().toasts.filter((t) => t.id !== id) }),
}));

export const toast = (text: string, kind?: Toast['kind']): void => useToasts.getState().push(text, kind);
