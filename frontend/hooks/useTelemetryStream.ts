"use client";

/**
 * Mounts a single ReconnectingSocket for the lifetime of the app and feeds every frame
 * into the zustand store. Call once, near the root (see app/page.tsx) — components should
 * read telemetry from useTelemetryStore, not call this hook themselves.
 */
import { useEffect } from "react";
import { ReconnectingSocket } from "@/lib/websocket";
import { useTelemetryStore } from "@/lib/store";
import { useFleetStore } from "@/lib/fleet/store";
import type { TelemetryFrame } from "@/lib/types";

const WS_URL = process.env.NEXT_PUBLIC_WS_URL ?? "ws://localhost:8000/ws/telemetry";

/**
 * Auth token, when the backend has TELEMETRY_AUTH_ENABLED=true. Empty for the default
 * open-demo configuration, in which case the URL is left exactly as configured.
 */
const AUTH_TOKEN = process.env.NEXT_PUBLIC_TELEMETRY_TOKEN ?? "";

/**
 * A browser cannot set an Authorization header on a WebSocket handshake, so the backend
 * also accepts the token as a `?token=` query parameter (see app/core/security.py).
 * Without this the REST controls authenticate fine while the telemetry socket is closed
 * with 1008 on every attempt — the dashboard renders, the controls work, and the stream
 * sits in "reconnecting" forever with no indication that a token is the problem.
 */
function authorizedUrl(url: string): string {
  if (!AUTH_TOKEN) return url;
  const separator = url.includes("?") ? "&" : "?";
  return `${url}${separator}token=${encodeURIComponent(AUTH_TOKEN)}`;
}

/**
 * Phase 6: the socket is scoped to one UAV (`/ws/telemetry?uav_id=...`) — the backend
 * only broadcasts that UAV's frames to it. `uavId` is a hook dependency below, so
 * switching the fleet selector tears down the old socket and opens a fresh one against
 * the newly selected UAV, rather than leaving the dashboard subscribed to whichever
 * engine was picked when the page first mounted.
 */
function uavScopedUrl(uavId: string): string {
  const separator = WS_URL.includes("?") ? "&" : "?";
  return authorizedUrl(`${WS_URL}${separator}uav_id=${encodeURIComponent(uavId)}`);
}

export function useTelemetryStream(): void {
  const pushFrame = useTelemetryStore((s) => s.pushFrame);
  const setStatus = useTelemetryStore((s) => s.setStatus);
  const uavId = useFleetStore((s) => s.selectedUavId);

  useEffect(() => {
    const socket = new ReconnectingSocket<TelemetryFrame>({
      url: uavScopedUrl(uavId),
      onMessage: (frame) => pushFrame(frame),
      onStatusChange: (status) => setStatus(status),
    });
    socket.connect();
    return () => socket.close();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [uavId]);
}
