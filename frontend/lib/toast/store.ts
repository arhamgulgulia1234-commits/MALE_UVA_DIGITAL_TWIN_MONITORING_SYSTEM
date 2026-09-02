/** Shared toast store — the one place a failed API call surfaces to the operator
 * instead of a console-only failure. Rendered once by components/ui/ToastViewport.tsx
 * (mounted in every route's shared layout), pushed to from anywhere via `toastError`. */
import { create } from "zustand";

export type ToastTone = "error" | "info";

export interface Toast {
  id: number;
  tone: ToastTone;
  message: string;
}

interface ToastState {
  toasts: Toast[];
  push: (tone: ToastTone, message: string) => void;
  dismiss: (id: number) => void;
}

let nextId = 1;

export const useToastStore = create<ToastState>((set) => ({
  toasts: [],
  push: (tone, message) =>
    set((s) => ({ toasts: [...s.toasts, { id: nextId++, tone, message }] })),
  dismiss: (id) => set((s) => ({ toasts: s.toasts.filter((t) => t.id !== id) })),
}));

/** Fire-and-forget: `.catch(toastError("Could not start mission"))` or call directly. */
export function toastError(message: string): (err?: unknown) => void {
  return () => useToastStore.getState().push("error", message);
}

export function pushToast(tone: ToastTone, message: string): void {
  useToastStore.getState().push(tone, message);
}
