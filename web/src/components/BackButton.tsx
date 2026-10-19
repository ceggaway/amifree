import { useEffect } from "react";
import { useLocation, useNavigate } from "react-router-dom";
import { telegramBackButton } from "../telegram";

/** Where Back goes when there's no earlier page in the app, e.g. a form opened straight
 * from a group link. Pages not listed here have no Back then: Telegram's close button is it. */
export function parentOf(path: string): string | null {
  const groupPage = /^\/g\/([^/]+)\/(new|find|stats)$/.exec(path);
  if (groupPage) return `/g/${groupPage[1]}`;
  const myTimes = /^\/poll\/([^/]+)\/mine$/.exec(path);
  if (myTimes) return `/poll/${myTimes[1]}`;
  return null;
}

/** Back to the previous page. Uses Telegram's own back arrow in the top bar, and falls back
 * to a button in the app's header where Telegram can't show one. */
export function BackButton() {
  const location = useLocation();
  const navigate = useNavigate();
  // React Router numbers each history entry; 0 is the page the app opened on.
  const index = (window.history.state as { idx?: number } | null)?.idx ?? 0;
  const parent = parentOf(location.pathname);
  const canGoBack = index > 0 || parent !== null;
  const native = telegramBackButton();

  useEffect(() => {
    if (!native) return;
    if (!canGoBack) {
      native.hide();
      return;
    }
    const back = () => (index > 0 ? navigate(-1) : navigate(parent!, { replace: true }));
    native.onClick(back);
    native.show();
    return () => native.offClick(back);
  }, [native, canGoBack, index, parent, navigate]);

  if (native || !canGoBack) return null;
  return (
    <button type="button" className="back-button" onClick={() => (index > 0 ? navigate(-1) : navigate(parent!, { replace: true }))}>
      ‹ Back
    </button>
  );
}
