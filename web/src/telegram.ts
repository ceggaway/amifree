// The only thing taken from Telegram for identity is the raw, signed initData string.
// initDataUnsafe is never read: the server verifies the signature and decides who you are.
type WebApp = {
  initData: string;
  ready: () => void;
  expand: () => void;
  close?: () => void;
  HapticFeedback?: { notificationOccurred: (kind: "success" | "error" | "warning") => void };
};

declare global {
  interface Window {
    Telegram?: { WebApp?: WebApp };
  }
}

export function initData(): string {
  return window.Telegram?.WebApp?.initData ?? "";
}

/** Where a link into the app points, e.g. "poll_<id>" from t.me/<bot>/<app>?startapp=poll_<id>.
 * Read from the signed initData string, and only used to choose a page: the server still
 * decides who you are and what you may see. */
export function startParam(): string {
  return new URLSearchParams(initData()).get("start_param") ?? "";
}

export function ready() {
  window.Telegram?.WebApp?.ready();
  window.Telegram?.WebApp?.expand();
}

/** A small buzz on success or failure, where the phone supports it. */
export function haptic(kind: "success" | "error" | "warning") {
  try {
    window.Telegram?.WebApp?.HapticFeedback?.notificationOccurred(kind);
  } catch {
    // Older Telegram versions: no feedback, nothing else changes.
  }
}

/** Back to the chat, e.g. after posting a card there. Does nothing in a plain browser. */
export function closeApp() {
  window.Telegram?.WebApp?.close?.();
}
