import { lazy, type ComponentType, type LazyExoticComponent } from "react";

/**
 * 路由级懒加载的韧性包装（CONTRACT §19.9，M4 交付项 1）。
 *
 * 背景：`React.lazy(() => import(...))` 在 chunk 拉取失败时会把异常抛给 React 错误
 * 边界，用户看到的是「Unexpected Application Error」白屏。chunk 拉取失败的原因很多
 * ——部署替换了带哈希的文件名、瞬时网络抖动、请求被中断、代理返回非 JS 内容——
 * 前端无法逐一区分，因此这里**不判定根因**，只做对任何成因都有效的兜底：
 *
 *   1. 失败且这个 chunk 还没重试过 → 打标记，然后 `location.reload()`。
 *      整页重新加载会重新取 `index.html`，拿到当前部署真实的 chunk 名。
 *   2. 标记**在该 chunk 成功加载后立刻清除**（易错点 1）：否则用户在一次失败后
 *      将永久失去自动恢复能力。
 *   3. 同一个 chunk 已经重载过一次仍然失败 → 不再重载（防循环），交给
 *      `RouteErrorBoundary` 展示可手动重试的界面。另有一个全局次数上限兜底。
 *   4. `sessionStorage` 不可用（隐私模式/被禁用）时无法防循环 → 不自动重载。
 *
 * 存储布局（同一个会话内）：
 *   `localcraft:chunk-retry:file`   最近一次触发自动重载的 chunk **文件名**（带哈希）
 *   `localcraft:chunk-retry:name`   同一时刻的稳定页面名，用于成功时归还额度
 *   `localcraft:chunk-retry:count`  本会话已自动重载的次数（硬上限，兜底防循环）
 *
 * 判定用**文件名**（错误信息里只有文件名），归还用**页面名**（成功路径拿不到文件名）。
 */

export const CHUNK_RETRY_PREFIX = "localcraft:chunk-retry:";
const KEY_FILE = `${CHUNK_RETRY_PREFIX}file`;
const KEY_NAME = `${CHUNK_RETRY_PREFIX}name`;
const KEY_COUNT = `${CHUNK_RETRY_PREFIX}count`;

/** 整个会话的自动重载次数硬上限：任何未预料的循环都由它兜底。 */
const MAX_AUTO_RELOADS = 2;

/**
 * 识别「模块脚本加载失败」这一类异常。
 *
 * 覆盖：Webpack 的 `ChunkLoadError`、Vite/Rollup 的
 * `Failed to fetch dynamically imported module`（生产与 dev 同文案）、
 * Safari 的 `Importing a module script failed`、以及旧浏览器的
 * `error loading dynamically imported module`。
 */
export function isChunkLoadError(error: unknown): boolean {
  if (error === null || error === undefined) return false;
  const candidate = error as { name?: unknown; message?: unknown };
  const name = typeof candidate.name === "string" ? candidate.name : "";
  const message = typeof candidate.message === "string" ? candidate.message : String(error);
  if (name === "ChunkLoadError") return true;
  return /Failed to fetch dynamically imported module|error loading dynamically imported module|Importing a module script failed|Loading chunk \d+ failed|Loading CSS chunk \d+ failed/i.test(
    message,
  );
}

