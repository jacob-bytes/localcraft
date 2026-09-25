import * as React from "react";
import { useRouteError } from "react-router-dom";

import { ErrorState } from "@/components/common/ErrorState";
import { Button } from "@/components/ui/button";
import {
  canPersistRetryFlag,
  chunkKeyFromError,
  chunkUrlFromError,
  clearAllChunkRetries,
  hasRetriedChunk,
  isChunkLoadError,
  markChunkRetried,
} from "@/lib/lazyWithRetry";

/**
 * 路由级错误边界（CONTRACT §19.9 / M4 交付项 1.2）。
 *
 * **只对「模块脚本加载失败」自动重载**；其他渲染错误一律照常展示，绝不重载 ——
 * 渲染错误重载会变成无限循环（易错点 2）。
 *
 * 自动重载的额度与 `lazyWithRetry` 共用同一个 sessionStorage 标记：
 *   - 首次 chunk 失败：`lazyWithRetry` 已重载过一次，这里只展示手动重试界面
 *   - 非路由的懒加载（Markdown/Shiki、mermaid 之类的嵌套 import）失败：
 *     没人包装它，这里补上「自动重载一次」
 *   - 已经重载过仍失败：不再自动重载，把控制权交给用户（并提供「清除标记」）
 */

function errorMessage(error: unknown): string {
  if (error && typeof error === "object" && "message" in error) {
    const message = (error as { message: unknown }).message;
    if (typeof message === "string" && message.trim() !== "") return message;
  }
  if (typeof error === "string" && error.trim() !== "") return error;
  return "未知错误";
}

export function RouteErrorBoundary() {
  const error = useRouteError();
  const chunkError = isChunkLoadError(error);
  const chunkUrl = chunkUrlFromError(error);
  const chunkKey = chunkKeyFromError(error);
  const guarded = canPersistRetryFlag();
  const alreadyRetried = hasRetriedChunk(chunkKey);

  React.useEffect(() => {
    // 只对 chunk 失败重载；且只在「标记可持久化 + 这个 chunk 还没重载过」时做，
    // 否则会把渲染错误变成无限重载循环（易错点 2）。
    // 走到这里通常说明 lazyWithRetry 已经重载过一次（标记已置位）→ 不再自动重载。
    if (!chunkError || !guarded || alreadyRetried) return;
    markChunkRetried(chunkKey, `boundary:${chunkKey}`);
    window.location.reload();
  }, [chunkError, guarded, alreadyRetried, chunkKey]);

  if (chunkError) {
    const autoReloading = guarded && !alreadyRetried;
    return (
      <div className="mx-auto w-full max-w-2xl p-6" data-testid="chunk-error-boundary">
        <ErrorState
          title={autoReloading ? "页面资源加载失败，正在重新加载…" : "页面资源加载失败"}
          message={
            autoReloading
              ? "正在自动重新加载一次（已允许一次 chunk 重试）。"
              : "已自动重试过一次仍未成功。通常是部署更新了静态资源，手动刷新即可恢复。"
          }
          onRetry={() => {
            // 先归还额度再重载：用户主动重试必须拥有新的自动恢复机会（易错点 1）。
            clearAllChunkRetries();
            window.location.reload();
          }}
        />
        <div className="mt-4 space-y-2 text-xs text-muted-foreground">
          <p className="break-all font-mono">失败的模块：{chunkUrl ?? "（未能从错误信息中解析出 URL）"}</p>
          <p className="break-all font-mono">错误信息：{errorMessage(error)}</p>
          <Button
            type="button"
            variant="ghost"
            size="sm"
            onClick={() => {
              window.location.href = "/";
            }}
          >
            返回首页
          </Button>
        </div>
      </div>
    );
  }

  return (
    <div className="mx-auto w-full max-w-2xl p-6" data-testid="route-error-boundary">
      <ErrorState
        title="页面出错了"
        message={errorMessage(error)}
        onRetry={() => window.location.reload()}
      />
      <div className="mt-4 text-center">
        <Button
          type="button"
          variant="ghost"
          size="sm"
          onClick={() => {
            window.location.href = "/";
          }}
        >
          返回首页
        </Button>
      </div>
    </div>
  );
}
