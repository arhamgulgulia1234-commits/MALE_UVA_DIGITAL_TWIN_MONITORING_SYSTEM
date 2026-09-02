import type { ReactNode } from "react";
import { AppNav } from "@/components/layout/AppNav";
import { ToastViewport } from "@/components/ui/ToastViewport";

/** Shared shell for every authenticated route — AppNav mounts here once and persists
 * across navigation instead of remounting per page (the root cause of the nav shift). */
export default function AppLayout({ children }: { children: ReactNode }) {
  return (
    <div className="flex min-h-screen flex-col">
      <AppNav />
      {children}
      <ToastViewport />
    </div>
  );
}
