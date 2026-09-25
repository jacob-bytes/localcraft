import * as React from "react";
import { Outlet } from "react-router-dom";

import { PageSkeleton } from "@/components/common/PageSkeleton";
import { TopNav } from "@/components/layout/TopNav";
import { useMeta } from "@/hooks/useMeta";

/**
 * Application shell: sticky top bar + content area + optional footer
 * (docs/04 §5.1). Route-level code splitting (docs/04 §9) means every page
 * arrives through `React.lazy`, so the outlet is wrapped in Suspense here.
 */
export function AppShell() {
  const { data: meta } = useMeta();

  return (
    <div className="flex min-h-screen flex-col bg-background text-foreground">
      <a className="skip-link" href="#main-content">
        跳到主内容
      </a>

      <TopNav />

      <main id="main-content" className="flex-1">
        <React.Suspense fallback={<PageSkeleton variant="page" />}>
          <Outlet />
        </React.Suspense>
      </main>

      <footer className="border-t py-6">
        <div className="mx-auto flex w-full max-w-[1400px] flex-col items-center justify-between gap-2 px-4 text-xs text-muted-foreground sm:flex-row sm:px-6 lg:px-8">
          <span>
            selftool {meta?.app_version ? `v${meta.app_version}` : ""}
            {meta?.api_version ? ` · API ${meta.api_version}` : ""}
          </span>
          <span>内网工具与 Skill 共享平台</span>
        </div>
      </footer>
    </div>
  );
}
