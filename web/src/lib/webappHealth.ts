/**
 * M14 · F2（CONTRACT §29.3）：在线工具探活结果的**宽容解析**与**文案**。
 *
 * 三条硬约束都在这里落地：
 *
 * 1. **`null` = 从未检测过，与 `fail` 是两回事**（§29.2 / §29.3 的原文）。
 *    所以「没拿到值」和「拿到 `fail`」必须落到不同的文案上 —— 有一个用例专门
 *    钉住这一点，不允许把「尚未检测」渲染成「失败」。
 * 2. **后端未落地时字段缺失**（M14 后端与前端并行开发），此时不能让页面崩、
 *    更不能渲染出 `undefined`。派生布尔一律按 `false` 处理 —— 这与 §29.3
 *    「从未检测过（NULL）不算不健康」的方向一致：宁可少一个告警，不可误报。
 * 3. **只看后端说了什么**：`webapp_unhealthy` 是后端算好的派生字段，前端不重算
 *    （列表项也没有原始状态可算）。这里只做「是不是恰好 `true`」的运行时守卫。
 */

import type { WebappHealthStatus } from "@/api/types";

/** 详情页的三种**已检测**结果文案。`null` 不在其中（它有单独的文案）。 */
export const WEBAPP_HEALTH_LABELS: Record<WebappHealthStatus, string> = {
  ok: "运行正常",
  fail: "检测失败",
  timeout: "检测超时",
};

/** 从未检测过。刻意与「检测失败」用词分离（§29.3）。 */
export const WEBAPP_HEALTH_UNCHECKED_LABEL = "尚未检测";

/**
 * 把收到的 `webapp_health_status` 归一化成三个已知值或 `null`。
 *
 * 未知字符串（后端将来扩了枚举、或字段被改坏）**也当作 `null`**：`null` 的语义是
 * 「没有可用的检测结果」，比硬塞一个认不出的值去渲染更安全。
 */
export function readWebappHealthStatus(value: unknown): WebappHealthStatus | null {
  return value === "ok" || value === "fail" || value === "timeout" ? value : null;
}

/**
 * 派生布尔。**只有恰好 `true` 才算不健康**：
 *  - 字段缺失（`undefined`，后端未落地）→ `false`；
 *  - `null` / 字符串 / 数字等任何非 `true` 值 → `false`。
 *
 * 反过来（把缺字段当成不健康）会让默认部署的每张在线工具卡片都挂上告警，
 * 正是 §29.3 明确要避免的。
 */
export function isWebappUnhealthy(value: unknown): boolean {
  return value === true;
}

/** 详情页要显示的一行文案（`null` → 「尚未检测」）。 */
export function webappHealthLabel(value: unknown): string {
  const status = readWebappHealthStatus(value);
  return status === null ? WEBAPP_HEALTH_UNCHECKED_LABEL : WEBAPP_HEALTH_LABELS[status];
}
