import { Star, ThumbsUp } from "lucide-react";

import { readCount } from "@/api/engagement";
import type { ToolDetail, ToolListItem, User } from "@/api/types";
import { Button } from "@/components/ui/button";
import { useAuth, type AuthStatus } from "@/hooks/useAuth";
import { useFavoriteToggle, useLikeToggle } from "@/hooks/useEngagement";
import { formatCount } from "@/lib/format";
import { canEngage } from "@/lib/permissions";
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
 *    因此这一组组件在字段缺失时整体隐藏（见 `supportsFavorites` / `supportsLikes`），
 *    后端落地后自动出现。
 *
 * M9 补一条（CONTRACT §25.1）：**`viewer` 也不显示切换控件**。后端
 * `engagement_guard` 不含 `viewer`（实测 PUT favorite/like 均 403），所以「登录了
 * 就能投票」是错的前端假设。处理方式与匿名一致 —— **不渲染可交互的按钮**，而不是
 * 渲染一个点下去必报错的控件；原因由 `EngagementPermissionNote` 在详情页给出
 * （沿用下载按钮「当前角色无下载权限」的措辞风格，且不泄漏内部角色名）。
 */

type EngagementTool = ToolListItem | ToolDetail;

/** 字段存在 ⇒ 服务端支持这一轮的新功能。缺失 = 后端还没落地，整体不渲染。 */
function supportsFavorites(tool: EngagementTool): boolean {
  return readCount(tool.favorite_count) !== null;
}

function supportsLikes(tool: EngagementTool): boolean {
  return readCount(tool.like_count) !== null;
}

/**
 * 「角色还没加载完」这一态的处理（M9 硬约束）。
 *
 * `status === "unknown"` 是启动态：`/auth/refresh` 还没回来，此时**什么都不渲染**。
 * 之所以不会「先闪一下按钮再消失」，是因为判定从「已认证 + 有投票角色」出发 ——
 * 未知一律按「不渲染」处理，等角色确定后只有该看到的人会看到它出现；反过来
 * （先乐观渲染、发现是 viewer 再撤掉）才会闪。下载按钮同理：它在拿到工具详情前
 * 也不会给出任何可点的入口。
 */
function canUseEngagementControls(
  status: AuthStatus,
  user: User | null,
): boolean {
  return status === "authenticated" && canEngage(user);
}

export interface FavoriteToggleProps {
  tool: EngagementTool;
  /** `labeled`：详情页标题区用的「图标 + 文字 + 计数」按钮；否则是卡片上的图标按钮。 */
  labeled?: boolean;
  className?: string;
}

export function FavoriteToggle({ tool, labeled = false, className }: FavoriteToggleProps) {
  const { status, user } = useAuth();
  const { toggle, isPending } = useFavoriteToggle(tool.slug);
  const count = readCount(tool.favorite_count);

  if (!canUseEngagementControls(status, user)) return null;
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
  const { status, user } = useAuth();
  const { toggle, isPending } = useLikeToggle(tool.slug);
  const count = readCount(tool.like_count);

  if (!canUseEngagementControls(status, user)) return null;
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
 * M9 · 权限说明（CONTRACT §25.1）：`viewer` 看不到切换控件，但**不能静默消失** ——
 * 用户得知道这是权限问题，而不是页面坏了。
 *
 * 三条刻意的设计：
 *
 * 1. **只对「已登录但没有投票角色」的账号渲染**。匿名不渲染：对未登录的人说
 *    「无权限」是错的，他只需要登录（与下载按钮区分「登录后可下载」对
 *    「当前角色无下载权限」是同一条推理）。
 * 2. **不是按钮、不可聚焦**：读屏软件读到的是一个说明性段落（`<p>`），而不是一个
 *    不可用的控件。这正是「隐藏」与「留一个点了必报错的按钮」之间的区别。
 * 3. **文案不泄漏内部角色名**：说「当前角色」，与既有「当前角色无下载权限」
 *    同一句式（FR-ACL-05 / AC-15）。
 *
 * 计数不归它管：`favorite_count` / `like_count` 是全站可见的公共数字，`viewer`
 * 照常看得到（卡片页脚、详情页信息栏），本组件只解释「为什么没有按钮」。
 *
 * 第 4 条与按钮同一口径：**服务端还没这两个字段时整条不渲染**。否则会出现
 * 「说明里说『仅可查看计数』，而页面上根本没有计数」这种自相矛盾（M8 的
 * `supportsFavorites` / `supportsLikes` 就是这条规则）。
 */
export function EngagementPermissionNote({
  tool,
  className,
}: {
  tool: EngagementTool;
  className?: string;
}) {
  const { status, user } = useAuth();
  // 先判角色（与按钮同一顺序）：那三个角色下本组件永远返回 null
  if (status !== "authenticated" || canEngage(user)) return null;
  if (!supportsFavorites(tool) && !supportsLikes(tool)) return null;

  return (
    <p
      data-testid="engagement-permission-note"
      className={cn("text-xs text-muted-foreground", className)}
    >
      当前角色无收藏、点赞权限，仅可查看计数。
    </p>
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
