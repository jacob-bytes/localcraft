#!/usr/bin/env node
/**
 * 截取 README 用的界面截图。
 *
 * 用法:
 *   PORT=8099 node web/scripts/capture-screenshots.mjs
 *
 * 前置：先在一个终端里 `PORT=8099 bash preview.sh`（生产拓扑，已播种 26 个演示工具）。
 *
 * 为什么把截图脚本入库：截图会随 UI 改动过时。留一个可重跑的命令，
 * 比留一堆来历不明的 PNG 诚实 —— 谁都能在改动后重新生成并 diff。
 *
 * 输出：docs/images/*.png
 */

import { chromium } from "playwright";
import { mkdirSync, statSync } from "node:fs";
import { dirname, join, resolve } from "node:path";
import { fileURLToPath } from "node:url";

const HERE = dirname(fileURLToPath(import.meta.url));
const REPO = resolve(HERE, "..", "..");
const OUT_DIR = join(REPO, "docs", "images");

const BASE = `http://127.0.0.1:${process.env.PORT ?? "8099"}`;
const ADMIN = { username: process.env.SHOT_USER ?? "admin", password: process.env.SHOT_PASS ?? "Admin@12345" };

//: 统一视口：README 正文列宽约 880px，1440 宽足够清晰又不至于让 PNG 太肥。
const VIEWPORT = { width: 1440, height: 900 };

/**
 * 每个截图：路径、文件名、说明。
 * `beforeLogin: true` 的必须在登录前抓 —— 登录后访问 /login 会被重定向到门户，
 * 否则拿到的其实是门户的重复图（这个坑踩过一次）。
 */
const SHOTS = [
  // 登录页正文天然很短（一个表单），给它单独的阈值，否则会误报「疑似空白页」。
  { name: "01-login", path: "/login", beforeLogin: true, minChars: 40, note: "登录页" },
  { name: "02-portal", path: "/", note: "工具门户（卡片墙 + 分类侧栏）" },
  { name: "03-portal-dark", path: "/", dark: true, note: "门户暗色模式" },
  { name: "04-tool-detail", path: null, note: "工具详情（版本时间线 + 文件树）" },
  { name: "05-my-tools", path: "/me/tools", note: "我的工具" },
  { name: "06-approvals", path: "/admin/approvals", note: "审批台" },
  { name: "07-users", path: "/admin/users", note: "用户与权限" },
  { name: "08-import-export", path: "/admin/import-export", note: "批量导入导出（管理 API 配套）" },
];

function log(msg) {
  process.stdout.write(`  ${msg}\n`);
}

async function login(page) {
  await page.goto(`${BASE}/login`, { waitUntil: "networkidle" });
  await page.fill('input[name="username"]', ADMIN.username);
  await page.fill('input[name="password"]', ADMIN.password);
  await Promise.all([
    page.waitForURL((u) => !u.pathname.startsWith("/login"), { timeout: 15000 }),
    page.click('button[type="submit"]'),
  ]);
}

/** 关掉可能遮挡界面的浮层（toast / dialog）。 */
async function settle(page) {
  await page.waitForLoadState("networkidle").catch(() => {});
  await page.keyboard.press("Escape").catch(() => {});
  await page.waitForTimeout(400);
}

/**
 * 给每张截图留一份**文本指纹**，用来证明它渲染出了真实内容而不是空白页/错误页。
 *
 * 为什么需要：截图是给人看的，自动化跑完只能拿到「文件已生成」。
 * 把主要文本抓出来打到日志里，CI 或审阅者不必打开图片就能判断这张图有没有意义。
 */
async function fingerprint(page) {
  return page.evaluate(() => {
    const pick = (sel) => document.querySelector(sel)?.textContent?.trim() ?? "";
    const body = document.body.innerText.replace(/\s+/g, " ").trim();
    return {
      title: document.title,
      h1: pick("h1"),
      // 卡片/表格行数，用来判断列表是否真的渲染了内容
      cards: document.querySelectorAll('a[href^="/tools/"]').length,
      rows: document.querySelectorAll("table tbody tr").length,
      chars: body.length,
      theme: document.documentElement.classList.contains("dark") ? "dark" : "light",
      sample: body.slice(0, 120),
    };
  });
}

