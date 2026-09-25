import {
  closestCenter,
  DndContext,
  KeyboardSensor,
  PointerSensor,
  useSensor,
  useSensors,
  type Announcements,
  type DragEndEvent,
} from "@dnd-kit/core";
import { restrictToVerticalAxis } from "@dnd-kit/modifiers";
import {
  arrayMove,
  SortableContext,
  sortableKeyboardCoordinates,
  useSortable,
  verticalListSortingStrategy,
} from "@dnd-kit/sortable";
import { CSS } from "@dnd-kit/utilities";
import { ArrowDown, ArrowUp, Ban, CircleCheckBig, GripVertical, Pencil } from "lucide-react";
import * as React from "react";

import type { AdminCategoryOut } from "@/api/types";
import { CATEGORY_ICONS } from "@/components/admin/IconPicker";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import {
  Table,
  TableBody,
  TableCaption,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table";
import { cn } from "@/lib/utils";

/**
 * 分类管理表格（docs/04 §6.15，FR-TAX-01/02）。
 *
 * **排序**：`dnd-kit` 的 `SortableContext` + `useSortable` 做**整行**拖拽，
 * 手柄只是拖拽的 activator（`setActivatorNodeRef`）。同时提供上移/下移按钮，
 * 两条路径都走同一个 `onReorder`（docs/04 §6.15 明确要求键盘可达）。
 *
 * 键盘拖拽：`KeyboardSensor` + `sortableKeyboardCoordinates`，焦点在手柄上时
 * 空格抓起、方向键移动、空格放下、Esc 取消（提示文案见 `announcements`）。
 *
 * 停用（软删除）不是删除：文案一律用「停用」，且停用行同时用 `opacity-60`
 * 和「已停用」徽标两个信号，不靠颜色（docs/04 §8.1）。
 */

export interface CategoryManagerProps {
  categories: readonly AdminCategoryOut[];
  /** 排序请求进行中：禁用拖拽与上移/下移，避免并发提交覆盖。 */
  reorderPending: boolean;
  /** 接收**完整的新顺序**；`sort_order` 的计算在页面层（`(index + 1) * 10`）。 */
  onReorder: (ordered: AdminCategoryOut[]) => void;
  onEdit: (category: AdminCategoryOut) => void;
  onToggleActive: (category: AdminCategoryOut) => void;
}

/** dnd-kit 的屏幕阅读器播报（默认是英文，这里统一成中文）。 */
const ANNOUNCEMENTS: Announcements = {
  onDragStart: ({ active }) =>
    `已抓起分类 #${String(active.id)}。用方向键移动，空格放下，Esc 取消。`,
  onDragOver: ({ active, over }) =>
    over
      ? `分类 #${String(active.id)} 正移动到 #${String(over.id)} 的位置。`
      : `分类 #${String(active.id)} 已离开可放置区域。`,
  onDragEnd: ({ active, over }) =>
    over
      ? `分类 #${String(active.id)} 已放到 #${String(over.id)} 的位置。`
      : `分类 #${String(active.id)} 已放下，顺序未变。`,
  onDragCancel: ({ active }) => `已取消移动分类 #${String(active.id)}。`,
};

const SCREEN_READER_INSTRUCTIONS = {
  draggable:
    "按空格或回车抓起分类，用上下方向键移动到目标位置，再按空格放下；按 Esc 取消。",
};

interface SortableCategoryRowProps {
  category: AdminCategoryOut;
  index: number;
  total: number;
  disabled: boolean;
  onMoveUp: (index: number) => void;
  onMoveDown: (index: number) => void;
  onEdit: (category: AdminCategoryOut) => void;
  onToggleActive: (category: AdminCategoryOut) => void;
}

function SortableCategoryRow({
  category,
  index,
  total,
  disabled,
  onMoveUp,
  onMoveDown,
  onEdit,
  onToggleActive,
}: SortableCategoryRowProps) {
  const {
    attributes,
    listeners,
    setNodeRef,
    setActivatorNodeRef,
    transform,
    transition,
    isDragging,
  } = useSortable({ id: category.id, disabled });

  const Icon = category.icon ? CATEGORY_ICONS[category.icon] : undefined;
  const style: React.CSSProperties = {
    transform: CSS.Transform.toString(transform),
    transition,
  };

  return (
    <TableRow
      ref={setNodeRef}
      style={style}
      data-testid="category-row"
      data-category-id={category.id}
      data-dragging={isDragging ? "true" : undefined}
      className={cn(
        category.is_active ? undefined : "opacity-60",
        isDragging && "relative z-10 bg-muted/60",
      )}
    >
      <TableCell className="w-12">
        <Button
          ref={setActivatorNodeRef}
          type="button"
          variant="ghost"
          size="icon"
          data-testid="category-drag-handle"
          aria-label={`拖拽调整「${category.name}」的排序`}
          disabled={disabled}
          className="cursor-grab touch-none text-muted-foreground active:cursor-grabbing"
          {...attributes}
          {...listeners}
        >
          <GripVertical aria-hidden="true" className="size-4" />
        </Button>
      </TableCell>

      <TableCell className="w-12">
        {Icon ? (
          <span className="inline-flex" title={category.icon ?? undefined}>
            <Icon aria-hidden="true" className="size-4 text-muted-foreground" />
          </span>
        ) : (
          <span
            className="text-xs text-muted-foreground"
            title={category.icon ? `未在白名单内的图标：${category.icon}` : "未设置图标"}
          >
            —
          </span>
        )}
      </TableCell>

      <TableCell>
        <span className="font-medium">{category.name}</span>
      </TableCell>

      <TableCell>
        <code className="font-mono text-xs text-muted-foreground">{category.slug}</code>
      </TableCell>

      <TableCell className="max-w-[18rem]">
        {category.description ? (
          <span className="block max-w-[18rem] truncate" title={category.description}>
            {category.description}
          </span>
        ) : (
          <span className="text-muted-foreground">—</span>
        )}
      </TableCell>

      <TableCell className="text-right tabular-nums">{category.tool_count}</TableCell>
      <TableCell className="text-right tabular-nums">{category.sort_order}</TableCell>

      <TableCell>
        {category.is_active ? (
          <Badge variant="success">启用</Badge>
        ) : (
          <Badge variant="secondary" title="停用后不出现在门户筛选中">
            已停用
          </Badge>
        )}
      </TableCell>

      <TableCell>
        <div className="flex items-center justify-end gap-1">
          <Button
            type="button"
            variant="ghost"
            size="icon"
            data-testid="category-move-up"
            aria-label={`上移「${category.name}」`}
            disabled={disabled || index === 0}
            onClick={() => onMoveUp(index)}
          >
            <ArrowUp aria-hidden="true" className="size-4" />
          </Button>
          <Button
            type="button"
            variant="ghost"
            size="icon"
            data-testid="category-move-down"
            aria-label={`下移「${category.name}」`}
            disabled={disabled || index === total - 1}
            onClick={() => onMoveDown(index)}
          >
            <ArrowDown aria-hidden="true" className="size-4" />
          </Button>
          <Button
            type="button"
            variant="ghost"
            size="sm"
            aria-label={`编辑「${category.name}」`}
            disabled={disabled}
            onClick={() => onEdit(category)}
          >
            <Pencil aria-hidden="true" className="size-4" />
            编辑
          </Button>
          <Button
            type="button"
            variant="ghost"
            size="sm"
            aria-label={category.is_active ? `停用「${category.name}」` : `启用「${category.name}」`}
            disabled={disabled}
            onClick={() => onToggleActive(category)}
          >
            {category.is_active ? (
              <>
                <Ban aria-hidden="true" className="size-4" />
                停用
              </>
            ) : (
              <>
                <CircleCheckBig aria-hidden="true" className="size-4" />
                启用
              </>
            )}
          </Button>
        </div>
      </TableCell>
    </TableRow>
  );
}

export function CategoryManager({
  categories,
  reorderPending,
  onReorder,
  onEdit,
  onToggleActive,
}: CategoryManagerProps) {
  const sensors = useSensors(
    useSensor(PointerSensor, { activationConstraint: { distance: 4 } }),
    useSensor(KeyboardSensor, { coordinateGetter: sortableKeyboardCoordinates }),
  );

  const sortableIds = React.useMemo(() => categories.map((category) => category.id), [categories]);

  const move = React.useCallback(
    (from: number, to: number) => {
      if (from === to || to < 0 || to >= categories.length) return;
      onReorder(arrayMove([...categories], from, to));
    },
    [categories, onReorder],
  );

  const moveUp = React.useCallback((index: number) => move(index, index - 1), [move]);
  const moveDown = React.useCallback((index: number) => move(index, index + 1), [move]);

  const handleDragEnd = React.useCallback(
    (event: DragEndEvent) => {
      const { active, over } = event;
      if (!over || active.id === over.id) return;
      const from = categories.findIndex((category) => category.id === active.id);
      const to = categories.findIndex((category) => category.id === over.id);
      if (from < 0 || to < 0) return;
      onReorder(arrayMove([...categories], from, to));
    },
    [categories, onReorder],
  );

  return (
    <DndContext
      sensors={sensors}
      collisionDetection={closestCenter}
      modifiers={[restrictToVerticalAxis]}
      accessibility={{
        announcements: ANNOUNCEMENTS,
        screenReaderInstructions: SCREEN_READER_INSTRUCTIONS,
      }}
      onDragEnd={handleDragEnd}
    >
      <div className="overflow-hidden rounded-xl border">
        <Table>
          <TableCaption className="sr-only">
            分类列表，可用拖拽手柄或上移、下移按钮调整排序
          </TableCaption>
          <TableHeader className="bg-muted/50">
            <TableRow>
              <TableHead className="w-12">
                <span className="sr-only">拖拽排序</span>
              </TableHead>
              <TableHead className="w-12">图标</TableHead>
              <TableHead>名称</TableHead>
              <TableHead>slug</TableHead>
              <TableHead>描述</TableHead>
              <TableHead className="text-right">工具数</TableHead>
              <TableHead className="text-right">排序值</TableHead>
              <TableHead>状态</TableHead>
              <TableHead className="text-right">操作</TableHead>
            </TableRow>
          </TableHeader>
          <TableBody>
            <SortableContext items={sortableIds} strategy={verticalListSortingStrategy}>
              {categories.map((category, index) => (
                <SortableCategoryRow
                  key={category.id}
                  category={category}
                  index={index}
                  total={categories.length}
                  disabled={reorderPending}
                  onMoveUp={moveUp}
                  onMoveDown={moveDown}
                  onEdit={onEdit}
                  onToggleActive={onToggleActive}
                />
              ))}
            </SortableContext>
          </TableBody>
        </Table>
      </div>
    </DndContext>
  );
}

export default CategoryManager;
