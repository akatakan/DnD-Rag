/**
 * The room the character sheet sits in.
 *
 * A theme changes the table, the light and the metal — never the sheet. The
 * panels stay vellum in every theme, which is both the stronger idea (the game
 * is a book open on a table, and the theme is the room around it) and the only
 * safe one: most of styles.css still paints panel internals with literal
 * colours, so a theme that darkened panels would strand text on them.
 */

export type ThemeId = "vellum" | "ebon" | "feywild" | "hoard";

export type Theme = {
  id: ThemeId;
  name: string;
  blurb: string;
};

export const THEMES: readonly Theme[] = [
  {
    id: "vellum",
    name: "Tomar",
    blurb: "Gün ışığında açılmış bir cilt. Altın ve mürekkep.",
  },
  {
    id: "ebon",
    name: "Kara Mahzen",
    blurb: "Mum ışığında taş bir masa. Gece oturumları için.",
  },
  {
    id: "feywild",
    name: "Yaban Diyar",
    blurb: "Alacakaranlıkta bir açıklık. Yeşim ve menekşe.",
  },
  {
    id: "hoard",
    name: "Ejderha Hazinesi",
    blurb: "Köz ve pirinç. Sıcak, ağır, tehlikeli.",
  },
];

export const DEFAULT_THEME: ThemeId = "vellum";

const STORAGE_KEY = "dnd-table-theme";

function isThemeId(value: unknown): value is ThemeId {
  return THEMES.some((theme) => theme.id === value);
}

export function readStoredTheme(): ThemeId {
  try {
    const stored = localStorage.getItem(STORAGE_KEY);
    if (isThemeId(stored)) return stored;
  } catch {
    // A privacy-restricted browser simply gets the default.
  }
  return DEFAULT_THEME;
}

/** Paint the theme. Called at boot before render, so there is no flash. */
export function applyTheme(id: ThemeId): void {
  document.documentElement.dataset.theme = id;
}

export function storeTheme(id: ThemeId): void {
  try {
    localStorage.setItem(STORAGE_KEY, id);
  } catch {
    // The choice still holds for this page session.
  }
}
