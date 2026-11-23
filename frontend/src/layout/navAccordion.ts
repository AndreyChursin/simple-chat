/** Nav sidebar accordion (FRONTEND_SPEC §4.1 rules 1–2).
 *
 * - expanded (no chat open): every section is open;
 * - compact (a chat is open): only the section of the current chat is open, the rest collapse to a
 *   header with a summary badge. Manual toggles in compact mode are remembered in localStorage.
 */
import { useCallback, useState } from 'react';

export type NavSection = 'public' | 'private' | 'contacts';
export type NavMode = 'expanded' | 'compact';
export type SectionOverrides = Partial<Record<NavSection, boolean>>;

export const NAV_SECTIONS: NavSection[] = ['public', 'private', 'contacts'];
const STORAGE_KEY = 'chat.nav.compactSections';

export function isSectionOpen(
  section: NavSection,
  mode: NavMode,
  activeSection: NavSection | null,
  overrides: SectionOverrides,
): boolean {
  if (mode === 'expanded') return true;
  const manual = overrides[section];
  if (manual !== undefined) return manual;
  return section === activeSection;
}

function load(): SectionOverrides {
  try {
    const raw = localStorage.getItem(STORAGE_KEY);
    const parsed: unknown = raw ? JSON.parse(raw) : {};
    if (!parsed || typeof parsed !== 'object') return {};
    const result: SectionOverrides = {};
    for (const s of NAV_SECTIONS) {
      const v = (parsed as Record<string, unknown>)[s];
      if (typeof v === 'boolean') result[s] = v;
    }
    return result;
  } catch {
    return {};
  }
}

export function useNavAccordion(mode: NavMode, activeSection: NavSection | null) {
  const [overrides, setOverrides] = useState<SectionOverrides>(load);

  const isOpen = useCallback(
    (section: NavSection) => isSectionOpen(section, mode, activeSection, overrides),
    [mode, activeSection, overrides],
  );

  const toggle = useCallback(
    (section: NavSection) => {
      if (mode === 'expanded') return; // everything is open while no chat is selected
      const next = { ...overrides, [section]: !isSectionOpen(section, mode, activeSection, overrides) };
      // The current chat's section is open by default; storing "open" for it would be redundant.
      if (section === activeSection && next[section]) delete next[section];
      setOverrides(next);
      try {
        localStorage.setItem(STORAGE_KEY, JSON.stringify(next));
      } catch {
        /* storage unavailable: keep in memory only */
      }
    },
    [mode, activeSection, overrides],
  );

  return { isOpen, toggle };
}
