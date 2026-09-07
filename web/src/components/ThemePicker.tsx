import { useEffect, useRef, useState } from "react";
import { Check, Palette } from "lucide-react";
import { applyTheme, readStoredTheme, storeTheme, THEMES, type ThemeId } from "../theme";

/**
 * Switches the room the sheet sits in.
 *
 * The swatch is a miniature of the theme rather than a colour dot: the same
 * ground, the same page on top of it, the same metal rule. A dot would tell
 * you the hue and nothing about what the screen becomes.
 */
export default function ThemePicker({ tone = "dark" }: { tone?: "dark" | "light" }) {
  const [open, setOpen] = useState(false);
  const [theme, setTheme] = useState<ThemeId>(readStoredTheme);
  const root = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (!open) return;
    const dismiss = (event: MouseEvent) => {
      if (!root.current?.contains(event.target as Node)) setOpen(false);
    };
    const escape = (event: KeyboardEvent) => {
      if (event.key === "Escape") setOpen(false);
    };
    document.addEventListener("mousedown", dismiss);
    document.addEventListener("keydown", escape);
    return () => {
      document.removeEventListener("mousedown", dismiss);
      document.removeEventListener("keydown", escape);
    };
  }, [open]);

  function choose(id: ThemeId) {
    applyTheme(id);
    storeTheme(id);
    setTheme(id);
    setOpen(false);
  }

  return (
    <div className={`theme-picker theme-picker-${tone}`} ref={root}>
      <button
        className="theme-trigger"
        aria-haspopup="menu"
        aria-expanded={open}
        title="Masanın temasını değiştir"
        onClick={() => setOpen((value) => !value)}
      >
        <Palette size={18} />
      </button>
      {open && (
        <div className="theme-menu" role="menu">
          <p className="theme-menu-title">Masanın teması</p>
          {THEMES.map((option) => (
            <button
              key={option.id}
              role="menuitemradio"
              aria-checked={option.id === theme}
              className={`theme-option${option.id === theme ? " selected" : ""}`}
              onClick={() => choose(option.id)}
            >
              <span className={`theme-swatch theme-swatch-${option.id}`} aria-hidden="true">
                <span className="theme-swatch-page" />
              </span>
              <span className="theme-option-text">
                <strong>{option.name}</strong>
                <small>{option.blurb}</small>
              </span>
              {option.id === theme && <Check size={15} className="theme-option-mark" />}
            </button>
          ))}
        </div>
      )}
    </div>
  );
}