async function main() {
  mkdirSync(OUT_DIR, { recursive: true });

  // 与 playwright.config.ts 一致：用本机安装的 Google Chrome。
  // 自带 chromium 需要 `npx playwright install`，在部分沙箱里下不下来。
  const browser = await chromium.launch({ channel: process.env.SHOT_CHANNEL ?? "chrome" });
  const ctx = await browser.newContext({ viewport: VIEWPORT, locale: "zh-CN" });
  const page = await ctx.newPage();

  page.on("pageerror", (e) => log(`  [页面错误] ${e.message}`));

  log(`目标 ${BASE}`);

  const done = [];
  const problems = [];

  /** 抓一张图 + 记录文本指纹 + 自检。 */
  async function capture(shot, target) {
    if (shot.dark) {
      await page.evaluate(() => localStorage.setItem("localcraft-theme", "dark"));
    }
    await page.goto(`${BASE}${target}`, { waitUntil: "networkidle" });
    await settle(page);

    const fp = await fingerprint(page);
    const file = join(OUT_DIR, `${shot.name}.png`);
    await page.screenshot({ path: file });
    const kb = (statSync(file).size / 1024).toFixed(0);
    done.push({ ...shot, file, kb, fp });
    log(
      `${shot.name}.png  ${kb} KB  h1="${fp.h1 || fp.title}" 卡片=${fp.cards} 行=${fp.rows} 主题=${fp.theme}`,
    );

    // 空白页 / 错误页的兜底判断
    const minChars = shot.minChars ?? 120;
    if (fp.chars < minChars) {
      problems.push(`${shot.name}: 正文只有 ${fp.chars} 字符（阈值 ${minChars}），疑似空白页`);
    }
    if (shot.dark && fp.theme !== "dark") problems.push(`${shot.name}: 期望 dark，实际 ${fp.theme}`);
    if (shot.name === "02-portal" && fp.cards === 0)
      problems.push("02-portal: 门户没有渲染出任何工具卡片");
    if (shot.name === "01-login" && !fp.sample.includes("密码"))
      problems.push("01-login: 正文里没有「密码」，可能没抓到登录表单（是不是已被重定向？）");

    if (shot.dark) {
      await page.evaluate(() => localStorage.setItem("localcraft-theme", "light"));
    }
  }

  // 1) 登录前的页面必须先抓
  for (const shot of SHOTS.filter((s) => s.beforeLogin)) {
    await capture(shot, shot.path);
  }

  // 2) 登录
  await login(page);
  log(`已用 ${ADMIN.username} 登录`);

  // 详情页需要先拿到一个真实 slug。
  // 不能调 /api/v1/tools —— 应用把 access token 放在**内存**里（不是 cookie），
  // 页面里的 fetch 带不上鉴权。直接从门户 DOM 里取一张卡片的链接更可靠。
  await page.goto(`${BASE}/`, { waitUntil: "networkidle" });
  await settle(page);
  const slug = await page.evaluate(() => {
    const a = document.querySelector('a[href^="/tools/"]');
    return a ? a.getAttribute("href").replace(/^\/tools\//, "").split("/")[0] : null;
  });
  if (!slug) log("[警告] 门户里没找到工具卡片链接，详情页截图将跳过");
  else log(`详情页 slug = ${slug}`);

  // 3) 其余页面
  for (const shot of SHOTS.filter((s) => !s.beforeLogin)) {
    const target = shot.path ?? (slug ? `/tools/${slug}` : null);
    if (!target) continue;
    await capture(shot, target);
  }

  await browser.close();

  const total = done.reduce((s, d) => s + Number(d.kb), 0);
  log(`共 ${done.length} 张，合计 ${total.toFixed(0)} KB`);
  if (total > 3000) log("[提示] 总体积偏大，考虑减少张数或改用 JPEG");

  if (problems.length) {
    log("");
    log("自检未通过：");
    for (const p of problems) log(`  - ${p}`);
    process.exitCode = 2;
  } else {
    log("自检通过：所有页面都渲染出了非空内容");
  }
}

main().catch((e) => {
  console.error("截图失败:", e);
  process.exit(1);
});
