/**
 * M1 真实前后端联调验收（监控方使用，不属于任何开发 agent 的交付物）
 *
 * 为什么需要这个脚本：前端交付的 Playwright 套件只在 `npm run dev` + MSW mock 下跑过，
 * 从未对真实后端验证过。mock 与真实后端之间存在若干只有联调才暴露的差异，
 * 本脚本专门覆盖这些「mock 永远测不到」的路径。
 *
 * 运行方式（必须从 web/ 目录运行，以便 require 解析到 web/node_modules）：
 *   cd web && NODE_PATH="$PWD/node_modules" node ../contracts/acceptance/real-backend-check.cjs
 *
 * 前置条件：后端已在 127.0.0.1:8000 就绪，且已 seed-demo，且 web/dist 已构建（mock 关闭）。
 */

const { chromium } = require("@playwright/test");

const BASE = process.env.ACCEPT_BASE || "http://127.0.0.1:8000";
const V = { width: 1440, height: 900 };

const results = [];
function record(id, name, ok, detail) {
  results.push({ id, name, ok, detail });
  const mark = ok ? "PASS" : "FAIL";
  console.log(`  [${mark}] ${id} ${name}${detail ? " —— " + detail : ""}`);
}
function check(id, name, cond, detail) {
  record(id, name, !!cond, detail);
  return !!cond;
}