/** 从异常里抽出失败 chunk 的 URL —— 诊断与防循环标记都要用它。 */
export function chunkUrlFromError(error: unknown): string | null {
  const message =
    error && typeof error === "object" && "message" in error
      ? String((error as { message: unknown }).message)
      : String(error ?? "");
  const match = /https?:\/\/[^\s"'()]+?\.(?:js|mjs|css)(?:\?[^\s"'()]*)?/.exec(message);
  return match?.[0] ?? null;
}

/** chunk 文件名（不含目录与查询串）；取不到就回退到 `unknown`。 */
export function chunkKeyFromError(error: unknown): string {
  const url = chunkUrlFromError(error);
  if (!url) return "unknown";
  const path = url.split("?")[0] ?? url;
  return path.split("/").pop() || "unknown";
}

/* -------------------------------------------------------------------------- */
/* sessionStorage（可能被禁用）                                                */
/* -------------------------------------------------------------------------- */

function safeStorage(): Storage | null {
  try {
    const storage = window.sessionStorage;
    const probe = `${CHUNK_RETRY_PREFIX}__probe__`;
    storage.setItem(probe, "1");
    storage.removeItem(probe);
    return storage;
  } catch {
    return null;
  }
}

/**
 * 能否持久化防循环标记。不能持久化时**任何**自动重载都必须放弃
 * （否则刷新后标记丢失 → 无限重载）。
 */
export function canPersistRetryFlag(): boolean {
  return safeStorage() !== null;
}

function readFlag(key: string): string | null {
  return safeStorage()?.getItem(key) ?? null;
}

function writeFlag(key: string, value: string): void {
  safeStorage()?.setItem(key, value);
}

/** 本会话已自动重载的次数。 */
export function autoReloadCount(): number {
  const raw = readFlag(KEY_COUNT);
  const parsed = raw === null ? 0 : Number.parseInt(raw, 10);
  return Number.isFinite(parsed) && parsed > 0 ? parsed : 0;
}

/**
 * 这个 chunk（按文件名）是否已经消耗过自动重载额度。
 * 错误边界用它决定「还能不能自动重载」。
 */
export function hasRetriedChunk(chunkKey: string): boolean {
  if (chunkKey === "unknown") return autoReloadCount() > 0;
  return readFlag(KEY_FILE) === chunkKey || autoReloadCount() >= MAX_AUTO_RELOADS;
}

/** 错误边界要走自动重载时调用：登记并占用一次额度。 */
export function markChunkRetried(chunkKey: string, stableName = "boundary"): void {
  writeFlag(KEY_FILE, chunkKey);
  writeFlag(KEY_NAME, stableName);
  writeFlag(KEY_COUNT, String(autoReloadCount() + 1));
}

/**
 * 懒加载成功 → 归还额度（易错点 1）。
 *
 * 只有「这次成功的就是上次触发重载的那个 chunk」才归还：用稳定页面名比对
 * （成功路径拿不到带哈希的文件名）。比对不上就不动 —— 例如上次是别的 chunk
 * 失败重载的，那个额度必须留给它，否则会退化成「任意 chunk 成功就清标记」，
 * 在某个 chunk 持续失败时被其它 chunk 的成功反复解锁，产生额外重载。
 */
function releaseRetryQuota(stableName: string): void {
  if (readFlag(KEY_NAME) !== stableName) return;
  const storage = safeStorage();
  if (!storage) return;
  storage.removeItem(KEY_FILE);
  storage.removeItem(KEY_NAME);
  storage.removeItem(KEY_COUNT);
}

/** 清掉所有 chunk 重试标记（错误边界的「清除标记并重载」用）。 */
export function clearAllChunkRetries(): void {
  const storage = safeStorage();
  if (!storage) return;
  const keys: string[] = [];
  for (let index = 0; index < storage.length; index += 1) {
    const key = storage.key(index);
    if (key?.startsWith(CHUNK_RETRY_PREFIX)) keys.push(key);
  }
  for (const key of keys) storage.removeItem(key);
}

/* -------------------------------------------------------------------------- */
/* lazyWithRetry                                                              */
/* -------------------------------------------------------------------------- */

/** 重载期间让 Suspense 一直挂着：这个 Promise 故意永不 resolve。 */
function pendingForever<T>(): Promise<T> {
  return new Promise<T>(() => {
    /* 整页重载会带走整个 JS 上下文 */
  });
}

/**
 * `React.lazy` / `LazyExoticComponent` 都要求 `T extends ComponentType<any>`。
 * 项目禁止显式 `any`，所以这里从 `lazy` 自身的签名里取出那个约束 ——
 * 类型层面等价，源码里不出现 `any`。
 */
type LazyComponentConstraint = Parameters<typeof lazy>[0] extends () => Promise<{
  default: infer Component;
}>
  ? Component
  : ComponentType<never>;

/**
 * `React.lazy` + 「失败重载一次」。
 *
 * @param factory   `() => import("@/pages/Xxx")`
 * @param chunkName 稳定页面名（诊断与「成功归还额度」用）
 */
export function lazyWithRetry<T extends LazyComponentConstraint>(
  factory: () => Promise<{ default: T }>,
  chunkName: string,
): LazyExoticComponent<T> {
  const retryable = async (): Promise<{ default: T }> => {
    try {
      const module = await factory();
      // 成功 → 立刻归还「自动重载额度」（易错点 1）。
      releaseRetryQuota(chunkName);
      return module;
    } catch (error) {
      if (!isChunkLoadError(error)) throw error;
      const fileKey = chunkKeyFromError(error);
      if (!canPersistRetryFlag() || hasRetriedChunk(fileKey)) {
        // 防不了循环，或这个 chunk 已经重载过一次 —— 交给错误边界手动处理。
        throw error;
      }
      markChunkRetried(fileKey, chunkName);
      window.location.reload();
      return pendingForever<{ default: T }>();
    }
  };
  // 转成 `React.lazy` 自己的约束类型再调用（源码里不出现 `any`），
  // 返回类型仍然是精确的 `LazyExoticComponent<T>`。
  const retryableLazy = lazy(
    retryable as unknown as () => Promise<{ default: LazyComponentConstraint }>,
  );
  return retryableLazy as unknown as LazyExoticComponent<T>;
}
