import { zodResolver } from "@hookform/resolvers/zod";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Check, Eraser, Info, Merge, Pencil } from "lucide-react";
import * as React from "react";
import { useForm } from "react-hook-form";
import { toast } from "sonner";
import { z } from "zod";

import {
  adminTagsQueryKey,
  cleanupAdminTags,
  fetchAdminTags,
  mergeAdminTags,
  renameAdminTag,
} from "@/api/admin";
import { getErrorMessage } from "@/api/client";
import { tagsQueryKey } from "@/api/tools";
import type { AdminTagOut } from "@/api/types";
import { ConfirmDialog } from "@/components/common/ConfirmDialog";
import { Alert, AlertDescription } from "@/components/ui/alert";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Checkbox } from "@/components/ui/checkbox";
import {
  Command,
  CommandEmpty,
  CommandGroup,
  CommandInput,
  CommandItem,
  CommandList,
} from "@/components/ui/command";
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
import {
  Table,
  TableBody,
  TableCaption,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table";
import { useDebounce } from "@/hooks/useDebounce";
import { formatDateTime, formatRelativeTime } from "@/lib/format";
import { cn } from "@/lib/utils";

/**
 * 标签管理（docs/04 §6.16，FR-TAX-03/05/06）。
 *
 * 三个动作：
 *  - **重命名** `PATCH /admin/tags/{id}`：全站生效，后端同时改归一化名与展示名；
 *  - **合并** `POST /admin/tags/merge`：源标签的引用转移到目标标签，同一工具上的
 *    重复引用由服务端去重（后端 `merge_tags` 会先删冲突关联再转移）；
 *  - **清理零引用** `POST /admin/tags/cleanup`：确认框里的数量只是**已加载页**的
 *    估算，服务端会先重算 `usage_count` 再对全量标签执行，实际数量以响应为准。
 *
 * 合并的目标标签走**服务端前缀搜索**（`fetchAdminTags({ q })`），源标签从当前页
 * 多选（docs/04 §6.16「Dialog 搜索目标标签 + 确认引用转移数量」）。
 */

/** 门户标签缓存前缀（`src/api/tools.ts` 的 `tagsQueryKey(q)`）；失效时连前缀一起失效。 */
const PORTAL_TAGS_CACHE_ROOT = tagsQueryKey("").slice(0, 1);

const renameSchema = z.object({
  displayName: z
    .string()
    .trim()
    .min(1, "请填写标签名")
    .max(64, "标签名不能超过 64 个字符"),
});

type RenameFormValues = z.infer<typeof renameSchema>;

export interface TagManagerProps {
  /** 当前页的标签（服务端按引用数倒序）。 */
  tags: readonly AdminTagOut[];
  /** 过滤条件下的标签总数。 */
  total: number;
}

export function TagManager({ tags, total }: TagManagerProps) {
  const queryClient = useQueryClient();

  const [renameTarget, setRenameTarget] = React.useState<AdminTagOut | null>(null);
  const [mergeOpen, setMergeOpen] = React.useState(false);
  const [sourceQuery, setSourceQuery] = React.useState("");
  const [sources, setSources] = React.useState<readonly AdminTagOut[]>([]);
  const [targetQuery, setTargetQuery] = React.useState("");
  const [target, setTarget] = React.useState<AdminTagOut | null>(null);
  const [cleanupOpen, setCleanupOpen] = React.useState(false);

  const debouncedTargetQuery = useDebounce(targetQuery, 300);

  /** 标签是门户筛选的数据源：管理侧改动后两边缓存都要失效。 */
  const invalidateTags = React.useCallback(() => {
    void queryClient.invalidateQueries({ queryKey: ["admin", "tags"] });
    void queryClient.invalidateQueries({ queryKey: PORTAL_TAGS_CACHE_ROOT });
  }, [queryClient]);

  /* ------------------------------------------------------------------ */
  /* 目标标签搜索（服务端前缀匹配）                                        */
  /* ------------------------------------------------------------------ */

  const targetQueryResult = useQuery({
    queryKey: adminTagsQueryKey(debouncedTargetQuery.trim()),
    queryFn: ({ signal }) => fetchAdminTags({ q: debouncedTargetQuery.trim() }, signal),
    enabled: mergeOpen,
  });

  const targetCandidates = React.useMemo(() => {
    const items = targetQueryResult.data?.items ?? [];
    const sourceIds = new Set(sources.map((tag) => tag.id));
    return items.filter((tag) => !sourceIds.has(tag.id));
  }, [targetQueryResult.data, sources]);

  /* ------------------------------------------------------------------ */
  /* 重命名                                                              */
  /* ------------------------------------------------------------------ */

  const renameForm = useForm<RenameFormValues>({
    resolver: zodResolver(renameSchema),
    defaultValues: { displayName: "" },
  });

  const renameMutation = useMutation({
    mutationFn: ({ tag, displayName }: { tag: AdminTagOut; displayName: string }) =>
      renameAdminTag(tag.id, { display_name: displayName }),
    onSuccess: (updated) => {
      toast.success(`标签已重命名为「${updated.display_name}」`);
      invalidateTags();
      setRenameTarget(null);
      renameForm.reset({ displayName: "" });
    },
    onError: (error) => toast.error(getErrorMessage(error)),
  });

  const openRename = React.useCallback(
    (tag: AdminTagOut) => {
      setRenameTarget(tag);
      renameForm.reset({ displayName: tag.display_name });
    },
    [renameForm],
  );

  /* ------------------------------------------------------------------ */
  /* 合并                                                                */
  /* ------------------------------------------------------------------ */

  const closeMerge = React.useCallback(() => {
    setMergeOpen(false);
    setSourceQuery("");
    setSources([]);
    setTargetQuery("");
    setTarget(null);
  }, []);

  const openMerge = React.useCallback(
    (preselect?: AdminTagOut) => {
      setSourceQuery("");
      setSources(preselect ? [preselect] : []);
      setTargetQuery("");
      setTarget(null);
      setMergeOpen(true);
    },
    [],
  );

  const toggleSource = React.useCallback((tag: AdminTagOut) => {
    setSources((previous) =>
      previous.some((item) => item.id === tag.id)
        ? previous.filter((item) => item.id !== tag.id)
        : [...previous, tag],
    );
    // 目标不能同时是源（后端会直接 422）。
    setTarget((previous) => (previous?.id === tag.id ? null : previous));
  }, []);

  const visibleSources = React.useMemo(() => {
    const keyword = sourceQuery.trim().toLowerCase();
    if (!keyword) return tags;
    return tags.filter(
      (tag) =>
        tag.display_name.toLowerCase().includes(keyword) || tag.name.includes(keyword),
    );
  }, [tags, sourceQuery]);

  const movedReferenceEstimate = sources.reduce((sum, tag) => sum + tag.usage_count, 0);

  const mergeMutation = useMutation({
    mutationFn: () => {
      if (target === null) throw new Error("请选择目标标签");
      return mergeAdminTags({
        source_ids: sources.map((tag) => tag.id),
        target_id: target.id,
      });
    },
    onSuccess: (result) => {
      const deleted =
        result.deleted_tags.length > 0 ? `；删除标签：${result.deleted_tags.join("、")}` : "";
      toast.success(`已合并 ${result.merged_tags} 个标签到「${result.target_name}」`, {
        description: `转移引用 ${result.moved_references} 条，去重 ${result.deduplicated_references} 条${deleted}`,
      });
      invalidateTags();
      closeMerge();
    },
    onError: (error) => toast.error(getErrorMessage(error)),
  });

  /* ------------------------------------------------------------------ */
  /* 清理零引用                                                          */
  /* ------------------------------------------------------------------ */

  const zeroUsageTags = React.useMemo(
    () => tags.filter((tag) => tag.usage_count === 0),
    [tags],
  );

  const cleanupMutation = useMutation({
    mutationFn: () => cleanupAdminTags(),
    onSuccess: (result) => {
      toast.success(`已清理 ${result.deleted} 个零引用标签`, {
        description:
          result.tags.length > 0
            ? `删除：${result.tags.slice(0, 20).join("、")}${result.tags.length > 20 ? "…" : ""}`
            : "服务端重新计算后没有发现零引用标签。",
      });
      invalidateTags();
      setCleanupOpen(false);
    },
    onError: (error) => toast.error(getErrorMessage(error)),
  });

  const cleanupPreviewNames = zeroUsageTags
    .slice(0, 5)
    .map((tag) => tag.display_name)
    .join("、");

  return (
    <div className="flex flex-col gap-3">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <p className="text-xs text-muted-foreground">
          共 {total} 个标签，服务端按引用数倒序返回。清理只针对引用数为 0 的标签。
        </p>
        <div className="flex flex-wrap items-center gap-2">
          <Button
            type="button"
            variant="outline"
            size="sm"
            onClick={() => openMerge()}
            disabled={tags.length === 0}
          >
            <Merge aria-hidden="true" className="size-4" />
            合并标签
          </Button>
          <Button
            type="button"
            variant="outline"
            size="sm"
            data-testid="tag-cleanup-button"
            onClick={() => setCleanupOpen(true)}
          >
            <Eraser aria-hidden="true" className="size-4" />
            清理零引用
          </Button>
        </div>
      </div>

      <div className="overflow-hidden rounded-xl border">
        <Table>
          <TableCaption className="sr-only">标签列表</TableCaption>
          <TableHeader className="bg-muted/50">
            <TableRow>
              <TableHead>标签</TableHead>
              <TableHead className="text-right">引用数</TableHead>
              <TableHead>创建时间</TableHead>
              <TableHead className="text-right">操作</TableHead>
            </TableRow>
          </TableHeader>
          <TableBody>
            {tags.map((tag) => (
              <TableRow key={tag.id} data-testid="tag-row" data-tag-id={tag.id}>
                <TableCell>
                  <div className="flex min-w-0 flex-col">
                    <span className="truncate font-medium">{tag.display_name}</span>
                    <code className="font-mono text-xs text-muted-foreground">{tag.name}</code>
                  </div>
                </TableCell>

                <TableCell className="text-right">
                  <div className="flex items-center justify-end gap-2">
                    {tag.usage_count === 0 ? <Badge variant="secondary">零引用</Badge> : null}
                    <span className="tabular-nums">{tag.usage_count}</span>
                  </div>
                </TableCell>

                <TableCell className="text-xs whitespace-nowrap tabular-nums">
                  <span title={formatDateTime(tag.created_at)}>
                    {formatRelativeTime(tag.created_at)}
                  </span>
                </TableCell>

                <TableCell>
                  <div className="flex items-center justify-end gap-1">
                    <Button
                      type="button"
                      variant="ghost"
                      size="sm"
                      data-testid="tag-rename-button"
                      aria-label={`重命名标签「${tag.display_name}」`}
                      onClick={() => openRename(tag)}
                    >
                      <Pencil aria-hidden="true" className="size-4" />
                      重命名
                    </Button>
                    <Button
                      type="button"
                      variant="ghost"
                      size="sm"
                      data-testid="tag-merge-button"
                      aria-label={`把标签「${tag.display_name}」合并到其他标签`}
                      onClick={() => openMerge(tag)}
                    >
                      <Merge aria-hidden="true" className="size-4" />
                      合并
                    </Button>
                  </div>
                </TableCell>
              </TableRow>
            ))}
          </TableBody>
        </Table>
      </div>

      {/* ---------------- 重命名 ---------------- */}
      <Dialog
        open={renameTarget !== null}
        onOpenChange={(open) => {
          if (!open) {
            setRenameTarget(null);
            renameForm.reset({ displayName: "" });
          }
        }}
      >
        <DialogContent data-testid="tag-rename-dialog" className="sm:max-w-md">
          <DialogHeader>
            <DialogTitle>重命名标签</DialogTitle>
            <DialogDescription>
              重命名会<span className="font-medium text-foreground">全站生效</span>
              ：所有引用该标签的工具都会显示新名称，门户筛选与工具标签同步更新。
            </DialogDescription>
          </DialogHeader>

          {renameTarget ? (
            <p className="text-xs text-muted-foreground">
              当前标签：
              <span className="font-medium text-foreground">{renameTarget.display_name}</span>
              （归一化名 <code className="font-mono">{renameTarget.name}</code>，被{" "}
              {renameTarget.usage_count} 个工具引用）
            </p>
          ) : null}

          <Form {...renameForm}>
            <form
              className="grid gap-4"
              onSubmit={renameForm.handleSubmit((values) => {
                if (!renameTarget) return;
                renameMutation.mutate({ tag: renameTarget, displayName: values.displayName });
              })}
            >
              <FormField
                control={renameForm.control}
                name="displayName"
                render={({ field }) => (
                  <FormItem>
                    <FormLabel>新的展示名</FormLabel>
                    <FormControl>
                      <Input placeholder="如：Python" autoComplete="off" {...field} />
                    </FormControl>
                    <FormDescription>
                      标签名会做归一化（去首尾空格、转小写、全角转半角）。如果归一化后与已有标签
                      冲突，请改用「合并」。
                    </FormDescription>
                    <FormMessage />
                  </FormItem>
                )}
              />

              <DialogFooter>
                <Button
                  type="button"
                  variant="outline"
                  onClick={() => {
                    setRenameTarget(null);
                    renameForm.reset({ displayName: "" });
                  }}
                >
                  取消
                </Button>
                <Button type="submit" disabled={renameMutation.isPending}>
                  {renameMutation.isPending ? "保存中…" : "保存"}
                </Button>
              </DialogFooter>
            </form>
          </Form>
        </DialogContent>
      </Dialog>

      {/* ---------------- 合并 ---------------- */}
      <Dialog
        open={mergeOpen}
        onOpenChange={(open) => {
          if (!open) closeMerge();
        }}
      >
        <DialogContent
          data-testid="tag-merge-dialog"
          className="max-h-[85vh] overflow-y-auto sm:max-w-2xl"
        >
          <DialogHeader>
            <DialogTitle>合并标签</DialogTitle>
            <DialogDescription>
              把源标签的引用全部转移到目标标签，源标签随后会被删除。此操作不可撤销。
            </DialogDescription>
          </DialogHeader>

          <div className="grid gap-4">
            <section className="grid gap-2">
              <p className="text-sm font-medium">1. 选择要合并掉的源标签（可多选）</p>
              <Input
                value={sourceQuery}
                onChange={(event) => setSourceQuery(event.target.value)}
                placeholder="在当前列表里过滤标签"
                aria-label="过滤源标签"
              />
              <div className="max-h-44 overflow-y-auto rounded-md border p-1">
                {visibleSources.length === 0 ? (
                  <p className="px-2 py-4 text-center text-xs text-muted-foreground">
                    当前列表里没有匹配的标签。可先搜索定位到目标页后再合并。
                  </p>
                ) : (
                  visibleSources.map((tag) => (
                    <label
                      key={tag.id}
                      className={cn(
                        "flex cursor-pointer items-center gap-2 rounded-md px-2 py-1.5 text-sm",
                        "hover:bg-accent hover:text-accent-foreground",
                      )}
                    >
                      <Checkbox
                        checked={sources.some((item) => item.id === tag.id)}
                        onCheckedChange={() => toggleSource(tag)}
                        aria-label={`选择源标签 ${tag.display_name}`}
                      />
                      <span className="min-w-0 flex-1 truncate">{tag.display_name}</span>
                      <code className="font-mono text-xs text-muted-foreground">{tag.name}</code>
                      <span className="text-xs tabular-nums text-muted-foreground">
                        {tag.usage_count}
                      </span>
                    </label>
                  ))
                )}
              </div>
              <p className="text-xs text-muted-foreground">
                已选 <span className="font-medium tabular-nums">{sources.length}</span> 个源标签，
                将转移的引用数量约{" "}
                <span className="font-medium tabular-nums">{movedReferenceEstimate}</span> 条
                （源标签引用数之和，实际以服务端结果为准）。
              </p>
            </section>

            <section className="grid gap-2">
              <p className="text-sm font-medium">2. 选择目标标签</p>
              <Command shouldFilter={false} className="rounded-md border">
                <CommandInput
                  value={targetQuery}
                  onValueChange={setTargetQuery}
                  placeholder="搜索目标标签（前缀匹配）"
                  aria-label="搜索目标标签"
                />
                <CommandList className="max-h-44">
                  <CommandEmpty>
                    {targetQueryResult.isFetching ? "搜索中…" : "没有匹配的标签"}
                  </CommandEmpty>
                  <CommandGroup>
                    {targetCandidates.map((tag) => (
                      <CommandItem
                        key={tag.id}
                        value={`${tag.name} ${tag.display_name}`}
                        data-testid="tag-merge-target"
                        onSelect={() => setTarget(tag)}
                      >
                        <Check
                          aria-hidden="true"
                          className={cn(
                            "size-4",
                            target?.id === tag.id ? "opacity-100" : "opacity-0",
                          )}
                        />
                        <span className="min-w-0 flex-1 truncate">{tag.display_name}</span>
                        <code className="font-mono text-xs text-muted-foreground">{tag.name}</code>
                        <span className="text-xs tabular-nums text-muted-foreground">
                          {tag.usage_count}
                        </span>
                      </CommandItem>
                    ))}
                  </CommandGroup>
                </CommandList>
              </Command>
              <p className="text-xs text-muted-foreground">
                目标标签：
                {target ? (
                  <span className="font-medium text-foreground">
                    {target.display_name}（<code className="font-mono">{target.name}</code>）
                  </span>
                ) : (
                  "未选择"
                )}
              </p>
            </section>

            <Alert>
              <Info aria-hidden="true" />
              <AlertDescription>
                同一工具上同时打了源标签与目标标签时，重复引用会被去重，不会产生重复关联。
              </AlertDescription>
            </Alert>
          </div>

          <DialogFooter>
            <Button type="button" variant="outline" onClick={closeMerge}>
              取消
            </Button>
            <Button
              type="button"
              disabled={sources.length === 0 || target === null || mergeMutation.isPending}
              onClick={() => mergeMutation.mutate()}
            >
              {mergeMutation.isPending ? "合并中…" : "确认合并"}
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>

      {/* ---------------- 清理零引用 ---------------- */}
      <ConfirmDialog
        open={cleanupOpen}
        onOpenChange={setCleanupOpen}
        title="清理零引用标签？"
        description={
          <>
            将清理当前列表中{" "}
            <span className="font-medium text-foreground">{zeroUsageTags.length}</span> 个零引用标签
            {zeroUsageTags.length > 0
              ? `（${cleanupPreviewNames}${zeroUsageTags.length > 5 ? "…" : ""}）`
              : ""}
            。这只是<span className="font-medium text-foreground">已加载页</span>
            的估算：确认后服务端会先重算全量标签的引用数再删除，实际删除数量以结果为准。
          </>
        }
        confirmLabel="确认清理"
        pending={cleanupMutation.isPending}
        onConfirm={() => cleanupMutation.mutate()}
      />
    </div>
  );
}

export default TagManager;
