import { expect, test, type Page } from "@playwright/test";

/**
 * CONTRACT §8 acceptance walkthrough for M1, driven against MSW mocks
 * (`VITE_ENABLE_MOCKS=true`, the dev default). Items owned by the frontend:
 * 2, 3, 4, 5, 6, 7, 8, 9, 13 — plus 10/11/12 which are cheap to cover here.
 *
 * Screenshots land in `e2e/artifacts/` (git-ignored).
 */

declare global {
  interface Window {
    __seen?: { login: boolean; loader: boolean };
    __firstFrame?: { className: string; bodyBackground: string };
  }
}

const ARTIFACTS = "e2e/artifacts";

/** Records whether the login page or the session loader was ever painted. */
async function installFlashProbe(page: Page): Promise<void> {
  await page.addInitScript(() => {
    const seen = { login: false, loader: false };
    window.__seen = seen;
    const check = () => {
      if (document.querySelector('[data-testid="login-page"]')) seen.login = true;
      if (document.querySelector('[data-testid="full-screen-loader"]')) seen.loader = true;
    };
    check();
    new MutationObserver(check).observe(document, {
      childList: true,
      subtree: true,
      attributes: true,
    });
  });
}

/**
 * Proves the dark class is applied by the blocking inline script in index.html
 * rather than by React: with the app bundle blocked, React never mounts, so a
 * dark <html> can only come from that script — i.e. before the first paint.
 */
async function assertInlineThemeBootstrap(page: Page): Promise<void> {
  await page.route("**/src/main.tsx", (route) => route.abort());
  await page.reload();
  await page.waitForLoadState("domcontentloaded");
  const state = await page.evaluate(() => ({
    className: document.documentElement.className,
    colorScheme: document.documentElement.style.colorScheme,
    hasAppRoot: (document.getElementById("root")?.childElementCount ?? 0) > 0,
  }));
  await page.unroute("**/src/main.tsx");
  expect(state.hasAppRoot).toBe(false);
  expect(state.className).toContain("dark");
  expect(state.colorScheme).toBe("dark");
}

/**
 * Collects genuine errors, ignoring the two classes of noise that are expected
 * in an auth flow:
 *  - browsers log every non-2xx response as a `console.error`, and we then see
 *    the same failure through the `response` listener with a URL attached;
 *  - the deliberate auth failures below (boot refresh with no session, wrong
 *    password, wrong old password) are asserted on explicitly by the tests.
 */
const EXPECTED_FAILING_ENDPOINTS = [
  "/api/v1/auth/refresh",
  "/api/v1/auth/login",
  "/api/v1/auth/change-password",
];

function watchPageErrors(
  page: Page,
  options: { allowConsole?: (text: string) => boolean } = {},
): string[] {
  const errors: string[] = [];
  page.on("pageerror", (error) => errors.push(`pageerror: ${error.message}`));
  page.on("response", (response) => {
    const status = response.status();
    if (status < 400) return;
    const url = response.url();
    if (EXPECTED_FAILING_ENDPOINTS.some((endpoint) => url.includes(endpoint))) return;
    errors.push(`HTTP ${status} ${url}`);
  });
  page.on("console", (message) => {
    if (message.type() !== "error") return;
    const text = message.text();
    if (/Failed to load resource: the server responded with a status of \d+/.test(text)) return;
    if (options.allowConsole?.(text)) return;
    errors.push(`console.error: ${text}`);
  });
  return errors;
}

async function login(
  page: Page,
  username: string,
  password: string,
  options: { goto?: boolean } = {},
) {
  // `goto: false` keeps an existing `/login?redirect=…` (a shared deep link)
  // instead of overwriting it with a plain "/".
  if (options.goto ?? true) await page.goto("/");
  await expect(page.getByTestId("login-page")).toBeVisible();
  await page.getByLabel("用户名", { exact: true }).fill(username);
  await page.getByLabel("密码", { exact: true }).fill(password);
  await page.getByRole("button", { name: "登录", exact: true }).click();
}

/**
 * 期望的卡片数从**页面工具栏**的「共 N 个工具」读取。
 *
 * 为什么不用 in-page fetch：mock 的 access token 只存在内存，页面刷新或改密后
 * 之前抓到的 token 会失效（401），而 HMR 的模块 query 又让动态 import 可能拿到
 * 另一个 client 实例。工具栏的 total 与卡片来自**同一个查询响应**，天然一致。
 */
