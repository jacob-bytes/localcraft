import * as React from "react";

import { cn } from "@/lib/utils";

/**
 * M7 · F5：三类空态的内联 SVG 插画（无搜索结果 / 无工具 / 无待审）。
 *
 * 硬约束：
 *  - **内联 SVG**，不引入任何图片资源或远程字体（docs/00 D24：内网可能完全离线）；
 *  - `aria-hidden="true"` —— 图形纯装饰，信息仍由 `EmptyState` 的标题/描述文字承担
 *    （任务书明确要求「文案必须仍然在 DOM 里」，这里没有替掉任何文字）；
 *  - 颜色一律走语义令牌（`currentColor` + `text-muted-foreground` / `text-primary`），
 *    light / dark 两种主题自动成立，不硬编码任何颜色值。
 *
 * 三个插画统一 `viewBox="0 0 120 88"`、线宽 1.5–2.5，风格与 lucide 图标一致
 * （圆头线帽、无填充）。
 */

const SVG_BASE = "h-20 w-auto text-muted-foreground";

function Svg({
  className,
  children,
}: {
  className?: string;
  children: React.ReactNode;
}) {
  return (
    <svg
      aria-hidden="true"
      focusable="false"
      viewBox="0 0 120 88"
      fill="none"
      strokeLinecap="round"
      strokeLinejoin="round"
      className={cn(SVG_BASE, className)}
    >
      {children}
    </svg>
  );
}

/** 无搜索结果：放大镜 + 空文档 + 镜片里的「×」。 */
export function NoResultsArt({ className }: { className?: string }) {
  return (
    <Svg className={className}>
      {/* 空文档轮廓 */}
      <g stroke="currentColor" strokeOpacity="0.45" strokeWidth="1.5">
        <rect x="14" y="10" width="62" height="68" rx="8" />
      </g>
      <g stroke="currentColor" strokeOpacity="0.35" strokeWidth="1.5">
        <path d="M26 30h34" />
        <path d="M26 44h24" />
        <path d="M26 58h18" />
      </g>
      {/* 放大镜（主色强调） */}
      <g className="text-primary" stroke="currentColor" strokeWidth="2.5">
        <circle cx="82" cy="52" r="19" />
        <path d="M95.5 65.5 108 78" strokeWidth="3" />
        <path d="M75 45l14 14" />
        <path d="M89 45 75 59" />
      </g>
    </Svg>
  );
}

/** 无工具：一个空箱子 + 上方虚线的「加入」提示。 */
export function NoToolsArt({ className }: { className?: string }) {
  return (
    <Svg className={className}>
      {/* 箱体 */}
      <g stroke="currentColor" strokeOpacity="0.45" strokeWidth="1.5">
        <path d="M26 42h68l-7 34H33z" />
        <path d="M26 42 34 28h52l8 14" />
      </g>
      {/* 箱内空槽 */}
      <g stroke="currentColor" strokeOpacity="0.3" strokeWidth="1.5">
        <path d="M42 54h36" />
        <path d="M46 64h28" />
      </g>
      {/* 加入提示 */}
      <g className="text-primary" stroke="currentColor" strokeWidth="2.5">
        <circle cx="60" cy="18" r="11" strokeDasharray="4 5" strokeWidth="2" />
        <path d="M60 13v10" />
        <path d="M55 18h10" />
      </g>
    </Svg>
  );
}

/** 无待审：空收件盘 + 对勾。 */
export function NoPendingArt({ className }: { className?: string }) {
  return (
    <Svg className={className}>
      {/* 收件盘 */}
      <g stroke="currentColor" strokeOpacity="0.45" strokeWidth="1.5">
        <path d="M20 48h22l6 11h24l6-11h22v22a6 6 0 0 1-6 6H26a6 6 0 0 1-6-6z" />
        <path d="M20 48h80" />
      </g>
      {/* 已清空的虚线槽 */}
      <g stroke="currentColor" strokeOpacity="0.3" strokeWidth="1.5">
        <path d="M34 30h22" />
        <path d="M34 20h14" />
      </g>
      {/* 对勾（主色强调） */}
      <g className="text-primary" stroke="currentColor" strokeWidth="2.5">
        <circle cx="86" cy="22" r="14" />
        <path d="M79.5 22.5 84 27l9.5-10" strokeWidth="3" />
      </g>
    </Svg>
  );
}
