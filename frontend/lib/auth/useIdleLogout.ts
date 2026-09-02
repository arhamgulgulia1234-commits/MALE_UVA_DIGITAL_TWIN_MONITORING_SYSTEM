"use client";

/** Idle-timeout auto-logout: 15 min of no mouse/key/scroll activity signs the operator
 * out, with a 60s countdown warning first so an active-but-idle session isn't cut off
 * without notice. Any listened activity, or the "stay signed in" action, resets it. */
import { useCallback, useEffect, useRef, useState } from "react";
import { useAuthStore } from "./store";

const IDLE_TIMEOUT_MS = 15 * 60 * 1000;
const WARNING_BEFORE_MS = 60 * 1000;
const ACTIVITY_EVENTS = ["mousemove", "keydown", "click", "scroll", "touchstart"] as const;

export function useIdleLogout() {
  const token = useAuthStore((s) => s.token);
  const logout = useAuthStore((s) => s.logout);
  const [secondsLeft, setSecondsLeft] = useState<number | null>(null);
  const warnAt = useRef(0);
  const logoutAt = useRef(0);

  const reset = useCallback(() => {
    const now = Date.now();
    warnAt.current = now + IDLE_TIMEOUT_MS - WARNING_BEFORE_MS;
    logoutAt.current = now + IDLE_TIMEOUT_MS;
    setSecondsLeft(null);
  }, []);

  useEffect(() => {
    if (!token) return;
    reset();
    for (const ev of ACTIVITY_EVENTS) window.addEventListener(ev, reset, { passive: true });
    const tick = setInterval(() => {
      const now = Date.now();
      if (now >= logoutAt.current) {
        logout();
        return;
      }
      setSecondsLeft(now >= warnAt.current ? Math.ceil((logoutAt.current - now) / 1000) : null);
    }, 1000);
    return () => {
      for (const ev of ACTIVITY_EVENTS) window.removeEventListener(ev, reset);
      clearInterval(tick);
    };
  }, [token, reset, logout]);

  return { secondsLeft, stayActive: reset };
}
