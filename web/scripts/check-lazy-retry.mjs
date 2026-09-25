#!/usr/bin/env node
/**
 * 懒加载韧性的离线自检（CONTRACT §19.9 / M4 交付项 1.1~1.4）。
 *
 * 为什么单独一个脚本：mock 套件（dev + MSW）里页面被 Service Worker 接管，
 * `page.route` 拦不到模块脚本请求；而真实后端套件需要后端在跑。这个脚本只依赖
 * **已构建的 dist + 本机 Chrome**，自己起 `vite preview`，因此可以在任何时刻复现
 * 「chunk 加载失败」这一类问题。
 *
 * 三个场景（每个都对应验收清单里的一条）：
 *   1. 一次性失败   → `lazyWithRetry` 自动重载并恢复；成功后 sessionStorage 标记被清除
 *   2. 持续 404     → 进入 chunk 错误边界，**不再**自动重载（防循环），并打印诊断
 *                     （失败 chunk URL + 服务端实际响应 + dist/index.html mtime）
 *   3. 非 chunk 错误（模块能下载但执行时抛错）→ 走**普通**错误边界，且一次都不重载
 *                     （易错点 2：不能把所有错误都变成重载）
 *
 * 用法：
 *   npm run build && node scripts/check-lazy-retry.mjs
 *   PORT=4201 node scripts/check-lazy-retry.mjs
 */

import { spawn } from "node:child_process";
import { existsSync, readFileSync, readdirSync, renameSync, statSync, writeFileSync } from "node:fs";
import { dirname, join, resolve } from "node:path";
import { fileURLToPath } from "node:url";

import { chromium } from "@playwright/test";

const webRoot = resolve(dirname(fileURLToPath(import.meta.url)), "..");
const distDir = join(webRoot, "dist");
const assetsDir = join(distDir, "assets");
const indexHtml = join(distDir, "index.html");
const PORT = Number(process.env.PORT ?? 4199);
const BASE = `http://127.0.0.1:${PORT}`;
const CHUNK_RETRY_PREFIX = "localcraft:chunk-retry:";

const results = [];
function check(label, ok, detail = "") {
  results.push({ label, ok });
  console.log(`${ok ? "  ✓" : "  ✗"} ${label}${detail ? ` —— ${detail}` : ""}`);
}

function findChunk(pageName) {
  const file = readdirSync(assetsDir).find((name) =>
    new RegExp(`^${pageName}-[A-Za-z0-9_-]+\\.js$`).test(name),
  );
  return file ? join(assetsDir, file) : null;
}

async function waitForServer(timeoutMs = 40_000) {
  const deadline = Date.now() + timeoutMs;
  while (Date.now() < deadline) {
    try {
      const response = await fetch(`${BASE}/`);
      if (response.ok) return true;
    } catch {
      /* 还没起来 */
    }
    await new Promise((done) => setTimeout(done, 300));
  }
  return false;
}

async function flags(page) {
  return page.evaluate(
    (prefix) => Object.keys(window.sessionStorage).filter((key) => key.startsWith(prefix)),
    CHUNK_RETRY_PREFIX,
  );
}

