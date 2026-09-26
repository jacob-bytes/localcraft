import * as React from "react";
import { Navigate, useLocation } from "react-router-dom";

import { FullScreenLoader } from "@/components/common/PageSkeleton";
import { useAuth } from "@/hooks/useAuth";
import { useMeta } from "@/hooks/useMeta";
import { safeRedirectPath } from "@/lib/navigation";

/**
 * 门户公开区的守卫：已登录放行；匿名访客**按开关**决定放行还是去登录页。
 *
 * 与 `RequireAuth` 的区别只有一个 —— 匿名时多看一个 `portal.allow_anonymous_view`：
 *
 *   unknown                     → 全屏 loader（不闪登录页，同 RequireAuth）
 *   authenticated               → children（`must_change_password` 仍然拦截）
 *   匿名 + 开关 true            → children（门户主页、工具详情可看）
 *   匿名 + 开关 false / 取不到   → /login?redirect=…
 *
 * **「取不到」一律按不放行处理。** `/meta` 请求失败时我们并不知道这个部署
 * 到底允不允许匿名，此时把门户露出去是错的方向 —— 宁可让访客多点一次登录。
 * 反过来「加载中」则只显示 loader，不做判断，避免开关为 true 的部署出现
 * 「先跳登录页、再跳回来」的闪烁。
 *
 * 注意这**不是安全边界**：真正的可见性判定在服务端（匿名只可能拿到 `public`），
 * 这里只决定前端要不要渲染。详见 docs/04 §4。
 */
export function AllowAnonymous({ children }: { children: React.ReactNode }) {
  const { status, user } = useAuth();
  const { data: meta, isPending, isError } = useMeta();
  const location = useLocation();

  /* 登录态或站点配置还没就绪 —— 先给 loader，不急着判断 */
  if (status === "unknown" || isPending) {
    return <FullScreenLoader />;
  }

  if (status === "authenticated") {
    /* 强制改密仍然拦截一切（CONTRACT §3.2 ⑥），与 RequireAuth 保持一致 */
    if (user?.must_change_password && location.pathname !== "/change-password") {
      return <Navigate to="/change-password" replace />;
    }
    return <>{children}</>;
  }

  if (!isError && meta?.allow_anonymous_view === true) {
    return <>{children}</>;
  }

  const current = `${location.pathname}${location.search}`;
  const target = safeRedirectPath(current, "/");
  const search = target === "/" ? "" : `?redirect=${encodeURIComponent(target)}`;
  return <Navigate to={`/login${search}`} replace />;
}