async function expectedCards(page: Page, pageSize = 24): Promise<number> {
  const label = page.getByText(/共 \d+ 个工具/).first();
  await expect(label).toBeVisible();
  const text = await label.innerText();
  const match = /共 (\d+) 个工具/.exec(text);
  if (!match?.[1]) throw new Error(`工具栏未显示总数："${text}"`);
  return Math.min(Number.parseInt(match[1], 10), pageSize);
}

test.describe("M1 验收（MSW mock 状态）", () => {

  test("2. 未登录访问门户 → 跳转 /login，无空白页", async ({ page }) => {
    const errors = watchPageErrors(page);
    await page.goto("/");
    await expect(page).toHaveURL(/\/login/);
    await expect(page.getByTestId("login-page")).toBeVisible();
    await expect(page.getByRole("button", { name: "登录", exact: true })).toBeVisible();
    expect(await page.locator("main, form").count()).toBeGreaterThan(0);
    await page.screenshot({ path: `${ARTIFACTS}/02-login.png`, fullPage: true });
    expect(errors).toEqual([]);
  });

  test("3. admin 登录 → 门户 + 顶栏角色与头像 + 8 张卡片", async ({ page }) => {
    const errors = watchPageErrors(page);
    await login(page, "admin", "Admin@12345");
    await expect(page).toHaveURL(/\/$/);
    await expect(page.getByRole("heading", { name: "发现内网工具与 Skill" })).toBeVisible();

    const nav = page.getByTestId("top-nav");
    await expect(nav).toContainText("超级管理员");
    await expect(nav.getByTestId("user-menu-trigger")).toContainText("管");

    await expect(page.getByTestId("tool-card")).toHaveCount(await expectedCards(page));
    await page.screenshot({ path: `${ARTIFACTS}/03-portal-admin.png`, fullPage: true });
    expect(errors).toEqual([]);
  });

  test("4. F5 刷新仍在门户，且从未渲染登录页（unknown 守卫态存在）", async ({ page }) => {
    const errors = watchPageErrors(page);
    await login(page, "admin", "Admin@12345");
    await expect(page.getByTestId("tool-card")).toHaveCount(await expectedCards(page));

    await installFlashProbe(page);
    await page.reload();

    await expect(page.getByRole("heading", { name: "发现内网工具与 Skill" })).toBeVisible();
    await expect(page.getByTestId("tool-card")).toHaveCount(await expectedCards(page));

    const seen = await page.evaluate(() => window.__seen);
    expect(seen).toEqual({ login: false, loader: true });
    await page.screenshot({ path: `${ARTIFACTS}/04-after-reload.png`, fullPage: true });
    expect(errors).toEqual([]);
  });

  test("5+6. 筛选/搜索/排序/分页全部落到 URL，链接可还原", async ({ page, context }) => {
    const errors = watchPageErrors(page);
    await login(page, "admin", "Admin@12345");
    await expect(page.getByTestId("tool-card")).toHaveCount(await expectedCards(page));

    // 分类单选
    await page
      .getByRole("navigation", { name: "按分类筛选" })
      .getByRole("button", { name: /研发工具/ })
      .click();
    await expect(page).toHaveURL(/category=dev-tools/);
    await expect(page.getByTestId("tool-card")).toHaveCount(await expectedCards(page));

    // 类型多选
    await page.getByRole("checkbox", { name: /文件包/ }).check();
    await expect(page).toHaveURL(/type=file/);
    await expect(page.getByTestId("tool-card")).toHaveCount(await expectedCards(page));

    // 清除全部
    await page.getByRole("button", { name: "清除全部" }).click();
    await expect(page).not.toHaveURL(/type=file/);
    await expect(page.getByTestId("tool-card")).toHaveCount(await expectedCards(page));

    // 标签筛选
    await page.getByRole("button", { name: /#k8s/ }).click();
    await expect(page).toHaveURL(/tag=k8s/);
    await expect(page.getByTestId("tool-card")).toHaveCount(await expectedCards(page));

    // 搜索（防抖 300ms 后写入 URL）
    await page.getByRole("button", { name: "清除全部" }).click();
    await page.getByRole("searchbox", { name: "搜索工具" }).fill("Nginx");
    await expect(page).toHaveURL(/q=Nginx/);
    // mock 里只有「日志分析器」的简介含 Nginx
    await expect(page.getByTestId("tool-card")).toHaveCount(1);
    await expect(page.getByTestId("tool-card")).toContainText("日志分析器");

    // 已选条件 Badge 集合
    await expect(page.getByText("已选条件")).toBeVisible();

    // 排序 + 每页条数
    await page.getByRole("combobox", { name: "排序方式" }).click();
    await page.getByRole("option", { name: "名称" }).click();
    await expect(page).toHaveURL(/sort=name/);

    await page.getByRole("button", { name: "清除全部" }).click();
    await page.getByRole("combobox", { name: "每页条数" }).click();
    await page.getByRole("option", { name: "每页 12" }).click();
    await expect(page).toHaveURL(/page_size=12/);
    await expect(page.getByTestId("tool-card")).toHaveCount(12);

    // 分享链接：复制当前 URL 到新的浏览器上下文，结果一致
    await page
      .getByRole("navigation", { name: "按分类筛选" })
      .getByRole("button", { name: /运维工具/ })
      .click();
    await page.getByRole("combobox", { name: "排序方式" }).click();
    await page.getByRole("option", { name: "热门" }).click();
    await expect(page.getByTestId("tool-card").first()).toBeVisible();
    const opsCards = await expectedCards(page);
    await expect(page.getByTestId("tool-card")).toHaveCount(opsCards);
    const sharedUrl = page.url();
    expect(sharedUrl).toContain("category=ops-tools");
    expect(sharedUrl).toContain("sort=hot");

    const fresh = await context.browser()?.newContext({ locale: "zh-CN" });
    expect(fresh).toBeTruthy();
    const sharedPage = await fresh!.newPage();
    // a fresh context has no refresh cookie → the shared link must land on /login
    await sharedPage.goto(sharedUrl);
    await expect(sharedPage).toHaveURL(/\/login\?redirect=/);
    await login(sharedPage, "admin", "Admin@12345", { goto: false });
    await expect(sharedPage).toHaveURL(/category=ops-tools/);
    await expect(sharedPage.getByTestId("tool-card")).toHaveCount(opsCards);
    await sharedPage.screenshot({ path: `${ARTIFACTS}/06-shared-link.png`, fullPage: true });
    await fresh!.close();

    // 越界页码：后端返回空数组 → 空态，但分页控件仍在（docs/04 §6.3）
    await page.goto("/?page=9");
    await expect(page.getByText("平台上还没有工具")).toBeVisible();
    await expect(page.getByRole("navigation", { name: "分页" })).toBeVisible();
    await expect(page.getByRole("button", { name: "上一页" })).toBeEnabled();

    // 有筛选但无结果：给出「清除筛选条件」出口
    await page.goto("/?q=zzzz-no-such-tool");
    await expect(page.getByText("没有找到匹配的工具")).toBeVisible();
    await page.getByRole("button", { name: "清除筛选条件" }).click();
    await expect(page.getByTestId("tool-card")).toHaveCount(await expectedCards(page));

    // 刷新不丢筛选
    await page.goto(sharedUrl);
    await expect(page.getByTestId("tool-card")).toHaveCount(opsCards);
    await page.reload();
    await expect(page.getByTestId("tool-card")).toHaveCount(opsCards);
    await expect(page).toHaveURL(/category=ops-tools/);

    expect(errors).toEqual([]);
  });

  test("6.1 契约：翻页时 facets 键仍存在且为 null；access token 只在内存", async ({ page }) => {
    const errors = watchPageErrors(page);
    await login(page, "admin", "Admin@12345");
    await expect(page.getByTestId("tool-card")).toHaveCount(await expectedCards(page));

    const probe = await page.evaluate(async () => {
      // Reach into the dev module graph to grab the in-memory token — that is
      // exactly the property under test: it exists at runtime but nowhere in
      // storage (CONTRACT §3.1).
      const modulePath = "/src/api/" + "client.ts";
      const client = (await import(/* @vite-ignore */ modulePath)) as {
        getAccessToken: () => string | null;
      };
      const token = client.getAccessToken();
      const call = async (url: string) => {
        const response = await fetch(url, {
          headers: token ? { Authorization: `Bearer ${token}` } : {},
        });
        return {
          status: response.status,
          body: (await response.json()) as Record<string, unknown>,
        };
      };
      const storage = `${JSON.stringify(window.localStorage)}|${JSON.stringify(window.sessionStorage)}`;
      return {
        page1: await call("/api/v1/tools?page=1&page_size=3"),
        page2: await call("/api/v1/tools?page=2&page_size=3"),
        tokenIsInMemory: typeof token === "string" && token.length > 0,
        tokenAbsentFromStorage: token === null || !storage.includes(token),
      };
    });

    expect(probe.page1.status).toBe(200);
    expect(probe.page1.body.facets).not.toBeNull();
    expect(probe.page2.status).toBe(200);
    // docs/03 §3.3 (M1 checkpoint ruling): the key is always present, null when paging.
    expect(Object.prototype.hasOwnProperty.call(probe.page2.body, "facets")).toBe(true);
    expect(probe.page2.body.facets).toBeNull();
    expect(probe.page1.body.items).toHaveLength(3);
    expect(probe.tokenIsInMemory).toBe(true);
    expect(probe.tokenAbsentFromStorage).toBe(true);
    expect(errors).toEqual([]);
  });

  test("7. 无封面工具显示占位色块 + 类型图标，且没有破图", async ({ page }) => {
    const errors = watchPageErrors(page);
    await login(page, "admin", "Admin@12345");
    await expect(page.getByTestId("tool-card")).toHaveCount(await expectedCards(page));

    const noCover = page.locator('[data-tool-slug="code-review-skill"]');
    await expect(noCover.getByTestId("cover-placeholder")).toBeVisible();
    await expect(noCover.getByTestId("cover-image")).toHaveCount(0);

    const withCover = page.locator('[data-tool-slug="log-analyzer-a3f2"]');
    await expect(withCover.getByTestId("cover-image")).toBeVisible();

    // every <img> must actually have decoded (no broken image)
    const broken = await page.evaluate(() =>
      Array.from(document.images)
        .filter((image) => image.complete && image.naturalWidth === 0)
        .map((image) => image.currentSrc || image.src),
    );
    expect(broken).toEqual([]);

    await page.screenshot({ path: `${ARTIFACTS}/07-cover-placeholder.png`, fullPage: true });
    expect(errors).toEqual([]);

    // Even when a cover URL fails (404 / restricted visibility / the M2-only
    // image endpoint), the card must degrade to the placeholder rather than
    // keep a broken <img>. `onError` is what turns that into the placeholder.
    await page.evaluate(() => {
      for (const image of document.querySelectorAll('[data-testid="cover-image"]')) {
        image.dispatchEvent(new Event("error", { bubbles: true }));
      }
    });
    await expect(page.getByTestId("cover-image")).toHaveCount(0);
    await expect(page.getByTestId("cover-placeholder")).toHaveCount(
      await expectedCards(page),
    );
    await expect(withCover.getByTestId("cover-placeholder")).toBeVisible();
  });

  test("8. 超长名称单行省略、简介两行省略、卡片高度一致", async ({ page }) => {
    const errors = watchPageErrors(page);
    await login(page, "admin", "Admin@12345");
    await expect(page.getByTestId("tool-card")).toHaveCount(await expectedCards(page));

    const longCard = page.locator('[data-tool-slug="gray-release-orchestrator"]');
    const name = longCard.getByTestId("tool-card-name");
    const summary = longCard.getByTestId("tool-card-summary");

    await expect(name).toBeVisible();
    expect(await name.evaluate((element) => getComputedStyle(element).webkitLineClamp)).toBe("1");
    expect(await summary.evaluate((element) => getComputedStyle(element).webkitLineClamp)).toBe(
      "2",
    );

    const nameBox = await name.boundingBox();
    const summaryBox = await summary.boundingBox();
    const nameLine = await name.evaluate((element) =>
      Number.parseFloat(getComputedStyle(element).lineHeight),
    );
    const summaryLine = await summary.evaluate((element) =>
      Number.parseFloat(getComputedStyle(element).lineHeight),
    );
    expect(nameBox!.height).toBeLessThan(nameLine * 1.6);
    expect(summaryBox!.height).toBeLessThan(summaryLine * 2 + 8);

    // Cards in the same grid row must have identical heights; different rows may
    // legitimately differ (a row with a two-line tag list is taller).
    const rows = await page.getByTestId("tool-card").evaluateAll((cards) => {
      const grouped: Record<string, number[]> = {};
      for (const card of cards) {
        const box = card.getBoundingClientRect();
        const key = String(Math.round(box.top));
        grouped[key] = [...(grouped[key] ?? []), box.height];
      }
      return Object.values(grouped);
    });
    expect(rows.length).toBeGreaterThan(1);
    for (const heights of rows) {
      expect(Math.max(...heights) - Math.min(...heights)).toBeLessThan(2);
    }

    await page.screenshot({ path: `${ARTIFACTS}/08-truncation.png`, fullPage: true });
    expect(errors).toEqual([]);
  });

  test("9. 登出回到 /login，再刷新不能恢复会话", async ({ page }) => {
    const errors = watchPageErrors(page);
    await login(page, "admin", "Admin@12345");
    await expect(page.getByTestId("tool-card")).toHaveCount(await expectedCards(page));

    await page.getByTestId("user-menu-trigger").click();
    await page.getByRole("menuitem", { name: "退出登录" }).click();
    await expect(page).toHaveURL(/\/login/);
    await expect(page.getByTestId("login-page")).toBeVisible();

    await page.reload();
    await expect(page).toHaveURL(/\/login/);
    await expect(page.getByTestId("login-page")).toBeVisible();

    // direct navigation to the portal must bounce back to /login too
    await page.goto("/");
    await expect(page).toHaveURL(/\/login/);
    await page.screenshot({ path: `${ARTIFACTS}/09-logout.png`, fullPage: true });
    expect(errors).toEqual([]);
  });

  test("10+11. newbie 强制改密：导航隐藏 → 改密成功回登录页 → 新密码可登录", async ({ page }) => {
    const errors = watchPageErrors(page);
    await login(page, "newbie", "Newbie@12345");

    await expect(page).toHaveURL(/\/change-password/);
    await expect(page.getByText("请先修改初始密码")).toBeVisible();
    // 顶栏导航项全部隐藏
    await expect(page.getByTestId("top-nav")).toContainText("需先修改密码");
    await expect(page.getByLabel("搜索工具（快捷键 Command K）")).toHaveCount(0);
    // 强制模式没有「跳过」
    await expect(page.getByRole("button", { name: "取消" })).toHaveCount(0);

    // 直接访问门户会被弹回改密页
    await page.goto("/");
    await expect(page).toHaveURL(/\/change-password/);

    await page.getByLabel("初始密码").fill("Newbie@12345");
    await page.getByLabel("新密码", { exact: true }).fill("Newbie@2025!");
    await page.getByLabel("确认新密码").fill("Newbie@2025!");
    await expect(page.getByText(/强度：很强|强度：强/)).toBeVisible();
    await page.screenshot({ path: `${ARTIFACTS}/10-force-change-password.png`, fullPage: true });
    await page.getByRole("button", { name: "确认修改" }).click();

    await expect(page).toHaveURL(/\/login/);
    await login(page, "newbie", "Newbie@2025!");
    await expect(page.getByTestId("tool-card")).toHaveCount(await expectedCards(page));
    expect(errors).toEqual([]);
  });

  test("12. 错误密码：凭证错误不清空密码框；连续 5 次后锁定倒计时", async ({ page }) => {
    const errors = watchPageErrors(page);
    await page.goto("/");
    await page.getByLabel("用户名", { exact: true }).fill("admin");

    for (let attempt = 1; attempt <= 5; attempt += 1) {
      await page.getByLabel("密码", { exact: true }).fill("WrongPass!1");
      await page.getByRole("button", { name: "登录", exact: true }).click();
      await expect(page.getByText("用户名或密码错误")).toBeVisible();
      // 密码框内容必须保留
      await expect(page.getByLabel("密码", { exact: true })).toHaveValue("WrongPass!1");
    }

    await page.getByLabel("密码", { exact: true }).fill("Admin@12345");
    await page.getByRole("button", { name: /登录|账号锁定中/ }).click();
    await expect(page.getByText("账号已锁定", { exact: true })).toBeVisible();
    await expect(page.getByText(/请 \d{2}:\d{2} 后重试/)).toBeVisible();
    await expect(page.getByRole("button", { name: /账号锁定中/ })).toBeDisabled();
    await page.screenshot({ path: `${ARTIFACTS}/12-account-locked.png`, fullPage: true });
    expect(errors).toEqual([]);
  });

  test("13. 深色主题刷新后保持，且首帧就是深色（无白闪）", async ({ page }) => {
    // 这个用例会**故意** abort /src/main.tsx 来证明内联脚本独立生效，
    // 浏览器为此记一条 net::ERR_FAILED，不算缺陷。
    const errors = watchPageErrors(page, {
      allowConsole: (text) => text.includes("net::ERR_FAILED"),
    });
    await page.emulateMedia({ colorScheme: "light" });
    await login(page, "admin", "Admin@12345");
    await expect(page.getByTestId("tool-card")).toHaveCount(await expectedCards(page));

    await page.getByLabel(/切换主题/).click();
    await page.getByRole("menuitemradio", { name: "深色" }).click();
    await expect(page.locator("html")).toHaveClass(/dark/);
    await page.screenshot({ path: `${ARTIFACTS}/13-dark-portal.png`, fullPage: true });

    await page.reload();
    await expect(page.getByTestId("tool-card")).toHaveCount(await expectedCards(page));
    await expect(page.locator("html")).toHaveClass(/dark/);

    // the dark background is actually applied to the painted page
    const background = await page.evaluate(
      () => getComputedStyle(document.body).backgroundColor,
    );
    // Chrome keeps oklch() as authored; other engines may normalise to rgb().
    const oklch = /^oklch\(([\d.]+)/.exec(background);
    if (oklch) {
      expect(Number(oklch[1])).toBeLessThan(0.4); // --background: oklch(0.17 …)
    } else {
      const rgb = /rgba?\((\d+),\s*(\d+),\s*(\d+)/.exec(background);
      expect(rgb).not.toBeNull();
      const [r, g, b] = [Number(rgb![1]), Number(rgb![2]), Number(rgb![3])];
      expect(r + g + b).toBeLessThan(200);
    }

    // ...and it comes from the inline bootstrap, not from React (no white flash)
    await assertInlineThemeBootstrap(page);

    // and it survives one more reload (localStorage persisted, not just in-memory)
    await page.reload();
    await expect(page.locator("html")).toHaveClass(/dark/);
    await page.screenshot({ path: `${ARTIFACTS}/13-dark-after-reload.png`, fullPage: true });
    expect(errors).toEqual([]);
  });

  test("顶栏搜索：⌘K 唤起 Command 面板并跳到工具详情", async ({ page }) => {
    const errors = watchPageErrors(page);
    await login(page, "admin", "Admin@12345");
    await expect(page.getByTestId("tool-card")).toHaveCount(await expectedCards(page));

    await page.keyboard.press("ControlOrMeta+k");
    const dialog = page.getByRole("dialog");
    await expect(dialog).toBeVisible();
    await dialog.getByRole("combobox").fill("日志");
    const toolOption = dialog.getByRole("option").filter({ hasNotText: "在门户中搜索" }).first();
    await expect(toolOption).toBeVisible();
    await page.screenshot({ path: `${ARTIFACTS}/command-palette.png` });
    await toolOption.click();
    // M2 起选中结果直接进详情页（CONTRACT §14.10）；门户搜索仍可用 ?q= 直达
    await expect(page).toHaveURL(/\/tools\/[^?]+$/);
    await expect(page.getByTestId("tool-detail")).toBeVisible();
    expect(errors).toEqual([]);
  });

  test("响应式：<sm 单列 + 筛选折叠为 Sheet", async ({ page }) => {
    const errors = watchPageErrors(page);
    await page.setViewportSize({ width: 390, height: 844 });
    await login(page, "admin", "Admin@12345");
    await expect(page.getByTestId("tool-card")).toHaveCount(await expectedCards(page));

    await expect(page.getByRole("complementary", { name: "筛选条件" })).toBeHidden();
    await page.getByRole("button", { name: /筛选/ }).click();
    await expect(page.getByRole("dialog")).toBeVisible();
    await expect(page.getByRole("navigation", { name: "按分类筛选" })).toBeVisible();
    await page.screenshot({ path: `${ARTIFACTS}/responsive-sheet.png`, fullPage: true });
    await page.keyboard.press("Escape");

    const widths = await page
      .getByTestId("tool-card")
      .evaluateAll((cards) => cards.map((card) => card.getBoundingClientRect().width));
    expect(new Set(widths.map((width) => Math.round(width))).size).toBe(1);
    expect(errors).toEqual([]);
  });
});
