import { Moon, Sun } from "lucide-react";
import { Button } from "@/components/ui/button";
import type { Theme } from "@/lib/theme";

export interface ThemeToggleProps {
  theme: Theme;
  onToggle: () => void;
}

/**
 * A single light/dark toggle. Deliberately low-emphasis (ghost variant,
 * icon-only) -- never competing with a screen's one primary action, per
 * the brief's "every other control is visually subordinate" rule.
 */
export function ThemeToggle({ theme, onToggle }: ThemeToggleProps) {
  return (
    <Button
      type="button"
      variant="ghost"
      size="icon"
      aria-label={theme === "dark" ? "Switch to light mode" : "Switch to dark mode"}
      data-testid="theme-toggle"
      onClick={onToggle}
    >
      {theme === "dark" ? <Sun className="h-5 w-5" /> : <Moon className="h-5 w-5" />}
    </Button>
  );
}