async function main() {
  if (!existsSync(indexHtml)) {
    console.error("dist/index.html 不存在 —— 先跑 npm run build");
    process.exit(2);
  }
  const loginChunk = findChunk("LoginPage");
  if (!loginChunk) {
    console.error("dist/assets 里找不到 LoginPage 的 chunk —— 先跑 npm run build");
    process.exit(2);
  }
  const loginChunkUrl = `${BASE}/assets/${loginChunk.split("/").pop()}`;
  // 场景 2/3 会改名与覆写这个 chunk —— 原始字节留在内存里，结束时原样写回，
  // 保证脚本跑完 dist/ 与跑之前逐字节一致。
  const originalChunk = readFileSync(loginChunk);

  console.log(`\n=== [1/3] chunk 一次性失败 → 自动重载并恢复 ===`);
  console.log(`目标 chunk: ${loginChunkUrl}`);

  const server = spawn(
    process.execPath,
    [join(webRoot, "node_modules", "vite", "bin", "vite.js"), "preview", "--port", String(PORT), "--strictPort"],
    { cwd: webRoot, stdio: "ignore" },
  );

  const backup = `${loginChunk}.m4-backup`;
  let browser;
  try {
    if (!(await waitForServer())) throw new Error(`vite preview 未能在 ${BASE} 起来`);
    browser = await chromium.launch({ channel: "chrome" });

    /* ------------------------------ 场景 1 ------------------------------ */
    {
      const context = await browser.newContext({ locale: "zh-CN" });
      const page = await context.newPage();
      let aborted = 0;
      let allowed = 0;
      await page.route(new RegExp(`/${loginChunk.split("/").pop().replace(/[.*+?^${}()|[\]\\]/g, "\\$&")}$`), async (route) => {
        if (aborted === 0) {
          aborted += 1;
          await route.abort("failed");
          return;
        }
        allowed += 1;
        await route.continue();
      });
      await page.goto(`${BASE}/`);
      let recovered = true;
      try {
        await page.getByTestId("login-page").waitFor({ state: "visible", timeout: 30_000 });
      } catch {
        recovered = false;
      }
      check("首次 chunk 失败后页面自动恢复", recovered, `aborted=${aborted} allowed=${allowed}`);
      check("确实拦下了第一次请求", aborted === 1, `aborted=${aborted}`);
      check("重载后重新请求并成功", allowed >= 1, `allowed=${allowed}`);
      const leftover = await flags(page);
      check("成功后清除重试标记（易错点 1）", leftover.length === 0, leftover.join(",") || "无残留");
      await context.close();
    }

    /* ------------------------------ 场景 2 ------------------------------ */
    console.log(`\n=== [2/3] chunk 持续 404 → 错误边界 + 诊断（交付项 1.2/1.3）===`);
    renameSync(loginChunk, backup);
    {
      const context = await browser.newContext({ locale: "zh-CN" });
      const page = await context.newPage();
      // 只数「真正的文档加载」：framenavigated 会把 SPA 的 pushState/replaceState
      // 也算成一次导航，用它判断「重载了几次」会虚高。
      let documentLoads = 0;
      page.on("request", (request) => {
        if (request.resourceType() === "document") documentLoads += 1;
      });
      await page.goto(`${BASE}/`);
      let boundary = true;
      try {
        await page.getByTestId("chunk-error-boundary").waitFor({ state: "visible", timeout: 30_000 });
      } catch {
        boundary = false;
      }
      check("进入 chunk 错误边界", boundary);
      const boundaryText = boundary
        ? (await page.getByTestId("chunk-error-boundary").innerText()).replace(/\s+/g, " ").slice(0, 160)
        : "";
      const manualRetry = await page.getByRole("button", { name: /重试/ }).count();
      check("提供手动重试入口", manualRetry > 0);

      const atBoundary = documentLoads;
      const probe = await page.request.get(loginChunkUrl);
      const mtime = statSync(indexHtml).mtime.toISOString();
      console.log(`  [诊断] 失败 chunk URL: ${loginChunkUrl}`);
      console.log(`  [诊断] 服务端响应: HTTP ${probe.status()} content-type=${probe.headers()["content-type"] ?? "(无)"}`);
      console.log(`  [诊断] dist/index.html mtime: ${mtime}`);
      console.log(`  [诊断] 边界文案: ${boundaryText}`);

      await page.waitForTimeout(3_000);
      check(
        "标记生效，不再自动重载（防循环）",
        documentLoads === atBoundary,
        `documentLoads=${documentLoads}`,
      );
      check("只自动重载一次", atBoundary === 2, `documentLoads=${atBoundary}`);
      const leftover = await flags(page);
      check("防循环标记存在", leftover.length > 0, leftover.join(","));
      await context.close();
    }
    renameSync(backup, loginChunk);

    /* ------------------------------ 场景 3 ------------------------------ */
    console.log(`\n=== [3/3] 非 chunk 错误 → 普通错误边界，且不重载（易错点 2）===`);
    writeFileSync(loginChunk, 'throw new Error("M4 非 chunk 错误探针");\n');
    {
      const context = await browser.newContext({ locale: "zh-CN" });
      const page = await context.newPage();
      let documentLoads = 0;
      page.on("request", (request) => {
        if (request.resourceType() === "document") documentLoads += 1;
      });
      await page.goto(`${BASE}/`);
      let generic = true;
      try {
        await page.getByTestId("route-error-boundary").waitFor({ state: "visible", timeout: 20_000 });
      } catch {
        generic = false;
      }
      check("进入普通错误边界（不是 chunk 边界）", generic);
      check("chunk 错误边界未被使用", (await page.getByTestId("chunk-error-boundary").count()) === 0);
      const text = generic
        ? (await page.getByTestId("route-error-boundary").innerText()).replace(/\s+/g, " ")
        : "";
      check("错误文案里带出了原始异常", text.includes("M4 非 chunk 错误探针"), text.slice(0, 100));
      await page.waitForTimeout(2_000);
      check("渲染错误不触发重载", documentLoads === 1, `documentLoads=${documentLoads}`);
      await context.close();
    }

    const failed = results.filter((entry) => !entry.ok);
    console.log(
      `\ncheck-lazy-retry: ${results.length - failed.length}/${results.length} 项通过` +
        (failed.length > 0 ? `，失败：${failed.map((entry) => entry.label).join(" / ")}` : ""),
    );
    process.exitCode = failed.length > 0 ? 1 : 0;
  } finally {
    if (browser) await browser.close().catch(() => {});
    server.kill("SIGTERM");
    // 任何异常路径都要把 dist 还原成跑之前的样子（逐字节）
    try {
      if (existsSync(backup)) renameSync(backup, loginChunk);
    } catch (error) {
      console.error(`还原备份失败：${String(error)}`);
    }
    try {
      writeFileSync(loginChunk, originalChunk);
    } catch (error) {
      console.error(`还原 chunk 内容失败：${String(error)}`);
    }
    if (!existsSync(backup) && !loginChunk) console.error("dist 可能已损坏，请重新 npm run build");
  }
}

await main();