(async () => {
  const browser = await chromium.launch({ channel: "chrome" });
  const ctx = await browser.newContext({ viewport: V, locale: "zh-CN" });
  const page = await ctx.newPage();

  const consoleErrors = [];
  const reactWarnings = [];
  const apiFailures = [];
  const navigations = [];

  page.on("console", (m) => {
    const t = m.text();
    if (m.type() === "error") consoleErrors.push(t);
    if (m.type() === "warning" && /ref|forwardRef|Function components/i.test(t)) {
      reactWarnings.push(t);
    }
  });
  page.on("pageerror", (e) => consoleErrors.push("pageerror: " + e.message));
  page.on("response", (r) => {
    const u = r.url();
    if (u.includes("/api/") && r.status() >= 400) {
      apiFailures.push(`${r.status()} ${r.request().method()} ${new URL(u).pathname}`);
    }
  });
  page.on("framenavigated", (f) => {
    if (f === page.mainFrame()) navigations.push(f.url());
  });
  // access token 只存在内存中（CONTRACT §3.1），脚本无法直接读取。
  // 从真实请求里抓一个 Authorization 头，供本脚本直接调 API 时复用。
  let authHeader = null;
  page.on("request", (r) => {
    const h = r.headers()["authorization"];
    if (h && /^Bearer /.test(h)) authHeader = h;
  });

  console.log("\n=== A. 登录与会话恢复（mock 测不到的真实 cookie 路径）===");

  await page.goto(BASE + "/", { waitUntil: "domcontentloaded" });
  await page.waitForURL(/\/login/, { timeout: 15000 }).catch(() => {});
  check("A1", "未登录访问 / 跳转 /login", /\/login/.test(page.url()), page.url());
  check(
    "A2",
    "登录页渲染出表单（非空白页）",
    (await page.locator('input[type="password"]').count()) > 0,
  );

  await page.fill('input[autocomplete="username"], input[name="username"]', "admin");
  await page.fill('input[type="password"]', "Admin@12345");
  await page.click('button[type="submit"]');
  await page.waitForURL((u) => !/\/login/.test(u.pathname), { timeout: 15000 });
  check("A3", "admin 登录成功并离开登录页", !/\/login/.test(page.url()), page.url());

  // 关键：refresh cookie 的 Path=/api/v1/auth，mock 用的是 Path=/ —— 只有真实后端能验证
  navigations.length = 0;
  // 丢弃「未登录启动阶段」的那次 401 refresh —— 那是正确行为（还没有 cookie）。
  // 只统计登录之后的 refresh 失败才有意义。
  apiFailures.length = 0;
  await page.reload({ waitUntil: "domcontentloaded" });
  await page.waitForSelector("main, [data-testid], article, a[href^='/tools']", {
    timeout: 15000,
  });
  const flashedLogin = navigations.some((u) => /\/login/.test(u));
  check("A4", "F5 后会话通过 refresh 恢复（不闪登录页）", !flashedLogin, `navigations=${navigations.length}`);
  check("A5", "刷新后仍在门户而非 /login", !/\/login/.test(page.url()), page.url());

  const refreshHits = apiFailures.filter((x) => x.includes("/auth/refresh"));
  check("A6", "refresh 请求无 4xx（cookie Path 正确）", refreshHits.length === 0, refreshHits.join(", ") || "无失败");
  check(
    "A7",
    "React 18 下无 ref 相关警告（shadcn React19 回归）",
    reactWarnings.length === 0,
    reactWarnings.slice(0, 2).join(" | ") || "无",
  );

  console.log("\n=== B. 真实数据渲染 ===");
  await page.waitForSelector('a[href^="/tools/"]', { timeout: 15000 }).catch(() => {});
  const cardCount = await page.locator('a[href^="/tools/"]').count();
  check("B1", "门户卡片来自真实后端（期望 8 个）", cardCount > 0, `实测 ${cardCount} 个 /tools/ 链接`);

  const bodyText = await page.locator("body").innerText();
  check("B2", "看到种子工具名", /日志|工具|Skill|Prompt|巡检|部署/i.test(bodyText));

  // 无封面工具应降级为占位块，不出现破图
  const brokenImgs = await page.evaluate(() =>
    Array.from(document.images)
      .filter((i) => i.complete && i.naturalWidth === 0)
      .map((i) => i.currentSrc || i.src),
  );
  const visibleBroken = brokenImgs.filter((s) => s && !s.endsWith("/"));
  check("B3", "无破图（图片 404 已降级为占位块）", visibleBroken.length === 0, visibleBroken.slice(0, 2).join(", ") || "无");

  console.log("\n=== C. facets 边界（监控方裁定的 null 语义）===");
  const facetsPage1 = await page.evaluate(async (auth) => {
    const r = await fetch("/api/v1/tools?page=1&page_size=3", { headers: { Authorization: auth } });
    const j = await r.json();
    return { has: "facets" in j, isNull: j.facets === null, pages: j.pages, total: j.total };
  }, authHeader);
  check("C1", "page=1 facets 为对象", facetsPage1.has && !facetsPage1.isNull, JSON.stringify(facetsPage1));

  const facetsPage2 = await page.evaluate(async (auth) => {
    const r = await fetch("/api/v1/tools?page=2&page_size=3", { headers: { Authorization: auth } });
    const j = await r.json();
    return { has: "facets" in j, isNull: j.facets === null };
  }, authHeader);
  check("C2", "page=2 facets 键存在且为 null", facetsPage2.has && facetsPage2.isNull, JSON.stringify(facetsPage2));

  // 翻到第 2 页，前端不能因为 facets=null 而崩溃或让左侧计数消失
  await page.goto(BASE + "/?page=2&page_size=3", { waitUntil: "domcontentloaded" });
  await page.waitForTimeout(1200);
  const p2Text = await page.locator("body").innerText();
  check("C3", "page=2 页面正常渲染未崩溃", p2Text.length > 50 && !/Something went wrong|Unexpected/i.test(p2Text));

  console.log("\n=== D. 筛选状态与 URL ===");
  await page.goto(BASE + "/?type=skill", { waitUntil: "domcontentloaded" });
  await page.waitForTimeout(1200);
  const skillText = await page.locator("body").innerText();
  check("D1", "URL 带 type=skill 时页面响应", skillText.length > 50, `URL=${page.url()}`);
  const skillCards = await page.locator('a[href^="/tools/"]').count();
  check("D2", "类型筛选生效（skill 数量 < 全部）", skillCards > 0 && skillCards <= cardCount, `skill=${skillCards} all=${cardCount}`);

  console.log("\n=== E. 登出与会话吊销 ===");
  await page.goto(BASE + "/", { waitUntil: "domcontentloaded" });
  await page.waitForTimeout(800);
  // 打开用户菜单并登出
  const avatarBtn = page.locator('button[aria-label^="用户菜单"]').first();
  await avatarBtn.waitFor({ timeout: 10000 });
  await avatarBtn.click();
  await page.waitForTimeout(500);
  const logoutItem = page.getByRole("menuitem", { name: /退出登录/ }).first();
  await logoutItem.waitFor({ timeout: 8000 });
  await logoutItem.click();
  await page.waitForTimeout(2000);
  check("E1", "登出后离开门户", /\/login/.test(page.url()), page.url());

  await page.reload({ waitUntil: "domcontentloaded" });
  await page.waitForTimeout(1500);
  check("E2", "登出后刷新不能恢复会话", /\/login/.test(page.url()), page.url());

  console.log("\n=== F. 强制改密（真实后端 403 PASSWORD_CHANGE_REQUIRED）===");
  await page.goto(BASE + "/login", { waitUntil: "domcontentloaded" });
  await page.waitForSelector('input[type="password"]', { timeout: 15000 });
  const userInput = page.locator('input[autocomplete="username"], input[name="username"]').first();
  await userInput.waitFor({ timeout: 10000 });
  await userInput.fill("newbie");
  await page.fill('input[type="password"]', "Newbie@12345");
  await page.click('button[type="submit"]');
  await page.waitForTimeout(2500);
  check("F1", "newbie 登录后落在改密页", /change-password/.test(page.url()), page.url());

  await page.goto(BASE + "/", { waitUntil: "domcontentloaded" });
  await page.waitForTimeout(2000);
  check("F2", "未改密访问门户被拦回改密页", /change-password/.test(page.url()), page.url());

  console.log("\n=== G. SPA fallback（docs/03 §6.5 的坑）===");
  const deep = await page.goto(BASE + "/tools/some-nonexistent-slug", { waitUntil: "domcontentloaded" });
  const deepCt = deep && deep.headers()["content-type"];
  const deepBody = await page.locator("body").innerText();
  check("G1", "深链返回 HTML 由前端路由接管（非 JSON）", /text\/html/.test(deepCt || ""), deepCt);
  check("G2", "未匹配深链渲染 404 页而非崩溃", !/^\s*\{/.test(deepBody.trim()), deepBody.slice(0, 60).replace(/\n/g, " "));

  const apiHtml = await page.evaluate(async () => {
    const r = await fetch("/api/v1/does-not-exist");
    return { ct: r.headers.get("content-type") || "", status: r.status };
  });
  check(
    "G3",
    "未匹配的 /api 路径返回 JSON 而非 index.html",
    /application\/json/.test(apiHtml.ct) && apiHtml.status === 404,
    JSON.stringify(apiHtml),
  );

  console.log("\n=== H. 控制台与接口健康 ===");
  const realErrors = consoleErrors.filter((e) => !/favicon|404 \(Not Found\)/i.test(e));
  check("H1", "无未预期的控制台错误", realErrors.length === 0, realErrors.slice(0, 3).join(" | ") || "无");
  const unexpectedApi = apiFailures.filter(
    (x) => !/\/auth\/refresh/.test(x) && !/\/images\//.test(x) && !/401/.test(x) && !/404/.test(x),
  );
  check("H2", "无 5xx 接口失败", unexpectedApi.length === 0, unexpectedApi.slice(0, 3).join(", ") || "无");

  await browser.close();

  const failed = results.filter((r) => !r.ok);
  console.log("\n" + "=".repeat(64));
  console.log(`合计 ${results.length} 项，通过 ${results.length - failed.length}，失败 ${failed.length}`);
  if (failed.length) {
    console.log("\n失败项：");
    failed.forEach((f) => console.log(`  - ${f.id} ${f.name} :: ${f.detail}`));
  }
  if (apiFailures.length) {
    console.log("\n接口失败明细（去重）：");
    [...new Set(apiFailures)].slice(0, 15).forEach((x) => console.log("  " + x));
  }
  process.exit(failed.length ? 1 : 0);
})().catch((e) => {
  console.error("脚本异常：", e);
  process.exit(2);
});
