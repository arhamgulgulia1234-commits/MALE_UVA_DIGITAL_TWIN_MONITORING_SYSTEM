"use client";

/**
 * Mounts a single ReconnectingSocket for the lifetime of the app and feeds every frame
 * into the zustand store. Call once, near the root (see app/page.tsx) — components should
 * read telemetry from useTelemetryStore, not call this hook themselves.
 */
import { useEffect } from "react";
import { ReconnectingSocket } from "@/lib/websocket";
import { useTelemetryStore } from "@/lib/store";
import type { TelemetryFrame } from "@/lib/types";

const WS_URL = process.env.NEXT_PUBLIC_WS_URL ?? "ws://localhost:8000/ws/telemetry";

export function useTelemetryStream(): void {
  const pushFrame = useTelemetryStore((s) => s.pushFrame);
  const setStatus = useTelemetryStore((s) => s.setStatus);

  useEffect(() => {
    const socket = new ReconnectingSocket<TelemetryFrame>({
      url: WS_URL,
      onMessage: (frame) => pushFrame(frame),
      onStatusChange: (status) => setStatus(status),
    });
    socket.connect();
    return () => socket.close();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);
}
