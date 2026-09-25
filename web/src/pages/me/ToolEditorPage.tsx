import { useQuery } from "@tanstack/react-query";
import { ArrowLeft } from "lucide-react";
import * as React from "react";
import { Link, useNavigate, useParams } from "react-router-dom";

import { getErrorMessage } from "@/api/client";
import { fetchMyTool, myToolDetailQueryKey } from "@/api/me";
import { ConfirmDialog } from "@/components/common/ConfirmDialog";
import { ErrorState } from "@/components/common/ErrorState";
import { PageSkeleton } from "@/components/common/PageSkeleton";
import { ToolForm } from "@/components/me/ToolForm";
import { Button } from "@/components/ui/button";

export interface ToolEditorPageProps {
  mode: "create" | "edit";
}

/**
 * `/me/tools/new` 与 `/me/tools/:id/edit` — 工具编辑器（docs/04 §6.7）。
 *
 * The page owns routing + data loading; `ToolForm` owns the fields and the two
 * save paths. `react-router` 6 has no stable `useBlocker`, so the in-app
 * navigation guard is a `ConfirmDialog` on the actions that leave the page
 * (返回 / 取消) plus a `beforeunload` handler inside the form
 * (see `ToolForm` — a full router blocker is not available in RR6).
 */
export default function ToolEditorPage({ mode }: ToolEditorPageProps) {
  const params = useParams<{ id: string }>();
  const navigate = useNavigate();
  const toolId = mode === "edit" ? Number.parseInt(params.id ?? "", 10) : null;
  const [dirty, setDirty] = React.useState(false);
  const [leaveOpen, setLeaveOpen] = React.useState(false);

  const toolQuery = useQuery({
    queryKey: myToolDetailQueryKey(toolId ?? 0),
    queryFn: ({ signal }) => fetchMyTool(toolId as number, signal),
    enabled: mode === "edit" && toolId !== null && Number.isFinite(toolId),
  });

  function leave(path: string) {
    if (dirty) {
      setLeaveOpen(true);
      return;
    }
    navigate(path);
  }

  if (mode === "edit") {
    if (toolId === null || !Number.isFinite(toolId)) {
      return (
        <div className="mx-auto w-full max-w-3xl px-4 py-10 sm:px-6 lg:px-8">
          <ErrorState title="工具不存在" message="URL 中的工具 ID 无效。" />
        </div>
      );
    }
    if (toolQuery.isError) {
      return (
        <div className="mx-auto w-full max-w-3xl px-4 py-10 sm:px-6 lg:px-8">
          <ErrorState
            message={getErrorMessage(toolQuery.error)}
            onRetry={() => {
              void toolQuery.refetch();
            }}
          />
          <Button asChild variant="ghost" size="sm" className="mt-4 -ml-2">
            <Link to="/me/tools">
              <ArrowLeft aria-hidden="true" className="size-4" />
              返回我的工具
            </Link>
          </Button>
        </div>
      );
    }
    if (toolQuery.isPending || !toolQuery.data) {
      return (
        <div className="mx-auto w-full max-w-3xl px-4 py-10 sm:px-6 lg:px-8">
          <PageSkeleton variant="page" />
        </div>
      );
    }
  }

  return (
    <div className="mx-auto w-full max-w-4xl px-4 py-4 sm:px-6 lg:px-8">
      <Button
        type="button"
        variant="ghost"
        size="sm"
        className="mb-2 -ml-2"
        onClick={() => leave("/me/tools")}
      >
        <ArrowLeft aria-hidden="true" className="size-4" />
        返回我的工具
      </Button>

      <ToolForm
        mode={mode}
        tool={mode === "edit" ? (toolQuery.data ?? null) : null}
        onDirtyChange={setDirty}
        onCreated={(created) => {
          // Later saves become PATCH + version uploads instead of re-creating.
          setDirty(false);
          navigate(`/me/tools/${created.id}/edit`, { replace: true });
        }}
        onSubmitted={() => {
          setDirty(false);
          navigate("/me/tools");
        }}
      />

      <ConfirmDialog
        open={leaveOpen}
        title="离开页面？"
        description="当前有未保存的修改，离开后这些内容会丢失。已保存的草稿不受影响。"
        confirmLabel="放弃修改并离开"
        cancelLabel="继续编辑"
        onOpenChange={setLeaveOpen}
        onConfirm={() => {
          setLeaveOpen(false);
          setDirty(false);
          navigate("/me/tools");
        }}
      />

      {/* In-app navigation is intentionally not blocked globally: react-router 6
          exposes no stable `useBlocker`, and an unguarded `<Link>` elsewhere in
          the shell cannot be intercepted. `beforeunload` in ToolForm covers the
          browser-level close/refresh case (docs/04 §6.7). */}
    </div>
  );
}
