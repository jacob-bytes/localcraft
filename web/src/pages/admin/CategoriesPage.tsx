import { zodResolver } from "@hookform/resolvers/zod";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { AlertTriangle, FolderTree, Info, Plus, X } from "lucide-react";
import * as React from "react";
import { useForm } from "react-hook-form";
import { toast } from "sonner";
import { z } from "zod";

import {
  adminCategoriesQueryKey,
  createAdminCategory,
  deleteAdminCategory,
  fetchAdminCategories,
  reorderAdminCategories,
  updateAdminCategory,
} from "@/api/admin";
import { getErrorMessage, isApiError } from "@/api/client";
import type {
  AdminCategoryOut,
  AdminCategoryUpdateRequest,
  CategoryInUseDetail,
  CategoryOrderItem,
} from "@/api/types";
import { CategoryManager } from "@/components/admin/CategoryManager";
import { IconPicker } from "@/components/admin/IconPicker";
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
import { Switch } from "@/components/ui/switch";
import { Textarea } from "@/components/ui/textarea";

/**
 * `/admin/categories` —— 分类管理（docs/04 §6.15，FR-TAX-01/02/04）。
 *
 * 三件事都在这一层落地：
 *  1. 拖拽 / 上移下移 → `PUT /admin/categories/order`，**乐观更新 + 失败回滚**；
 *  2. 新建 / 编辑 → `POST` / `PATCH`，成功后 invalidate 分类缓存；
 *  3. 「停用」→ `DELETE`（**软删除**，FR-TAX-02）。被工具引用时后端返回
 *     `409 CATEGORY_IN_USE`，从 `details.tool_count` 取引用数并展示影响面。
 *
 * 后端字段语义（`app/services/admin_taxonomy_service.py`）：
 *  - `icon: null` / `slug: null` 表示**不修改**，所以「清空图标」只能提交空串，
 *    「清空 slug」做不到（slug 有唯一约束，空串会写坏数据）—— 见最终报告缺口。
 *  - `description: ""` 才会清空描述（`null` 同样是不修改）。
 */

const categorySchema = z.object({
  name: z
    .string()
    .trim()
    .min(1, "请填写分类名称")
    .max(64, "分类名称不能超过 64 个字符"),
  slug: z.string().trim().max(64, "slug 不能超过 64 个字符"),
  description: z.string().trim().max(500, "描述不能超过 500 个字"),
  icon: z.string().nullable(),
  sortOrder: z
    .string()
    .trim()
    .regex(/^\d+$/, "排序值必须是非负整数")
    .max(6, "排序值不能超过 6 位"),
  isActive: z.boolean(),
});

type CategoryFormValues = z.infer<typeof categorySchema>;

function toFormValues(
  category: AdminCategoryOut | null,
  nextSortOrder: number,
): CategoryFormValues {
  if (!category) {
    return {
      name: "",
      slug: "",
      description: "",
      icon: null,
      sortOrder: String(nextSortOrder),
      isActive: true,
    };
  }
  return {
    name: category.name,
    slug: category.slug,
    description: category.description ?? "",
    icon: category.icon && category.icon.length > 0 ? category.icon : null,
    sortOrder: String(category.sort_order),
    isActive: category.is_active,
  };
}

/** `409 CATEGORY_IN_USE` 的 `details`（`CategoryInUseDetail`，FR-TAX-02）。 */
function readInUseDetail(error: unknown, fallbackCategoryId: number): CategoryInUseDetail {
  const details = isApiError(error) ? error.details : null;
  const rawCount = details?.["tool_count"];
  const rawId = details?.["category_id"];
  return {
    category_id: typeof rawId === "number" ? rawId : fallbackCategoryId,
    tool_count:
      typeof rawCount === "number" && Number.isFinite(rawCount) ? rawCount : 0,
  };
}

/** 顺序 → `[{id, sort_order}]`：`(index + 1) * 10`，给中间插入留空位。 */
function toOrderItems(ordered: readonly AdminCategoryOut[]): CategoryOrderItem[] {
  return ordered.map((category, index) => ({
    id: category.id,
    sort_order: (index + 1) * 10,
  }));
}

