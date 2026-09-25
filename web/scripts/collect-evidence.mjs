#!/usr/bin/env node
/**
 * 验收证据采集（M2 验收 #26 / #27 / #28 / #30）。
 *
 * 这个脚本只**读取**并打印可粘贴进 checkpoint 报告的事实：分页档位对应的页数、
 * ⌘K 选中后的落地 URL、用户菜单三项的可用状态、生产产物是否含 mock。
 *
 * 用法（需要目标已在运行）：
 *   # 1) mock 模式：先 `npm run dev`
 *   node scripts/collect-evidence.mjs                       # 默认 http://127.0.0.1:5173
 *   # 2) 真实后端：后端托管 web/dist
 *   BASE=http://127.0.0.1:8000 node scripts/collect-evidence.mjs
 *
 * 依赖 @playwright/test（devDependency）与本机 Chrome（沙箱无法下载 Playwright 自带浏览器）。
 */

import { readFileSync, readdirSync, statSync, existsSync } from "node:fs";
import { dirname, join, resolve } from "node:path";
import { fileURLToPath } from "node:url";

import { chromium } from "@playwright/test";

const webRoot = resolve(dirname(fileURLToPath(import.meta.url)), "..");
const BASE = process.env.BASE ?? "http://127.0.0.1:5173";
const USERNAME = process.env.E2E_USER ?? "admin";
const PASSWORD = process.env.E2E_PASSWORD ?? "Admin@12345";

function line(title) {
  console.log(`\n=== ${title} ===`);
}

/** dist/ 里是否残留 mock（#30 的静态证据）。 */
function distCheck() {
  line("#30 dist 洁净度");
  const distDir = join(webRoot, "dist");
  if (!existsSync(distDir)) {
    console.log("dist/ 不存在 —— 先跑 npm run build");
    return;
  }
  const files = [];
  const walk = (dir) => {
    for (const entry of readdirSync(dir, { withFileTypes: true })) {
      const full = join(dir, entry.name);
      if (entry.isDirectory()) walk(full);
      else files.push(full);
    }
  };
  walk(distDir);
  const offenders = files.filter((file) => {
    const base = file.slice(distDir.length + 1);
    if (/mockServiceWorker/i.test(base)) return true;
    if (statSync(file).size > 4 * 1024 * 1024) return false;
    try {
      return /msw/i.test(readFileSync(file, "utf8"));
    } catch {
      return false;
    }
  });
  console.log(`dist 文件数: ${files.length}`);
  console.log(
    offenders.length === 0
      ? '命中 "msw" 的文件: 无（通过）'
      : `命中 "msw" 的文件: ${offenders.join(", ")}（失败）`,
  );
}

const browser = await chromium.launch({ channel: "chrome" });
const page = await (await browser.newContext({ locale: "zh-CN", viewport: { width: 1440, height: 900 } })).newPage();

await page.goto(`${BASE}/login`);
await page.getByLabel("用户名", { exact: true }).fill(USERNAME);
await page.getByLabel("密码", { exact: true }).fill(PASSWORD);
await page.getByRole("button", { name: "登录", exact: true }).click();
await page.waitForSelector('[data-testid="tool-card"]', { timeout: 20_000 });
console.log(`已登录 ${USERNAME} @ ${BASE}`);

/* ---- #26 分页：12/24/48 档的页数 + 实际翻页 ---- */
line("#26 分页（每页 12/24/48）");
for (const pageSize of [12, 24, 48]) {
  await page.goto(`${BASE}/?page_size=${pageSize}`);
  await page.waitForSelector('[data-testid="tool-card"]');
  const total = await page.evaluate(async () => {
    const text = document.body.innerText;
    const match = /共 (\d+) 个工具/.exec(text);
    return match ? Number.parseInt(match[1], 10) : null;
  });
  const pageButtons = await page
    .locator('nav[aria-label="分页"] button[aria-label^="第 "]')
    .allInnerTexts();
  const cards = await page.locator('[data-testid="tool-card"]').count();
  console.log(
    `page_size=${pageSize} → total=${total} 页数=${pageButtons.length || 1} ` +
      `页码=[${pageButtons.join(",") || "无（单页不显示控件）"}] 本页卡片=${cards}`,
  );
}
await page.goto(`${BASE}/?page_size=12`);
await page.waitForSelector('[data-testid="tool-card"]');
const firstBefore = await page.locator('[data-testid="tool-card-name"]').first().innerText();
if (await page.locator('nav[aria-label="分页"]').count()) {
  await page.getByRole("button", { name: "第 2 页" }).click();
  await page.waitForFunction(
    (name) =>
      document.querySelector('[data-testid="tool-card-name"]')?.textContent?.trim() !== name,
    firstBefore,
    { timeout: 10_000 },
  );
  const firstAfter = await page.locator('[data-testid="tool-card-name"]').first().innerText();
  console.log(`翻到第 2 页: URL=${new URL(page.url()).search} 首卡 "${firstBefore}" → "${firstAfter}"`);
} else {
  console.log("单页数据，无翻页控件（真实后端种子尚未扩到 26 个工具时会这样）");
}

/* ---- #27 ⌘K 选中后落地 URL ---- */
line("#27 ⌘K 搜索选中工具");
await page.goto(`${BASE}/`);
await page.waitForSelector('[data-testid="tool-card"]');
await page.keyboard.press("ControlOrMeta+k");
const dialog = page.getByRole("dialog");
await dialog.waitFor({ timeout: 10_000 });
await dialog.getByRole("combobox").fill("日志");
const option = dialog.getByRole("option").first();
await option.waitFor({ timeout: 10_000 });
const optionText = (await option.innerText()).split("\n").filter(Boolean).join(" / ");
await option.click();
await page.waitForURL(/\/tools\//, { timeout: 10_000 });
console.log(`选中「${optionText}」→ URL=${page.url()}`);
console.log(
  new URL(page.url()).pathname.startsWith("/tools/")
    ? "落地页: /tools/:slug（通过）"
    : "落地页: 不是详情页（失败）",
);

/* ---- #28 UserMenu 三项 ---- */
line("#28 UserMenu 三项可用性");
await page.getByTestId("user-menu-trigger").click();
await page.getByRole("menuitem").first().waitFor({ timeout: 10_000 });
for (const label of ["个人中心", "我的工具", "管理后台"]) {
  const item = page.getByRole("menuitem", { name: label });
  const count = await item.count();
  if (count === 0) {
    console.log(`${label}: 不存在（approver/superadmin 之外的角色不显示管理后台）`);
    continue;
  }
  const disabled = await item.getAttribute("data-disabled");
  console.log(`${label}: 存在，disabled=${disabled ?? "false"}`);
}
await page.keyboard.press("Escape");
await browser.close();

distCheck();
console.log("\n完成。");
