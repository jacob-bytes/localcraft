import { statSync } from "node:fs";
import { dirname, resolve } from "node:path";
import { fileURLToPath } from "node:url";

import type { APIRequestContext, Page, TestInfo } from "@playwright/test";

/**
 * 懒加载 chunk 失败时的现场取证（CONTRACT §19.9 / M4 交付项 1.3）。
 *
 * 上一轮监控方只能从 React 错误边界的文案里读到失败 chunk 的 URL，剩下什么都看不到。
 * 这里在**每个用例开始时**挂上监听，失败时打印四样东西：
 *
 *   1. 失败/非 2xx 的 chunk 请求（URL + requestfailed 原因 或 HTTP 状态 + content-type）
 *   2. 对每个失败 URL 的**现场复核**（测试结束时再打一次，判断是「文件没了」
 *      还是「当时那一下失败」）
 *   3. `dist/index.html` 的当前 mtime（排除「构建替换导致旧 chunk 消失」）
 *   4. 页面上的错误边界文案 + `sessionStorage` 里的 chunk 重试标记
 *      （证明 lazyWithRetry 是否已经自动重载过）
 */

const webRoot = resolve(dirname(fileURLToPath(import.meta.url)), "..");

/** 只关心模块脚本与样式：图片/接口失败由各自的用例断言。 */
const ASSET_PATTERN = /\.(?:js|mjs|css)(?:\?|$)/;

interface ChunkEvent {
  url: string;
  detail: string;
}

const trackers = new WeakMap<Page, ChunkEvent[]>();

/** 在用例开头调用；只挂监听，不影响任何断言。 */
export function trackChunkRequests(page: Page): void {
  const events: ChunkEvent[] = [];
  trackers.set(page, events);

  page.on("requestfailed", (request) => {
    if (!ASSET_PATTERN.test(request.url())) return;
    events.push({
      url: request.url(),
      detail: `requestfailed: ${request.failure()?.errorText ?? "unknown"}`,
    });
  });
  page.on("response", (response) => {
    if (response.status() < 400 || !ASSET_PATTERN.test(response.url())) return;
    events.push({
      url: response.url(),
      detail: `HTTP ${response.status()} content-type=${response.headers()["content-type"] ?? ""}`,
    });
  });
}

function distIndexMtime(): string {
  try {
    return statSync(resolve(webRoot, "dist", "index.html")).mtime.toISOString();
  } catch (error) {
    return `读取失败: ${String(error)}`;
  }
}

/** 从错误边界的文案里捞 chunk URL —— 边界已经把它显示出来了，别再丢一次。 */
function urlsFromText(text: string, baseURL: string): string[] {
  const found = text.match(/https?:\/\/[^\s"'()]+?\.(?:js|mjs|css)(?:\?[^\s"'()]*)?/g) ?? [];
  const relative = text.match(/\/assets\/[^\s"'()]+?\.(?:js|mjs|css)/g) ?? [];
  return [...found, ...relative.map((path) => `${baseURL.replace(/\/$/, "")}${path}`)];
}

export async function reportChunkDiagnostics(
  page: Page,
  request: APIRequestContext,
  testInfo: TestInfo,
): Promise<void> {
  const baseURL = String(testInfo.project.use.baseURL ?? "http://127.0.0.1:8000");
  const events = trackers.get(page) ?? [];

  const snapshot = await page
    .evaluate(() => {
      const read = (selector: string) =>
        Array.from(document.querySelectorAll(selector))
          .map((node) => (node.textContent ?? "").trim())
          .filter(Boolean);
      let retries: string[] = [];
      try {
        retries = Object.keys(window.sessionStorage).filter((key) =>
          key.startsWith("selftool:chunk-retry:"),
        );
      } catch {
        retries = ["<sessionStorage 不可用>"];
      }
      return {
        chunkBoundary: read('[data-testid="chunk-error-boundary"]'),
        routeBoundary: read('[data-testid="route-error-boundary"]'),
        retries,
        body: document.body?.innerText?.slice(0, 600) ?? "",
      };
    })
    .catch((error: unknown) => ({ error: String(error) }) as const);

  const lines: string[] = ["", "=== [诊断] chunk / 静态资源加载现场 ==="];
  lines.push(`baseURL: ${baseURL}`);
  lines.push(`dist/index.html mtime: ${distIndexMtime()}`);

  if ("error" in snapshot) {
    lines.push(`页面快照读取失败（可能已关闭）: ${snapshot.error}`);
  } else {
    lines.push(
      `sessionStorage 里的 chunk 重试标记: ${
        snapshot.retries.length > 0 ? snapshot.retries.join(", ") : "（无 → 未发生自动重载）"
      }`,
    );
    if (snapshot.chunkBoundary.length > 0) {
      lines.push(`chunk 错误边界文案: ${snapshot.chunkBoundary.join(" | ")}`);
    }
    if (snapshot.routeBoundary.length > 0) {
      lines.push(`普通错误边界文案: ${snapshot.routeBoundary.join(" | ")}`);
    }
  }

  const urls = new Set(events.map((event) => event.url));
  if (!("error" in snapshot)) {
    for (const text of [...snapshot.chunkBoundary, ...snapshot.routeBoundary]) {
      for (const url of urlsFromText(text, baseURL)) urls.add(url);
    }
  }

  if (events.length === 0 && urls.size === 0) {
    lines.push("本用例没有观察到任何失败的 chunk 请求（失败原因可能不在静态资源）。");
  } else {
    lines.push(`观察到 ${events.length} 条失败的静态资源请求：`);
    for (const event of events) lines.push(`  - ${event.url} → ${event.detail}`);
  }

  for (const url of urls) {
    try {
      const response = await request.get(url);
      lines.push(
        `现场复核 ${url} → HTTP ${response.status()} content-type=${
          response.headers()["content-type"] ?? "(无)"
        }`,
      );
    } catch (error) {
      lines.push(`现场复核 ${url} → 请求失败: ${String(error)}`);
    }
  }

  if (!("error" in snapshot) && snapshot.body) {
    lines.push(`页面正文前 600 字: ${snapshot.body.replace(/\s+/g, " ")}`);
  }
  lines.push("=== [诊断] 结束 ===", "");

  const report = lines.join("\n");
  // eslint 之外的 reporter（list）会把 console.log 归属到失败的用例下
  console.log(report);
  await testInfo.attach("chunk-diagnostics.txt", { body: report, contentType: "text/plain" });
}
