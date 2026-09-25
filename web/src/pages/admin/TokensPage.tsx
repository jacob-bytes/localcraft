import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Info, KeyRound, Plus, TriangleAlert } from "lucide-react";
import * as React from "react";
import { useSearchParams } from "react-router-dom";
import { toast } from "sonner";

import {
  createToken,
  deleteTokenRecord,
  fetchTokens,
  overviewQueryKey,
  revokeToken,
  tokensQueryKey,
} from "@/api/admin";
import { getErrorMessage } from "@/api/client";
import type { ApiScope, ApiTokenCreateResponse, ApiTokenOut } from "@/api/types";
import { TokenManager } from "@/components/admin/TokenManager";
import { TokenSecretDialog } from "@/components/admin/TokenSecretDialog";
import { EmptyState } from "@/components/common/EmptyState";
import { ErrorState } from "@/components/common/ErrorState";
import { PageSkeleton } from "@/components/common/PageSkeleton";
import { Pagination } from "@/components/common/Pagination";
import { Alert, AlertDescription, AlertTitle } from "@/components/ui/alert";
import {
  AlertDialog,
  AlertDialogAction,
  AlertDialogCancel,
  AlertDialogContent,
  AlertDialogDescription,
  AlertDialogFooter,
  AlertDialogHeader,
  AlertDialogTitle,
} from "@/components/ui/alert-dialog";
import { Button } from "@/components/ui/button";
import { Checkbox } from "@/components/ui/checkbox";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { RadioGroup, RadioGroupItem } from "@/components/ui/radio-group";
import { Switch } from "@/components/ui/switch";
import { Tooltip, TooltipContent, TooltipTrigger } from "@/components/ui/tooltip";

/**
 * `/admin/tokens` —— API Token 管理（docs/04 §6.18、docs/03 §3.12）。
 *
 * 安全：明文 Token 与一次性密码一样**只展示一次**，只存在于 React state，
 * 绝不写 localStorage、URL、日志或 console（CONTRACT §3.1 的同一原则）。
 *
 * 契约缺口：`POST /admin/tokens/{id}/revoke` **不接受请求体**，所以 docs/04
 * §6.18 的「吊销理由选填」无处提交 —— 这里不收集理由（收集了也只能静默丢弃，
 * 那比不收集更糟），作为缺口上报监控方。
 */

/** Scope → 说明。覆盖后端 `ApiScope` 全部 8 个取值（docs/03 §1.4）。 */
const SCOPE_OPTIONS: ReadonlyArray<{ scope: ApiScope; description: string }> = [
  { scope: "tools:read", description: "读取全部工具、版本与统计数据（含未发布的元信息）。" },
  { scope: "tools:write", description: "创建 / 更新工具与版本，上传文件（需要 tools:read 配合使用）。" },
  { scope: "approvals:write", description: "审批队列：批准、驳回、下架与重新上架工具。" },
  { scope: "users:write", description: "用户管理：创建用户、改角色、重置密码、强制下线。" },
  { scope: "groups:write", description: "用户组管理：建组、改成员、删除组。" },
  { scope: "taxonomy:write", description: "分类与标签管理：改名、排序、合并、清理。" },
  { scope: "settings:write", description: "修改系统设置（含审批模式、配额与安全参数）。" },
  { scope: "admin:all", description: "包含全部其他权限。仅在你确实需要一个全能脚本时勾选。" },
];

const ALL_SCOPES: readonly ApiScope[] = SCOPE_OPTIONS.map((option) => option.scope);

/** `type="date"` 取到的是本地日期；按当天 23:59:59 转成 ISO（UTC）提交。 */
function dateInputToIso(value: string): string | null {
  if (!value) return null;
  const date = new Date(`${value}T23:59:59`);
  return Number.isNaN(date.getTime()) ? null : date.toISOString();
}

