"use client";

/**
 * Keyboard shortcuts (design §10). `/` and ⌘K live in the command bar itself;
 * these are the page-level ones.
 *
 * Every handler is suppressed while a text field has focus — otherwise typing
 * "space" in the search box would pause the set.
 */

import { useEffect } from "react";

export function isTyping(): boolean {
  const el = document.activeElement;
  return (
    el instanceof HTMLInputElement ||
    el instanceof HTMLTextAreaElement ||
    el instanceof HTMLSelectElement ||
    Boolean((el as HTMLElement | null)?.isContentEditable)
  );
}

export function useKeyboardShortcuts(handlers: Record<string, () => void>) {
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (isTyping() || e.metaKey || e.ctrlKey || e.altKey) return;
      const key = e.key === " " ? "space" : e.key;
      const handler = handlers[key];
      if (!handler) return;
      e.preventDefault();
      handler();
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [handlers]);
}
