import { create } from 'zustand';

/** Drawer state of the Main Chat Layout on narrow screens (FRONTEND_SPEC §4.1 rule 5). */
interface LayoutState {
  navOpen: boolean;
  ctxOpen: boolean;
  setNavOpen: (open: boolean) => void;
  setCtxOpen: (open: boolean) => void;
}

export const useLayout = create<LayoutState>((set) => ({
  navOpen: false,
  ctxOpen: false,
  setNavOpen: (navOpen) => set({ navOpen }),
  setCtxOpen: (ctxOpen) => set({ ctxOpen }),
}));
