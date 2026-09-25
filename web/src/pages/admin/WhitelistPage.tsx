import { zodResolver } from "@hookform/resolvers/zod";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { AlertTriangle, Info, Plus } from "lucide-react";
import * as React from "react";
import { useForm } from "react-hook-form";
import { toast } from "sonner";
import { z } from "zod";

import {
  addWhitelistEntry,
  fetchSettings,
  fetchWhitelist,
  removeWhitelistEntry,
  SETTING_KEYS,
  settingsQueryKey,
  whitelistQueryKey,
} from "@/api/admin";
import { getErrorMessage } from "@/api/client";
import type { WhitelistEntry } from "@/api/types";
import { WhitelistTable } from "@/components/admin/WhitelistTable";
import { ConfirmDialog } from "@/components/common/ConfirmDialog";
import { EmptyState } from "@/components/common/EmptyState";
import { ErrorState } from "@/components/common/ErrorState";
import { PageSkeleton } from "@/components/common/PageSkeleton";
import { Alert, AlertDescription, AlertTitle } from "@/components/ui/alert";
import { Button } from "@/components/ui/button";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import {
  Form,
  FormControl,
  FormDescription,
  FormField,
  FormItem,
  FormLabel,
  FormMessage,
} from "@/components/ui/form";
import { Input } from "@/components/ui/input";

/**
 * `/admin/whitelist` —— 免审白名单（docs/04 §6.17，FR-APPR-03/04）。
 *
 * 契约缺口：M2 冻结清单里**没有用户检索接口**（用户/用户组管理是 M3，
 * CONTRACT §6.1）。因此添加表单只能填数字 user_id，并显式提示用户。
 * 后端补上 `GET /admin/users?q=` 之后，这里应换成搜索选择器。
 */

/** 与服务端 `WhitelistEntryInput` 对齐：user_id 正整数，原因为可空字符串。 */
const whitelistSchema = z.object({
  userId: z
    .string()
    .trim()
    .min(1, "请填写用户 ID")
    .regex(/^\d+$/, "用户 ID 必须是数字")
    .refine((value) => Number.parseInt(value, 10) > 0, "用户 ID 必须大于 0"),
  reason: z
    .string()
    .trim()
    .max(500, "原因不能超过 500 个字")
    .optional()
    .or(z.literal("")),
  expiresAt: z.string().optional(),
});

type WhitelistFormValues = z.infer<typeof whitelistSchema>;

/** `type="date"` 取到的是本地日期，转成当天 23:59:59 的 ISO（UTC）。 */
function dateInputToIso(value: string | undefined): string | null {
  if (!value) return null;
  const date = new Date(`${value}T23:59:59`);
  return Number.isNaN(date.getTime()) ? null : date.toISOString();
}

