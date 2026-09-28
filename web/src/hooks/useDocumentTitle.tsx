import * as React from "react";

import { siteName, useMeta } from "@/hooks/useMeta";

/**
 * M12 · F3 —— 让 `document.title` 跟随 `portal.site_name`（CONTRACT §27.4）。
 *
 * 这是**修一个已实测的缺陷**，不是新功能：原先 `<title>` 只写死在 `index.html`
 * 里，全仓没有任何设置 `document.title` 的代码，于是管理员改了站点名之后顶栏变了、
 * 浏览器标签页没变（§27.5 约束 2 的实测表）。
 *
 * 三个刻意的取舍：
 *
 * - 挂在 `App` 的 provider 栈里（`QueryClientProvider` 之内、`RouterProvider`
 *   之外），而不是 `AppShell` 里 —— `/login` 不在 `AppShell` 下，挂在 shell 里
 *   登录页就拿不到正确标题。
 * - **只要求页面加载后正确**：`staleTime: Infinity` 的 `/meta` 在刷新后才重新
 *   请求，§27.4 明确不要求「改设置后不刷新即时更新」。
 * - `/meta` 失败或尚未返回时用 `siteName()` 的既有默认值，与 `index.html` 里那段
 *   静态 `<title>`（首屏兜底，JS 未加载完时显示）保持同一个值，所以不会出现
 *   「先闪一个别的名字」。
 */
export function DocumentTitle() {
  const { data: meta } = useMeta();

  React.useEffect(() => {
    document.title = siteName(meta);
  }, [meta]);

  return null;
}