export default function TokensPage() {
  const queryClient = useQueryClient();
  const [searchParams, setSearchParams] = useSearchParams();

  // `include_revoked` 默认开（docs/04 §6.18），并写进 URL 便于分享与刷新。
  const includeRevoked = searchParams.get("include_revoked") !== "0";
  const parsedPage = Number.parseInt(searchParams.get("page") ?? "1", 10);
  const page = Number.isFinite(parsedPage) && parsedPage >= 1 ? parsedPage : 1;

  const tokensQuery = useQuery({
    queryKey: tokensQueryKey(includeRevoked),
    queryFn: ({ signal }) => fetchTokens({ include_revoked: includeRevoked, page }, signal),
  });

  const [createOpen, setCreateOpen] = React.useState(false);
  const [secret, setSecret] = React.useState<ApiTokenCreateResponse | null>(null);
  const [revokeTarget, setRevokeTarget] = React.useState<ApiTokenOut | null>(null);
  const [deleteTarget, setDeleteTarget] = React.useState<ApiTokenOut | null>(null);

  // ---- 签发表单（受控 state；提交前做本地校验） ----
  const [name, setName] = React.useState("");
  const [scopes, setScopes] = React.useState<ApiScope[]>([]);
  const [expiryMode, setExpiryMode] = React.useState<"never" | "date">("never");
  const [expiryDate, setExpiryDate] = React.useState("");
  const [formError, setFormError] = React.useState<string | null>(null);

  function resetForm() {
    setName("");
    setScopes([]);
    setExpiryMode("never");
    setExpiryDate("");
    setFormError(null);
  }

  const createMutation = useMutation({
    mutationFn: (payload: { name: string; scopes: ApiScope[]; expiresAt: string | null }) =>
      createToken({
        name: payload.name,
        scopes: payload.scopes,
        expires_at: payload.expiresAt,
      }),
    onSuccess: (response) => {
      setCreateOpen(false);
      resetForm();
      // 明文只交给一次性的 TokenSecretDialog；列表接口永不返回明文。
      setSecret(response);
      void queryClient.invalidateQueries({ queryKey: ["admin", "tokens"] });
      void queryClient.invalidateQueries({ queryKey: overviewQueryKey });
    },
    onError: (error) => setFormError(getErrorMessage(error)),
  });

  const revokeMutation = useMutation({
    mutationFn: (tokenId: number) => revokeToken(tokenId),
    onSuccess: () => {
      toast.success("Token 已吊销，立即失效");
      setRevokeTarget(null);
      void queryClient.invalidateQueries({ queryKey: ["admin", "tokens"] });
      void queryClient.invalidateQueries({ queryKey: overviewQueryKey });
    },
    onError: (error) => toast.error(getErrorMessage(error)),
  });

  const deleteMutation = useMutation({
    mutationFn: (tokenId: number) => deleteTokenRecord(tokenId),
    onSuccess: () => {
      toast.success("已删除 Token 记录");
      setDeleteTarget(null);
      void queryClient.invalidateQueries({ queryKey: ["admin", "tokens"] });
      void queryClient.invalidateQueries({ queryKey: overviewQueryKey });
    },
    onError: (error) => toast.error(getErrorMessage(error)),
  });

  function handleCreate() {
    const trimmed = name.trim();
    if (!trimmed) {
      setFormError("请填写名称");
      return;
    }
    if (scopes.length === 0) {
      setFormError("请至少勾选一个权限范围");
      return;
    }
    const expiresAt = expiryMode === "never" ? null : dateInputToIso(expiryDate);
    if (expiryMode === "date" && expiresAt === null) {
      setFormError("请选择有效的过期日期");
      return;
    }
    setFormError(null);
    createMutation.mutate({ name: trimmed, scopes, expiresAt });
  }

  function toggleScope(scope: ApiScope, checked: boolean) {
    setScopes((current) =>
      checked
        ? ALL_SCOPES.filter((item) => item === scope || current.includes(item))
        : current.filter((item) => item !== scope),
    );
  }

  const tokens = tokensQuery.data?.items ?? [];

  return (
    <div data-testid="tokens-page" className="flex flex-col gap-4">
      <div className="flex flex-wrap items-baseline justify-between gap-2">
        <h1 className="text-xl font-semibold tracking-tight">API Token</h1>
        <Button type="button" size="sm" data-testid="token-create-button" onClick={() => setCreateOpen(true)}>
          <Plus aria-hidden="true" className="size-4" />
          签发 Token
        </Button>
      </div>

      <Alert>
        <Info aria-hidden="true" />
        <AlertTitle>关于 API Token</AlertTitle>
        <AlertDescription>
          API Token 用于脚本与自动化工具调用管理接口。Token 明文仅在创建时显示一次，请妥善保存。
        </AlertDescription>
      </Alert>

      <div className="flex flex-wrap items-center gap-3">
        <div className="flex items-center gap-2">
          <Switch
            id="tokens-include-revoked"
            checked={includeRevoked}
            onCheckedChange={(checked) => {
              setSearchParams(
                (current) => {
                  const next = new URLSearchParams(current);
                  if (checked) next.delete("include_revoked");
                  else next.set("include_revoked", "0");
                  next.delete("page");
                  return next;
                },
                { replace: true },
              );
            }}
          />
          <Label htmlFor="tokens-include-revoked" className="text-sm">
            显示已吊销的 Token
          </Label>
        </div>
        <span className="text-xs text-muted-foreground">
          共 {tokensQuery.data?.total ?? 0} 个
        </span>
      </div>

      {tokensQuery.isPending ? (
        <PageSkeleton variant="list" count={4} />
      ) : tokensQuery.isError ? (
        <ErrorState
          message={getErrorMessage(tokensQuery.error)}
          onRetry={() => void tokensQuery.refetch()}
        />
      ) : tokens.length === 0 ? (
        <EmptyState
          icon={KeyRound}
          title="还没有 API Token"
          description="签发后可在 CI 或脚本里用 Authorization: Bearer <token> 调用管理接口。"
          action={
            <Button type="button" variant="outline" onClick={() => setCreateOpen(true)}>
              签发 Token
            </Button>
          }
        />
      ) : (
        <>
          <TokenManager
            tokens={tokens}
            onRevokeRequest={setRevokeTarget}
            onDeleteRequest={setDeleteTarget}
          />
          <Pagination
            page={page}
            pages={tokensQuery.data?.pages ?? 1}
            onPageChange={(nextPage) => {
              setSearchParams(
                (current) => {
                  const next = new URLSearchParams(current);
                  if (nextPage > 1) next.set("page", String(nextPage));
                  else next.delete("page");
                  return next;
                },
                { replace: true },
              );
            }}
          />
        </>
      )}

      {/* ---- 签发 Dialog ---- */}
      <Dialog
        open={createOpen}
        onOpenChange={(open) => {
          setCreateOpen(open);
          if (!open) resetForm();
        }}
      >
        <DialogContent data-testid="token-create-dialog" className="max-w-xl">
          <DialogHeader>
            <DialogTitle>签发 API Token</DialogTitle>
            <DialogDescription>
              权限范围应是完成工作所需的最小集合；Token 权限不能超过你自身的角色权限。
            </DialogDescription>
          </DialogHeader>

          <div className="space-y-4">
            <div className="space-y-2">
              <Label htmlFor="token-name">名称 *</Label>
              <Input
                id="token-name"
                value={name}
                maxLength={128}
                placeholder="如：CI 发布脚本"
                onChange={(event) => setName(event.target.value)}
              />
            </div>

            <fieldset className="space-y-2">
              <legend className="text-sm font-medium">权限范围 *</legend>
              <div className="grid gap-2 sm:grid-cols-2">
                {SCOPE_OPTIONS.map((option) => {
                  const scopeId = `token-scope-${option.scope.replace(/[^a-z]/g, "-")}`;
                  return (
                    <Tooltip key={option.scope}>
                      <TooltipTrigger asChild>
                        <div className="flex items-center gap-2 rounded-md border p-2">
                          <Checkbox
                            id={scopeId}
                            data-testid="token-scope-option"
                            checked={scopes.includes(option.scope)}
                            onCheckedChange={(checked) =>
                              toggleScope(option.scope, checked === true)
                            }
                          />
                          <Label htmlFor={scopeId} className="font-mono text-xs">
                            {option.scope}
                          </Label>
                        </div>
                      </TooltipTrigger>
                      <TooltipContent side="top" className="max-w-xs">
                        {option.description}
                      </TooltipContent>
                    </Tooltip>
                  );
                })}
              </div>
              <p className="flex items-start gap-1.5 text-xs text-muted-foreground">
                <TriangleAlert aria-hidden="true" className="mt-0.5 size-3.5 text-amber-500" />
                勾选 admin:all 将包含全部其他权限。
              </p>
              {scopes.includes("admin:all") ? (
                <Alert variant="warning" data-testid="token-admin-all-warning">
                  <TriangleAlert aria-hidden="true" />
                  <AlertTitle>已包含全部其他权限</AlertTitle>
                  <AlertDescription>
                    admin:all 覆盖上面全部 Scope；再单独勾选其它 Scope 已无意义，建议只保留它。
                  </AlertDescription>
                </Alert>
              ) : null}
            </fieldset>

            <fieldset className="space-y-2">
              <legend className="text-sm font-medium">过期时间</legend>
              <RadioGroup
                value={expiryMode}
                onValueChange={(value) => setExpiryMode(value === "date" ? "date" : "never")}
                className="flex flex-wrap items-center gap-4"
              >
                <span className="flex items-center gap-2">
                  <RadioGroupItem id="token-expiry-never" value="never" />
                  <Label htmlFor="token-expiry-never">永不过期</Label>
                </span>
                <span className="flex items-center gap-2">
                  <RadioGroupItem id="token-expiry-date" value="date" />
                  <Label htmlFor="token-expiry-date">指定日期</Label>
                </span>
                {expiryMode === "date" ? (
                  <Input
                    type="date"
                    aria-label="过期日期"
                    className="w-40"
                    value={expiryDate}
                    onChange={(event) => setExpiryDate(event.target.value)}
                  />
                ) : null}
              </RadioGroup>
            </fieldset>

            {formError ? (
              <p role="alert" className="text-sm text-destructive">
                {formError}
              </p>
            ) : null}
          </div>

          <DialogFooter>
            <Button type="button" variant="outline" onClick={() => setCreateOpen(false)}>
              取消
            </Button>
            <Button type="button" disabled={createMutation.isPending} onClick={handleCreate}>
              {createMutation.isPending ? "签发中…" : "签发"}
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>

      {/* ---- 一次性明文 ---- */}
      {secret ? (
        <TokenSecretDialog
          tokenName={secret.name}
          token={secret.token}
          warning={secret.warning}
          onClose={() => {
            setSecret(null);
            // 清掉 mutation 缓存里的响应，明文不在内存里多留一秒（CONTRACT §3.1）。
            createMutation.reset();
          }}
        />
      ) : null}

      {/* ---- 吊销（保留审计痕迹） ---- */}
      <AlertDialog
        open={revokeTarget !== null}
        onOpenChange={(open) => {
          if (!open) setRevokeTarget(null);
        }}
      >
        <AlertDialogContent data-testid="token-revoke-dialog">
          <AlertDialogHeader>
            <AlertDialogTitle>吊销「{revokeTarget?.name ?? ""}」？</AlertDialogTitle>
            <AlertDialogDescription>
              吊销后该 Token 立即失效，正在使用它的脚本会立刻收到 401；记录会保留，可在列表中查看状态。
              {/* 契约缺口：吊销接口不接受请求体，无法提交理由（见文件头注释）。 */}
            </AlertDialogDescription>
          </AlertDialogHeader>
          <AlertDialogFooter>
            <AlertDialogCancel>取消</AlertDialogCancel>
            <AlertDialogAction
              data-testid="token-revoke-confirm"
              className="bg-destructive text-destructive-foreground hover:bg-destructive/90"
              disabled={revokeMutation.isPending}
              onClick={(event) => {
                event.preventDefault();
                if (revokeTarget) revokeMutation.mutate(revokeTarget.id);
              }}
            >
              {revokeMutation.isPending ? "吊销中…" : "确认吊销"}
            </AlertDialogAction>
          </AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>

      {/* ---- 删除记录（物理删除，无审计痕迹） ---- */}
      <AlertDialog
        open={deleteTarget !== null}
        onOpenChange={(open) => {
          if (!open) setDeleteTarget(null);
        }}
      >
        <AlertDialogContent>
          <AlertDialogHeader>
            <AlertDialogTitle>删除「{deleteTarget?.name ?? ""}」的记录？</AlertDialogTitle>
            <AlertDialogDescription>
              这是物理删除，记录将从列表中消失，且不保留任何审计痕迹。要保留审计痕迹请用「吊销」。
            </AlertDialogDescription>
          </AlertDialogHeader>
          <AlertDialogFooter>
            <AlertDialogCancel>取消</AlertDialogCancel>
            <AlertDialogAction
              data-testid="token-delete-confirm"
              className="bg-destructive text-destructive-foreground hover:bg-destructive/90"
              disabled={deleteMutation.isPending}
              onClick={(event) => {
                event.preventDefault();
                if (deleteTarget) deleteMutation.mutate(deleteTarget.id);
              }}
            >
              {deleteMutation.isPending ? "删除中…" : "确认删除"}
            </AlertDialogAction>
          </AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>
    </div>
  );
}