export default function CategoriesPage() {
  const queryClient = useQueryClient();
  const [formOpen, setFormOpen] = React.useState(false);
  const [editing, setEditing] = React.useState<AdminCategoryOut | null>(null);
  const [stopTarget, setStopTarget] = React.useState<AdminCategoryOut | null>(null);
  const [inUse, setInUse] = React.useState<{
    name: string;
    detail: CategoryInUseDetail;
  } | null>(null);

  const categoriesQuery = useQuery({
    queryKey: adminCategoriesQueryKey,
    queryFn: ({ signal }) => fetchAdminCategories(signal),
  });

  const categories = React.useMemo(
    () => categoriesQuery.data ?? [],
    [categoriesQuery.data],
  );

  const form = useForm<CategoryFormValues>({
    resolver: zodResolver(categorySchema),
    defaultValues: toFormValues(null, 0),
  });

  /* ------------------------------------------------------------------ */
  /* 排序：乐观更新 + 失败回滚                                            */
  /* ------------------------------------------------------------------ */

  interface ReorderInput {
    ordered: AdminCategoryOut[];
    items: CategoryOrderItem[];
  }

  const reorderMutation = useMutation({
    mutationFn: (input: ReorderInput) => reorderAdminCategories({ items: input.items }),
    onMutate: (input) => {
      const previous = queryClient.getQueryData<AdminCategoryOut[]>(adminCategoriesQueryKey);
      queryClient.setQueryData(adminCategoriesQueryKey, input.ordered);
      return { previous };
    },
    onError: (error, _input, context) => {
      if (context?.previous) {
        queryClient.setQueryData(adminCategoriesQueryKey, context.previous);
      }
      toast.error(getErrorMessage(error));
    },
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: adminCategoriesQueryKey });
    },
  });

  const handleReorder = React.useCallback(
    (ordered: readonly AdminCategoryOut[]) => {
      const next = ordered.map((category, index) => ({
        ...category,
        sort_order: (index + 1) * 10,
      }));
      reorderMutation.mutate({ ordered: next, items: toOrderItems(next) });
    },
    [reorderMutation],
  );

  /* ------------------------------------------------------------------ */
  /* 新建 / 编辑                                                         */
  /* ------------------------------------------------------------------ */

  const submitMutation = useMutation({
    mutationFn: ({
      values,
      category,
    }: {
      values: CategoryFormValues;
      category: AdminCategoryOut | null;
    }) => {
      const shared = {
        name: values.name,
        sort_order: Number.parseInt(values.sortOrder, 10),
        is_active: values.isActive,
      };
      if (category) {
        // 更新时 `description: ""` / `icon: ""` 才是「清空」；`null` 表示不修改。
        const payload: AdminCategoryUpdateRequest = {
          ...shared,
          description: values.description,
          icon: values.icon ?? "",
          slug: values.slug.length > 0 ? values.slug : null,
        };
        return updateAdminCategory(category.id, payload);
      }
      return createAdminCategory({
        ...shared,
        description: values.description.length > 0 ? values.description : null,
        icon: values.icon,
        slug: values.slug.length > 0 ? values.slug : null,
      });
    },
    onSuccess: (_data, variables) => {
      toast.success(variables.category ? "分类已更新" : "分类已创建");
      void queryClient.invalidateQueries({ queryKey: adminCategoriesQueryKey });
      setFormOpen(false);
      setEditing(null);
    },
    onError: (error) => toast.error(getErrorMessage(error)),
  });

  const openCreate = React.useCallback(() => {
    const nextSortOrder =
      categories.reduce((max, category) => Math.max(max, category.sort_order), 0) + 10;
    setEditing(null);
    form.reset(toFormValues(null, nextSortOrder));
    setFormOpen(true);
  }, [categories, form]);

  const openEdit = React.useCallback(
    (category: AdminCategoryOut) => {
      setEditing(category);
      form.reset(toFormValues(category, 0));
      setFormOpen(true);
    },
    [form],
  );

  /* ------------------------------------------------------------------ */
  /* 停用（软删除）/ 启用                                                 */
  /* ------------------------------------------------------------------ */

  const stopMutation = useMutation({
    mutationFn: (category: AdminCategoryOut) => deleteAdminCategory(category.id),
    onSuccess: () => {
      toast.success("分类已停用");
      void queryClient.invalidateQueries({ queryKey: adminCategoriesQueryKey });
      setStopTarget(null);
    },
    onError: (error, category) => {
      setStopTarget(null);
      if (isApiError(error) && error.is("CATEGORY_IN_USE")) {
        setInUse({ name: category.name, detail: readInUseDetail(error, category.id) });
        return;
      }
      toast.error(getErrorMessage(error));
    },
  });

  const activateMutation = useMutation({
    mutationFn: (category: AdminCategoryOut) =>
      updateAdminCategory(category.id, { is_active: true }),
    onSuccess: () => {
      toast.success("分类已启用");
      void queryClient.invalidateQueries({ queryKey: adminCategoriesQueryKey });
    },
    onError: (error) => toast.error(getErrorMessage(error)),
  });

  const handleToggleActive = React.useCallback(
    (category: AdminCategoryOut) => {
      if (category.is_active) setStopTarget(category);
      else activateMutation.mutate(category);
    },
    [activateMutation],
  );

  return (
    <div className="flex flex-col gap-4" data-testid="categories-page">
      <div className="flex flex-wrap items-baseline justify-between gap-2">
        <div>
          <h1 className="text-xl font-semibold tracking-tight">分类管理</h1>
          <p className="mt-1 text-xs text-muted-foreground">
            分类是门户的一级筛选维度（FR-TAX-01）。拖拽行或使用上移/下移按钮调整顺序，
            顺序与启停会立即影响门户筛选。
          </p>
        </div>
        <Button type="button" size="sm" onClick={openCreate}>
          <Plus aria-hidden="true" className="size-4" />
          新建分类
        </Button>
      </div>

      <Alert>
        <Info aria-hidden="true" />
        <AlertTitle>「停用」不是删除</AlertTitle>
        <AlertDescription>
          停用是软删除（is_active 置为 false，FR-TAX-02）：停用后该分类不再出现在门户筛选里，
          已有工具不受影响，可以随时重新启用。被工具引用的分类无法停用。
        </AlertDescription>
      </Alert>

      {inUse ? (
        <Alert variant="destructive" data-testid="category-in-use-alert">
          <AlertTriangle aria-hidden="true" />
          <AlertTitle>「{inUse.name}」无法停用</AlertTitle>
          <AlertDescription>
            <div className="flex flex-wrap items-center justify-between gap-2">
              <span>
                该分类下有 {inUse.detail.tool_count} 个工具，无法删除。请先移动这些工具。
              </span>
              <Button
                type="button"
                variant="ghost"
                size="sm"
                aria-label="关闭提示"
                onClick={() => setInUse(null)}
              >
                <X aria-hidden="true" className="size-4" />
                关闭
              </Button>
            </div>
          </AlertDescription>
        </Alert>
      ) : null}

      {categoriesQuery.isPending ? (
        <PageSkeleton variant="list" />
      ) : categoriesQuery.isError ? (
        <ErrorState
          message={getErrorMessage(categoriesQuery.error)}
          onRetry={() => void categoriesQuery.refetch()}
        />
      ) : categories.length === 0 ? (
        <EmptyState
          icon={FolderTree}
          title="还没有分类"
          description="创建第一个分类后，门户的筛选栏与工具表单里就能选到它。"
          action={
            <Button type="button" variant="outline" onClick={openCreate}>
              新建分类
            </Button>
          }
        />
      ) : (
        <CategoryManager
          categories={categories}
          reorderPending={reorderMutation.isPending}
          onReorder={handleReorder}
          onEdit={openEdit}
          onToggleActive={handleToggleActive}
        />
      )}

      <Dialog
        open={formOpen}
        onOpenChange={(open) => {
          setFormOpen(open);
          if (!open) setEditing(null);
        }}
      >
        <DialogContent
          data-testid="category-form-dialog"
          className="max-h-[85vh] overflow-y-auto sm:max-w-lg"
        >
          <DialogHeader>
            <DialogTitle>{editing ? "编辑分类" : "新建分类"}</DialogTitle>
            <DialogDescription>
              分类名称唯一；slug 用于门户 URL 与筛选参数，改动会影响已分享的链接。
            </DialogDescription>
          </DialogHeader>

          <Form {...form}>
            <form
              className="grid gap-4"
              onSubmit={form.handleSubmit((values) =>
                submitMutation.mutate({ values, category: editing }),
              )}
            >
              <FormField
                control={form.control}
                name="name"
                render={({ field }) => (
                  <FormItem>
                    <FormLabel>名称</FormLabel>
                    <FormControl>
                      <Input placeholder="如：运维工具" autoComplete="off" {...field} />
                    </FormControl>
                    <FormMessage />
                  </FormItem>
                )}
              />

              <FormField
                control={form.control}
                name="slug"
                render={({ field }) => (
                  <FormItem>
                    <FormLabel>slug（选填）</FormLabel>
                    <FormControl>
                      <Input
                        placeholder="如：ops-tools"
                        autoComplete="off"
                        className="font-mono"
                        {...field}
                      />
                    </FormControl>
                    <FormDescription>
                      留空由服务端生成（中文名会退化成 cat-…）；编辑时留空表示不修改当前
                      slug。最多 64 个字符。
                    </FormDescription>
                    <FormMessage />
                  </FormItem>
                )}
              />

              <FormField
                control={form.control}
                name="description"
                render={({ field }) => (
                  <FormItem>
                    <FormLabel>描述（选填）</FormLabel>
                    <FormControl>
                      <Textarea rows={3} placeholder="一句话说明这个分类放什么工具" {...field} />
                    </FormControl>
                    <FormDescription>最多 500 个字。</FormDescription>
                    <FormMessage />
                  </FormItem>
                )}
              />

              <FormField
                control={form.control}
                name="icon"
                render={({ field }) => (
                  <FormItem>
                    <FormLabel>图标</FormLabel>
                    <FormControl>
                      <IconPicker
                        value={field.value}
                        onChange={field.onChange}
                        disabled={submitMutation.isPending}
                      />
                    </FormControl>
                    <FormDescription>
                      图标名存的是 lucide 图标名（如 server、wrench），门户按同一个名字渲染。
                    </FormDescription>
                    <FormMessage />
                  </FormItem>
                )}
              />

              <FormField
                control={form.control}
                name="sortOrder"
                render={({ field }) => (
                  <FormItem>
                    <FormLabel>排序值</FormLabel>
                    <FormControl>
                      <Input type="number" min={0} step={10} {...field} />
                    </FormControl>
                    <FormDescription>
                      数值越小越靠前。拖拽排序会按「(序号 + 1) × 10」重写所有分类的排序值。
                    </FormDescription>
                    <FormMessage />
                  </FormItem>
                )}
              />

              <FormField
                control={form.control}
                name="isActive"
                render={({ field }) => (
                  <FormItem className="flex flex-row items-center justify-between rounded-lg border p-3">
                    <div className="space-y-0.5">
                      <FormLabel>启用</FormLabel>
                      <FormDescription>
                        停用后该分类不出现在门户筛选中，已有工具不受影响。
                      </FormDescription>
                    </div>
                    <FormControl>
                      <Switch
                        checked={field.value}
                        onCheckedChange={field.onChange}
                        aria-label="启用该分类"
                      />
                    </FormControl>
                  </FormItem>
                )}
              />

              <DialogFooter>
                <Button
                  type="button"
                  variant="outline"
                  onClick={() => {
                    setFormOpen(false);
                    setEditing(null);
                  }}
                >
                  取消
                </Button>
                <Button type="submit" disabled={submitMutation.isPending}>
                  {submitMutation.isPending ? "保存中…" : editing ? "保存" : "创建"}
                </Button>
              </DialogFooter>
            </form>
          </Form>
        </DialogContent>
      </Dialog>

      <ConfirmDialog
        open={stopTarget !== null}
        onOpenChange={(open) => {
          if (!open) setStopTarget(null);
        }}
        title={`停用「${stopTarget?.name ?? ""}」？`}
        description="停用后该分类不再出现在门户筛选里，已有工具不受影响；之后可以随时重新启用。若分类下仍有工具，服务端会拒绝停用。"
        confirmLabel="确认停用"
        pending={stopMutation.isPending}
        onConfirm={() => {
          if (stopTarget) stopMutation.mutate(stopTarget);
        }}
      />
    </div>
  );
}
