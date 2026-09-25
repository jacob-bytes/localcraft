import { zodResolver } from "@hookform/resolvers/zod";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { ChevronLeft, ChevronRight, Download, HardDrive, KeyRound, Package, Pencil } from "lucide-react";
import * as React from "react";
import { useForm } from "react-hook-form";
import { Link } from "react-router-dom";
import { toast } from "sonner";
import { z } from "zod";

import { getErrorMessage } from "@/api/client";
import {
  fetchMyDownloads,
  fetchProfile,
  myDownloadsQueryKey,
  profileQueryKey,
  updateProfile,
} from "@/api/me";
import type { DownloadLogItem, Profile, Role } from "@/api/types";
import { CopyButton } from "@/components/common/CopyButton";
import { EmptyState } from "@/components/common/EmptyState";
import { ErrorState } from "@/components/common/ErrorState";
import { PageSkeleton } from "@/components/common/PageSkeleton";
import { StorageUsageBar } from "@/components/common/StorageUsageBar";
import { Avatar, AvatarFallback } from "@/components/ui/avatar";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import {
  Dialog,
  DialogContent,
  DialogDescription,
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
import { formatDateTime, formatFileSize, formatRelativeTime, initials } from "@/lib/format";
import { ROLE_LABELS } from "@/lib/permissions";

const downloadsSchema = z.object({
  display_name: z.string().trim().min(1, "请填写显示名").max(128, "显示名最多 128 个字符"),
  email: z
    .string()
    .trim()
    .max(255, "邮箱最多 255 个字符")
    .email("请输入有效的邮箱地址")
    .or(z.literal("")),
});

type DownloadsFormValues = z.infer<typeof downloadsSchema>;

/** Role badge tones; the badge always carries the text label (docs/04 §8.1). */
const ROLE_VARIANTS: Record<Role, "default" | "secondary" | "warning" | "success"> = {
  superadmin: "default",
  approver: "success",
  user: "secondary",
  viewer: "warning",
};

/**
 * `/me` — 个人资料（docs/04 §6.5）。
 *
 * `GET /me/profile` embeds `usage`, so the storage card comes from the same
 * request; `GET /me/stats` is only a fallback for the rare case of a null usage
 * payload.
 */
export default function ProfilePage() {
  const queryClient = useQueryClient();
  const [editOpen, setEditOpen] = React.useState(false);
  const [page, setPage] = React.useState(1);

  const profileQuery = useQuery({
    queryKey: profileQueryKey,
    queryFn: ({ signal }) => fetchProfile(signal),
  });

  const downloadsQuery = useQuery({
    queryKey: myDownloadsQueryKey(page),
    queryFn: ({ signal }) => fetchMyDownloads(page, 10, signal),
    placeholderData: (previous) => previous,
  });

  const profile = profileQuery.data;

  if (profileQuery.isError) {
    return (
      <div className="mx-auto w-full max-w-4xl px-4 py-10 sm:px-6 lg:px-8">
        <ErrorState
          message={getErrorMessage(profileQuery.error)}
          onRetry={() => {
            void profileQuery.refetch();
          }}
        />
      </div>
    );
  }

  if (profileQuery.isPending || !profile) {
    return (
      <div className="mx-auto w-full max-w-4xl px-4 py-10 sm:px-6 lg:px-8">
        <PageSkeleton variant="page" />
      </div>
    );
  }

  const usage = profile.usage;
  const downloads = downloadsQuery.data;

  return (
    <div
      data-testid="profile-page"
      className="mx-auto w-full max-w-4xl space-y-6 px-4 py-6 sm:px-6 lg:px-8"
    >
      <header>
        <h1 className="text-2xl font-semibold tracking-tight">个人中心</h1>
        <p className="mt-1 text-sm text-muted-foreground">
          查看账号信息、存储用量与最近下载记录。
        </p>
      </header>

      {/* ---- 账号信息卡 ---- */}
      <Card>
        <CardHeader>
          <CardTitle>账号信息</CardTitle>
          <CardDescription>用户名创建后不可修改，显示名与邮箱可自行编辑。</CardDescription>
        </CardHeader>
        <CardContent className="space-y-4">
          <div className="flex flex-wrap items-start gap-4">
            <Avatar className="size-16">
              <AvatarFallback className="bg-primary/10 text-xl font-medium text-primary">
                {initials(profile.display_name || profile.username)}
              </AvatarFallback>
            </Avatar>

            <div className="min-w-0 flex-1 space-y-1">
              <p className="text-lg font-semibold">{profile.display_name}</p>
              <p className="font-mono text-sm text-muted-foreground">{profile.username}</p>
              <p className="text-sm text-muted-foreground">{profile.email ?? "未填写邮箱"}</p>
              <div className="flex flex-wrap gap-1.5 pt-1">
                {profile.roles.length === 0 ? (
                  <Badge variant="outline">无角色</Badge>
                ) : (
                  profile.roles.map((role) => (
                    <Badge key={role} variant={ROLE_VARIANTS[role]}>
                      {ROLE_LABELS[role]}
                    </Badge>
                  ))
                )}
              </div>
            </div>

            <div className="flex flex-col items-end gap-2">
              <Button type="button" variant="outline" onClick={() => setEditOpen(true)}>
                <Pencil aria-hidden="true" className="size-4" />
                编辑资料
              </Button>
              <p className="text-xs text-muted-foreground">
                注册时间：
                <time dateTime={profile.created_at ?? undefined}>
                  {formatDateTime(profile.created_at)}
                </time>
              </p>
            </div>
          </div>

          <div className="flex flex-wrap items-center gap-2 border-t pt-4">
            <Button asChild variant="outline" size="sm">
              <Link to="/change-password">
                <KeyRound aria-hidden="true" className="size-4" />
                修改密码
              </Link>
            </Button>
            <span className="text-xs text-muted-foreground">
              修改后当前会话失效，需要用新密码重新登录。
            </span>
          </div>
        </CardContent>
      </Card>

      {/* ---- 存储用量卡 ---- */}
      <Card>
        <CardHeader>
          <CardTitle className="flex items-center gap-2">
            <HardDrive aria-hidden="true" className="size-4" />
            存储用量
          </CardTitle>
          <CardDescription>个人配额按上传的版本文件累计计算（FR-FILE-12）。</CardDescription>
        </CardHeader>
        <CardContent className="space-y-4" data-testid="storage-usage">
          {usage ? (
            <StorageUsageBar
              usedBytes={usage.used_bytes}
              quotaBytes={usage.quota_bytes}
              hint={`平台已用 ${formatFileSize(usage.platform_used_bytes)} / ${formatFileSize(
                usage.total_quota_bytes,
              )}`}
            />
          ) : (
            <p className="text-sm text-muted-foreground">暂无用量的数据。</p>
          )}

          <dl className="grid grid-cols-2 gap-3 sm:grid-cols-4">
            <StatItem label="我的工具数" value={usage?.tool_count ?? 0} icon={Package} />
            <StatItem label="我的版本数" value={usage?.version_count ?? 0} icon={Package} />
            <StatItem label="已发布" value={usage?.published_tool_count ?? 0} icon={Package} />
            <StatItem label="累计下载" value={usage?.download_count ?? 0} icon={Download} />
          </dl>
        </CardContent>
      </Card>

      {/* ---- 我的下载 ---- */}
      <Card>
        <CardHeader>
          <CardTitle>我的下载</CardTitle>
          <CardDescription>最近下载过的工具，点击名称可直接回到详情页。</CardDescription>
        </CardHeader>
        <CardContent className="space-y-3">
          {downloadsQuery.isError ? (
            <ErrorState
              message={getErrorMessage(downloadsQuery.error)}
              onRetry={() => {
                void downloadsQuery.refetch();
              }}
            />
          ) : downloadsQuery.isPending ? (
            <PageSkeleton variant="list" count={4} />
          ) : (downloads?.items.length ?? 0) === 0 ? (
            <EmptyState
              icon={Download}
              title="还没有下载记录"
              description="下载过的工具与版本会出现在这里，方便快速找回。"
            />
          ) : (
            <>
              <ul className="divide-y rounded-lg border">
                {(downloads?.items ?? []).map((item) => (
                  <DownloadRow key={item.id} item={item} />
                ))}
              </ul>

              {downloads && downloads.pages > 1 ? (
                <nav className="flex items-center justify-between" aria-label="下载记录分页">
                  <Button
                    type="button"
                    variant="outline"
                    size="sm"
                    disabled={page <= 1}
                    onClick={() => setPage((current) => Math.max(1, current - 1))}
                  >
                    <ChevronLeft aria-hidden="true" className="size-4" />
                    上一页
                  </Button>
                  <span className="text-xs text-muted-foreground">
                    第 {downloads.page} / {downloads.pages} 页 · 共 {downloads.total} 条
                  </span>
                  <Button
                    type="button"
                    variant="outline"
                    size="sm"
                    disabled={page >= downloads.pages}
                    onClick={() => setPage((current) => current + 1)}
                  >
                    下一页
                    <ChevronRight aria-hidden="true" className="size-4" />
                  </Button>
                </nav>
              ) : null}
            </>
          )}
        </CardContent>
      </Card>

      <EditProfileDialog
        open={editOpen}
        onOpenChange={setEditOpen}
        profile={profile}
        onSaved={() => {
          void queryClient.invalidateQueries({ queryKey: profileQueryKey });
        }}
      />
    </div>
  );
}

function StatItem({
  label,
  value,
  icon: Icon,
}: {
  label: string;
  value: number;
  icon: typeof Package;
}) {
  return (
    <div className="rounded-lg border bg-muted/30 px-3 py-2">
      <dt className="flex items-center gap-1.5 text-xs text-muted-foreground">
        <Icon aria-hidden="true" className="size-3.5" />
        {label}
      </dt>
      <dd className="mt-0.5 text-xl font-semibold tabular-nums">{value}</dd>
    </div>
  );
}

function DownloadRow({ item }: { item: DownloadLogItem }) {
  const name = item.tool_name ?? "已删除的工具";
  return (
    <li className="flex flex-wrap items-center gap-2 px-3 py-2 text-sm">
      <div className="min-w-0 flex-1">
        {item.tool_slug ? (
          <Link
            to={`/tools/${item.tool_slug}`}
            className="truncate font-medium text-primary underline-offset-4 hover:underline"
          >
            {name}
          </Link>
        ) : (
          <span className="truncate font-medium">{name}</span>
        )}
        <p className="truncate text-xs text-muted-foreground">
          <span className="font-mono">{item.version ?? "—"}</span>
          <span aria-hidden="true"> · </span>
          {item.file_name ?? "文件名不可用"}
        </p>
      </div>
      <span className="text-xs text-muted-foreground">{formatFileSize(item.file_size)}</span>
      <time
        dateTime={item.created_at ?? undefined}
        title={formatDateTime(item.created_at)}
        className="w-24 text-right text-xs text-muted-foreground"
      >
        {formatRelativeTime(item.created_at)}
      </time>
    </li>
  );
}

/** 编辑资料对话框（FR-AUTH-11：仅显示名与邮箱）。 */
function EditProfileDialog({
  open,
  onOpenChange,
  profile,
  onSaved,
}: {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  profile: Profile;
  onSaved: () => void;
}) {
  const form = useForm<DownloadsFormValues>({
    resolver: zodResolver(downloadsSchema),
    defaultValues: { display_name: profile.display_name, email: profile.email ?? "" },
    mode: "onSubmit",
  });

  React.useEffect(() => {
    if (open) {
      form.reset({ display_name: profile.display_name, email: profile.email ?? "" });
    }
  }, [open, profile, form]);

  const mutation = useMutation({
    mutationFn: (values: DownloadsFormValues) =>
      updateProfile({
        display_name: values.display_name.trim(),
        email: values.email.trim() === "" ? null : values.email.trim(),
      }),
    onSuccess: () => {
      toast.success("资料已更新");
      onSaved();
      onOpenChange(false);
    },
    onError: (error) => {
      toast.error(getErrorMessage(error));
    },
  });

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="sm:max-w-md">
        <DialogHeader>
          <DialogTitle>编辑资料</DialogTitle>
          <DialogDescription>用户名不可修改；邮箱用于接收平台通知（可选）。</DialogDescription>
        </DialogHeader>

        <Form {...form}>
          <form
            className="space-y-4"
            noValidate
            onSubmit={(event) => {
              void form.handleSubmit((values) => mutation.mutate(values))(event);
            }}
          >
            <FormField
              control={form.control}
              name="display_name"
              render={({ field }) => (
                <FormItem>
                  <FormLabel>显示名</FormLabel>
                  <FormControl>
                    <Input {...field} maxLength={128} disabled={mutation.isPending} />
                  </FormControl>
                  <FormDescription>显示在工具卡片、审批记录与顶栏上。</FormDescription>
                  <FormMessage />
                </FormItem>
              )}
            />

            <FormField
              control={form.control}
              name="email"
              render={({ field }) => (
                <FormItem>
                  <FormLabel>邮箱</FormLabel>
                  <FormControl>
                    <Input
                      {...field}
                      type="email"
                      autoComplete="email"
                      disabled={mutation.isPending}
                    />
                  </FormControl>
                  <FormMessage />
                </FormItem>
              )}
            />

            <div className="flex items-center gap-2">
              <Button type="submit" disabled={mutation.isPending}>
                {mutation.isPending ? "保存中…" : "保存"}
              </Button>
              <Button
                type="button"
                variant="outline"
                disabled={mutation.isPending}
                onClick={() => onOpenChange(false)}
              >
                取消
              </Button>
              <CopyButton
                value={profile.username}
                label="复制用户名"
                className="ml-auto"
                onCopied={() => toast.success("用户名已复制")}
              />
            </div>
          </form>
        </Form>
      </DialogContent>
    </Dialog>
  );
}
