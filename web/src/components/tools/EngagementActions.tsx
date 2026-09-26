import { Star, ThumbsUp } from "lucide-react";

import { readCount } from "@/api/engagement";
import type { ToolDetail, ToolListItem } from "@/api/types";
import { Button } from "@/components/ui/button";
import { useAuth } from "@/hooks/useAuth";
import { useFavoriteToggle, useLikeToggle } from "@/hooks/useEngagement";
import { formatCount } from "@/lib/format";
import { cn } from "@/lib/utils";

/**
 * M8 · 收藏 / 点赞的展示与切换（任务书 F7、F8）。
 *
 * 三条硬约束：
 *
 * 1. **匿名不显示切换按钮，只显示计数**（F7.1）—— 与「登录后可下载」同一套匿名
 *    策略：服务端对匿名恒返回 `is_favorited: false`，所以按钮在这里直接不渲染，
 *    而不是渲染一个点了没反应的控件。
 * 2. **切换控件用 `aria-pressed` 表达状态**，并且**不只靠颜色**：收藏是
 *    「实心星 + 已收藏」对「空心星 + 收藏」，点赞同理（无障碍硬约束 4）。
 * 3. **计数缺失时不渲染数字**。真实后端尚未落地 M8 时 `favorite_count` 是
 *    `undefined`，`formatCount(undefined)` 会渲染成 "0" —— 那是在编造数据。
 *    因此这一组组件在字段缺失时整体隐藏（见 `available`），后端落地后自动出现。
 */

type EngagementTool = ToolListItem | ToolDetail;

/** 字段存在 ⇒ 服务端支持这一轮的新功能。缺失 = 后端还没落地，整体不渲染。 */
function supportsFavorites(tool: EngagementTool): boolean {
  return readCount(tool.favorite_count) !== null;
}

function supportsLikes(tool: EngagementTool): boolean {
  return readCount(tool.like_count) !== null;
}

export interface FavoriteToggleProps {
  tool: EngagementTool;
  /** `labeled`：详情页标题区用的「图标 + 文字 + 计数」按钮；否则是卡片上的图标按钮。 */
  labeled?: boolean;
  className?: string;
}

export function FavoriteToggle({ tool, labeled = false, className }: FavoriteToggleProps) {
  const { status } = useAuth();
  const { toggle, isPending } = useFavoriteToggle(tool.slug);
  const count = readCount(tool.favorite_count);

  if (status !== "authenticated") return null;
  if (!supportsFavorites(tool)) return null;

  const favorited = tool.is_favorited === true;
  const label = favorited ? "取消收藏" : "收藏";
  const hint = `当前 ${formatCount(count)} 次收藏`;

  return (
    <Button
      type="button"
      variant={favorited ? "default" : "secondary"}
      size={labeled ? "default" : "icon-sm"}
      aria-pressed={favorited}
      aria-label={`${label} ${tool.name}`}
      title={`${label}（${hint}）`}
      disabled={isPending}
      data-testid={labeled ? "favorite-button" : "card-favorite-button"}
      onClick={() => toggle(!favorited)}
      className={cn(!labeled && "shadow-sm ring-1 ring-border", className)}
    >
      <Star aria-hidden="true" className={cn("size-4", favorited && "fill-current")} />
      {labeled ? (
        <>
          <span>{favorited ? "已收藏" : "收藏"}</span>
          <span data-testid="favorite-button-count" className="tabular-nums opacity-80">
            {formatCount(count)}
          </span>
        </>
      ) : null}
    </Button>
  );
}

export interface LikeToggleProps {
  tool: EngagementTool;
  labeled?: boolean;
  className?: string;
}

/**
 * 点赞切换。**只有整数计数，没有平均分、没有星星**（CONTRACT §23.7 / D37）：
 * 后端不下发平均分，前端也不去算。
 */
export function LikeToggle({ tool, labeled = false, className }: LikeToggleProps) {
  const { status } = useAuth();
  const { toggle, isPending } = useLikeToggle(tool.slug);
  const count = readCount(tool.like_count);

  if (status !== "authenticated") return null;
  if (!supportsLikes(tool)) return null;

  const liked = tool.is_liked === true;
  const label = liked ? "取消点赞" : "点赞";

  return (
    <Button
      type="button"
      variant={liked ? "default" : "secondary"}
      size={labeled ? "default" : "icon-sm"}
      aria-pressed={liked}
      aria-label={`${label} ${tool.name}`}
      title={liked ? "取消点赞" : "点赞"}
      disabled={isPending}
      data-testid={labeled ? "like-button" : "card-like-button"}
      onClick={() => toggle(!liked)}
      className={cn(!labeled && "shadow-sm ring-1 ring-border", className)}
    >
      <ThumbsUp aria-hidden="true" className={cn("size-4", liked && "fill-current")} />
      {labeled ? (
        <>
          <span>{liked ? "已点赞" : "点赞"}</span>
          <span data-testid="like-button-count" className="tabular-nums opacity-80">
            {formatCount(count)}
          </span>
        </>
      ) : null}
    </Button>
  );
}

/**
 * 只读计数（卡片页脚 / 详情页信息栏）。
 *
 * **卡片上不放可点的点赞按钮**（F8.2）：卡片信息密度已高，可点区域越多越容易误触。
 * 匿名访客看到的也是这两个数字。
 */
export function EngagementCounts({
  tool,
  className,
}: {
  tool: EngagementTool;
  className?: string;
}) {
  const favorites = readCount(tool.favorite_count);
  const likes = readCount(tool.like_count);
  if (favorites === null && likes === null) return null;

  return (
    <span className={cn("inline-flex items-center gap-2", className)}>
      {favorites !== null ? (
        <span className="inline-flex items-center gap-1" data-testid="favorite-count">
          <Star aria-hidden="true" className="size-3" />
          <span data-testid="favorite-count-value" className="tabular-nums">
            {formatCount(favorites)}
          </span>
          <span className="sr-only">次收藏</span>
        </span>
      ) : null}
      {likes !== null ? (
        <span className="inline-flex items-center gap-1" data-testid="like-count">
          <ThumbsUp aria-hidden="true" className="size-3" />
          <span data-testid="like-count-value" className="tabular-nums">
            {formatCount(likes)}
          </span>
          <span className="sr-only">次点赞</span>
        </span>
      ) : null}
    </span>
  );
}
