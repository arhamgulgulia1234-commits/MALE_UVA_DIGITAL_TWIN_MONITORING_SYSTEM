"use client";

/**
 * Mounts a single ReconnectingSocket for the lifetime of the app and feeds every frame
 * into the zustand store. Call once, near the root (see app/page.tsx) — components should
 * read telemetry from useTelemetryStore, not call this hook themselves.
 */
import { useEffect } from "react";
import { getAuthToken } from "@/lib/auth/store";
import { ReconnectingSocket } from "@/lib/websocket";
import { useTelemetryStore } from "@/lib/store";
import { useFleetStore } from "@/lib/fleet/store";
import type { TelemetryFrame } from "@/lib/types";

const WS_URL = process.env.NEXT_PUBLIC_WS_URL ?? "ws://localhost:8000/ws/telemetry";

/**
 * A browser cannot set an Authorization header on a WebSocket handshake, so the backend
 * also accepts the JWT as a `?token=` query parameter (see app/auth/deps.py::
 * websocket_user). AuthGate only mounts the dashboard once a session token exists, so
 * this always has one to attach by the time a socket actually opens.
 */
function authorizedUrl(url: string): string {
  const token = getAuthToken();
  if (!token) return url;
  const separator = url.includes("?") ? "&" : "?";
  return `${url}${separator}token=${encodeURIComponent(token)}`;
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
