// The only thing taken from Telegram for identity is the raw, signed initData string.
// initDataUnsafe is never read: the server verifies the signature and decides who you are.
type WebApp = {
  initData: string;
  ready: () => void;
  expand: () => void;
};

declare global {
  interface Window {
    Telegram?: { WebApp?: WebApp };
  }
}

export function initData(): string {
  return window.Telegram?.WebApp?.initData ?? "";
}

export function ready() {
  window.Telegram?.WebApp?.ready();
  window.Telegram?.WebApp?.expand();
}