export default function WhitelistPage() {
  const queryClient = useQueryClient();
  const [dialogOpen, setDialogOpen] = React.useState(false);
  const [removeTarget, setRemoveTarget] = React.useState<WhitelistEntry | null>(null);

  const whitelistQuery = useQuery({
    queryKey: whitelistQueryKey,
    queryFn: ({ signal }) => fetchWhitelist(signal),
  });

  // 总开关状态从系统设置读取（docs/03 §3.13），不在前端硬编码。
  const settingsQuery = useQuery({
    queryKey: settingsQueryKey,
    queryFn: ({ signal }) => fetchSettings(signal),
    staleTime: 60_000,
  });
  const whitelistEnabledSetting = settingsQuery.data?.items.find(
    (item) => item.key === SETTING_KEYS.whitelistEnabled,
  );
  // 读不到设置时按「开启」处理，避免设置接口失败就谎报功能已关闭。
  const whitelistEnabled =
    whitelistEnabledSetting === undefined ? true : whitelistEnabledSetting.value === true;

  const form = useForm<WhitelistFormValues>({
    resolver: zodResolver(whitelistSchema),
    defaultValues: { userId: "", reason: "", expiresAt: "" },
  });

  const addMutation = useMutation({
    mutationFn: (values: WhitelistFormValues) =>
      addWhitelistEntry({
        user_id: Number.parseInt(values.userId, 10),
        reason: values.reason?.trim() ? values.reason.trim() : null,
        expires_at: dateInputToIso(values.expiresAt),
      }),
    onSuccess: () => {
      toast.success("已加入免审白名单");
      void queryClient.invalidateQueries({ queryKey: whitelistQueryKey });
      setDialogOpen(false);
      form.reset({ userId: "", reason: "", expiresAt: "" });
    },
    onError: (error) => {
      // 重复加入返回 409 DUPLICATE_ENTRY —— 把服务端消息直接展示给用户。
      toast.error(getErrorMessage(error));
    },
  });

  const removeMutation = useMutation({
    mutationFn: (userId: number) => removeWhitelistEntry(userId),
    onSuccess: () => {
      toast.success("已移出白名单");
      void queryClient.invalidateQueries({ queryKey: whitelistQueryKey });
      setRemoveTarget(null);
    },
    onError: (error) => toast.error(getErrorMessage(error)),
  });

  const entries = whitelistQuery.data ?? [];

  return (
    <div className="flex flex-col gap-4">
      <div className="flex flex-wrap items-baseline justify-between gap-2">
        <h1 className="text-xl font-semibold tracking-tight">免审白名单</h1>
        <Button type="button" size="sm" onClick={() => setDialogOpen(true)}>
          <Plus aria-hidden="true" className="size-4" />
          添加用户
        </Button>
      </div>

      <Alert>
        <Info aria-hidden="true" />
        <AlertTitle>白名单说明</AlertTitle>
        <AlertDescription>
          白名单内的用户上传工具与版本时将跳过审批，直接发布。此功能受系统设置
          「免审白名单」总开关控制。
        </AlertDescription>
      </Alert>

      {whitelistEnabled ? null : (
        <Alert variant="destructive" data-testid="whitelist-warning">
          <AlertTriangle aria-hidden="true" />
          <AlertTitle>白名单功能当前已关闭，名单不生效</AlertTitle>
          <AlertDescription>
            请到「系统设置 → 审批 → 免审白名单」打开总开关后再依赖此名单。
          </AlertDescription>
        </Alert>
      )}

      {whitelistQuery.isPending ? (
        <PageSkeleton variant="list" />
      ) : whitelistQuery.isError ? (
        <ErrorState
          message={getErrorMessage(whitelistQuery.error)}
          onRetry={() => void whitelistQuery.refetch()}
        />
      ) : entries.length === 0 ? (
        <EmptyState
          title="白名单为空"
          description="添加用户后，其后续提交将跳过审批直接发布（受总开关控制）。"
          action={
            <Button type="button" variant="outline" onClick={() => setDialogOpen(true)}>
              添加用户
            </Button>
          }
        />
      ) : (
        <WhitelistTable entries={entries} onRemoveRequest={setRemoveTarget} />
      )}

      <Dialog
        open={dialogOpen}
        onOpenChange={(open) => {
          setDialogOpen(open);
          if (!open) form.reset({ userId: "", reason: "", expiresAt: "" });
        }}
      >
        <DialogContent>
          <DialogHeader>
            <DialogTitle>添加免审用户</DialogTitle>
            <DialogDescription>
              名单内用户提交时跳过审批，直接发布。请确认已核对用户身份。
            </DialogDescription>
          </DialogHeader>

          <Form {...form}>
            <form
              className="grid gap-4"
              onSubmit={form.handleSubmit((values) => addMutation.mutate(values))}
            >
              {/* 契约缺口：M2 没有用户搜索接口，只能填数字 ID（见文件头注释）。 */}
              <FormField
                control={form.control}
                name="userId"
                render={({ field }) => (
                  <FormItem>
                    <FormLabel>用户 ID</FormLabel>
                    <FormControl>
                      <Input type="number" inputMode="numeric" min={1} placeholder="如 42" {...field} />
                    </FormControl>
                    <FormDescription>
                      当前 M2 版本暂不提供用户搜索接口，请填写用户 ID。
                    </FormDescription>
                    <FormMessage />
                  </FormItem>
                )}
              />

              <FormField
                control={form.control}
                name="reason"
                render={({ field }) => (
                  <FormItem>
                    <FormLabel>加入原因（选填）</FormLabel>
                    <FormControl>
                      <Input placeholder="如：CI 机器人账号，发布流程已人工审核" {...field} />
                    </FormControl>
                    <FormMessage />
                  </FormItem>
                )}
              />

              <FormField
                control={form.control}
                name="expiresAt"
                render={({ field }) => (
                  <FormItem>
                    <FormLabel>过期时间（选填）</FormLabel>
                    <FormControl>
                      <Input type="date" {...field} />
                    </FormControl>
                    <FormDescription>留空表示永不过期。</FormDescription>
                    <FormMessage />
                  </FormItem>
                )}
              />

              <DialogFooter>
                <Button type="button" variant="outline" onClick={() => setDialogOpen(false)}>
                  取消
                </Button>
                <Button type="submit" disabled={addMutation.isPending}>
                  {addMutation.isPending ? "添加中…" : "添加"}
                </Button>
              </DialogFooter>
            </form>
          </Form>
        </DialogContent>
      </Dialog>

      <ConfirmDialog
        open={removeTarget !== null}
        onOpenChange={(open) => {
          if (!open) setRemoveTarget(null);
        }}
        title={`移除「${removeTarget?.display_name ?? removeTarget?.username ?? ""}」的免审资格？`}
        description="移除后该用户之后提交的工具与版本将重新进入审批队列，已经发布的工具不受影响。"
        confirmLabel="确认移除"
        pending={removeMutation.isPending}
        onConfirm={() => {
          if (removeTarget) removeMutation.mutate(removeTarget.user_id);
        }}
      />
    </div>
  );
}
