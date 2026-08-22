import { createContext, useContext, useMemo, useState, type ReactNode } from "react";

type Theme = "dark" | "light";

type ThemeContextValue = {
  theme: Theme;
  toggleTheme: () => void;
};

const ThemeContext = createContext<ThemeContextValue | null>(null);

function readTheme(): Theme {
  const saved = localStorage.getItem("dashboard_theme");
  if (saved === "dark" || saved === "light") return saved;
  return "dark";
}

function applyTheme(theme: Theme) {
  document.documentElement.setAttribute("data-theme", theme);
  document.body.setAttribute("data-theme", theme);
  localStorage.setItem("dashboard_theme", theme);
}

export function ThemeProvider({ children }: { children: ReactNode }) {
  const [theme, setTheme] = useState<Theme>(() => {
    const next = readTheme();
    applyTheme(next);
    return next;
  });

  const value = useMemo(
    () => ({
      theme,
      toggleTheme: () => {
        const next = theme === "dark" ? "light" : "dark";
        applyTheme(next);
        setTheme(next);
      },
    }),
    [theme],
  );

  return <ThemeContext.Provider value={value}>{children}</ThemeContext.Provider>;
}

export function useTheme() {
  const ctx = useContext(ThemeContext);
  if (!ctx) throw new Error("useTheme must be used within ThemeProvider");
  return ctx;
}
