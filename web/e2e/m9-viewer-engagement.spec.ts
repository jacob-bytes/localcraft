import { expect, test, type Page } from "@playwright/test";

import { ARTIFACTS, AUTHOR, VIEWER, login, openTool, watchPageErrors } from "./helpers";

/**
 * M9 验收 · `viewer` 的收藏 / 点赞可见性（CONTRACT §25.1）。
 *
 * ## 这一轮修的是什么
 *
 * §23.4 冻结 4 个收藏/点赞端点时**漏写了授权**，后端按「只读角色不能刷公共计数」
 * 实现（`engagement_guard`：`user` / `approver` / `superadmin`，**不含 `viewer`**，
 * 真机实测 viewer 得 403），前端却按「契约没限制角色」把按钮渲染给了所有人。
 * 裁定：后端胜出，**前端对 `viewer` 隐藏控件并给出原因**。
 *
 * 所以这里断言的是三件事，缺一不可：
 *  1. `viewer` 拿不到任何**可交互**的收藏/点赞控件（不是禁用一个仍会被读屏软件
 *     当按钮播报的元素，而是根本不渲染）；
 *  2. 计数照常可见（`favorite_count` / `like_count` 是全站公共数字）；
 *  3. `user` 的行为与 M8 逐字一致（乐观更新、刷新保持、没有多出说明文字）。
 */

/** 显式把 mock 的互动状态恢复成种子（需要页面已加载，MSW 才拦得到）。 */
async function resetMock(page: Page): Promise<void> {
  await page.evaluate(async () => {
    await fetch("/api/v1/__mock__/reset", { method: "POST" });
  });
}

/**
 * **卡片墙上**的工具卡片（整卡是拉伸链接，`data-tool-slug` 在卡片的包裹元素上）。
 *
 * M14 起门户首页还可能有「继续使用」/「我的收藏」横向区块，同一个 slug 会出现
 * 两张卡 —— 这里锚定 `ToolGrid` 的 `tool-grid`，指的就是卡片墙那张。
 */
function cardFor(page: Page, slug: string) {
  return page.locator(`[data-testid="tool-grid"] [data-tool-slug="${slug}"]`);
}

/** 从真实请求里抓一个 `Authorization` 头 —— access token 只在内存，脚本读不到。 */
function captureAuthHeader(page: Page): { current: () => string | null } {
  let token: string | null = null;
  page.on("request", (request) => {
    const header = request.headers()["authorization"];
    if (header?.startsWith("Bearer ")) token = header;
  });
  return { current: () => token };
}

/** 借页面内的同源 fetch（带上真实 token），核对原始响应。 */
async function api(
  page: Page,
  path: string,
  token: string | null,
  method: "GET" | "PUT" | "DELETE" = "GET",
): Promise<{ status: number; body: unknown }> {
  return page.evaluate(
    async ([url, auth, verb]) => {
      const response = await fetch(url as string, {
        method: verb as string,
        headers: auth ? { Authorization: auth as string } : {},
      });
      const text = await response.text();
      return { status: response.status, body: (text ? JSON.parse(text) : null) as unknown };
    },
    [path, token, method] as const,
  );
}

const ENGAGEMENT_CONTROLS = [
  '[data-testid="favorite-button"]',
  '[data-testid="card-favorite-button"]',
  '[data-testid="like-button"]',
  '[data-testid="card-like-button"]',
].join(", ");

/**
 * 「角色未加载完」那一态的探针：**记录控件是否在页面生命周期的任何一刻出现过**。
 *
 * 比「加载完成后断言不存在」更强 —— 后者对「先渲染、再撤掉」的闪烁是盲的。这里
 * 在 `addInitScript` 里挂 MutationObserver，逐个检查 `addedNodes`：只要某一帧插入了
 * 收藏/点赞控件，`seen` 就非空（即使它随后被移除，或在同一次批量回调里被移除）。
 */
async function installEngagementControlProbe(page: Page): Promise<void> {
  await page.addInitScript((selector: string) => {
    const seen: string[] = [];
    (window as unknown as { __engagementControlSeen?: string[] }).__engagementControlSeen =
      seen;
    const hit = (node: Node): boolean => {
      if (!(node instanceof Element)) return false;
      return node.matches(selector) || node.querySelector(selector) !== null;
    };
    const initial = document.querySelector(selector);
    if (initial) seen.push(`initial:${initial.getAttribute("data-testid") ?? "?"}`);
    new MutationObserver((records) => {
      for (const record of records) {
        for (const node of Array.from(record.addedNodes)) {
          if (!hit(node)) continue;
          const testId =
            node instanceof Element
              ? (node.getAttribute("data-testid") ??
                node.querySelector(selector)?.getAttribute("data-testid"))
              : null;
          seen.push(`added:${testId ?? "?"}`);
        }
      }
    }).observe(document, { childList: true, subtree: true });
  }, ENGAGEMENT_CONTROLS);
}

async function probedControls(page: Page): Promise<string[]> {
  return page.evaluate(
    () => (window as unknown as { __engagementControlSeen?: string[] }).__engagementControlSeen ?? [],
  );
}

test.describe("M9 · viewer 的收藏 / 点赞可见性（CONTRACT §25.1）", () => {
  test("M9 · viewer：门户卡片与详情页都没有可交互的收藏/点赞控件，计数照常可见，并给出权限原因", async ({
    page,
  }) => {
    const errors = watchPageErrors(page);
    await login(page, VIEWER);
    await resetMock(page);
    await page.goto("/");
    await expect(cardFor(page, "log-analyzer-a3f2")).toBeVisible();

    /* ---- ① 门户卡片：有计数、没有星标按钮 ---- */
    const card = cardFor(page, "log-analyzer-a3f2");
    // 计数是全站公共数字，viewer 照常看得到（§25.1 ③）
    await expect(card.getByTestId("favorite-count-value")).toHaveText("5");
    await expect(card.getByTestId("like-count-value")).toHaveText("8");
    await expect(card.getByTestId("card-favorite-button")).toHaveCount(0);
    await expect(card.getByTestId("card-like-button")).toHaveCount(0);

    /* ---- ② 左栏与用户菜单里的「我的收藏」入口对 viewer 也隐藏 ---- */
    await expect(page.getByTestId("nav-my-favorites")).toHaveCount(0);
    await page.getByTestId("user-menu-trigger").click();
    await expect(page.getByRole("menuitem", { name: "我的收藏" })).toHaveCount(0);
    await page.keyboard.press("Escape");

    /* ---- ③ 详情页：没有切换控件，但有权限说明；计数仍在信息栏 ---- */
    await openTool(page, "log-analyzer-a3f2");
    await expect(page.getByTestId("favorite-button")).toHaveCount(0);
    await expect(page.getByTestId("like-button")).toHaveCount(0);

    const note = page.getByTestId("engagement-permission-note");
    await expect(note).toBeVisible();
    await expect(note).toContainText("当前角色无收藏、点赞权限");
    await expect(note).toContainText("仅可查看计数");
    // 说明**不是控件**：读屏软件读到的是段落文字，不是一个不可用的按钮
    expect(await note.evaluate((element) => element.tagName)).toBe("P");
    expect(await note.getAttribute("tabindex")).toBeNull();
    // 文案不泄漏内部角色名（与「当前角色无下载权限」同一句式）
    await expect(note).not.toContainText("viewer");
    await expect(note).not.toContainText("只读访客");

    const sidebar = page.getByTestId("tool-detail");
    await expect(sidebar.locator("dt", { hasText: /^收藏$/ })).toBeVisible();
    await expect(sidebar.locator("dt", { hasText: /^点赞$/ })).toBeVisible();
    // 计数是真的数字（不是被隐藏的占位）：读 `dt` 的下一个兄弟 `dd`
    await expect(
      sidebar.locator('xpath=//dt[normalize-space(text())="收藏"]/following-sibling::dd[1]'),
    ).toHaveText("5");
    await expect(
      sidebar.locator('xpath=//dt[normalize-space(text())="点赞"]/following-sibling::dd[1]'),
    ).toHaveText("8");

    /* ---- ④ 直达 `/me/favorites`：角色门给出「没有访问权限」，而不是空收藏夹 ---- */
    await page.goto("/me/favorites");
    await expect(page.getByText("没有访问权限")).toBeVisible();
    await expect(page.getByTestId("my-favorites-page")).toHaveCount(0);

    await page.screenshot({ path: `${ARTIFACTS}/m9-viewer-no-engagement.png`, fullPage: true });
    expect(errors).toEqual([]);
  });

  test("M9 · viewer：整个页面生命周期里控件从未出现过（角色未加载完也不闪）", async ({ page }) => {
    const errors = watchPageErrors(page);
    await login(page, VIEWER);
    await installEngagementControlProbe(page);

    // 刷新：`/auth/refresh` 有一个真实的往返窗口，期间 `status === "unknown"`。
    await page.reload();
    // 等到角色已经确定（用户菜单出现 ⇒ `/auth/refresh` 已落地并 setUser）
    await expect(page.getByTestId("user-menu-trigger")).toBeVisible();
    await expect(cardFor(page, "log-analyzer-a3f2")).toBeVisible();
    await expect(page.getByTestId("portal-side-nav")).toHaveCount(0);

    // 探针是**累积**的：任何一帧插入过控件都会被记下来
    expect(await probedControls(page)).toEqual([]);

    await openTool(page, "log-analyzer-a3f2");
    await expect(page.getByTestId("engagement-permission-note")).toBeVisible();
    expect(await probedControls(page)).toEqual([]);

    expect(errors).toEqual([]);
  });

  test("M9 · mock 与真机同权：viewer 的收藏/点赞写端点 403，只读的 /me/favorites 仍 200", async ({
    page,
  }) => {
    /*
     * 这一条**故意**打出 403，所以登记成预期失败。
     *
     * 为什么值得写：M8 的 mock 只挡了匿名，viewer 在 mock 里能收藏成功，而真机是
     * 403 —— 正是这层差异让「viewer 点收藏必报错」在 mock E2E 里复现不出来。
     * 这条断言把 mock 钉在真机的授权上（`engagement_guard`），防止再次漂移。
     */
    const errors = watchPageErrors(page, {
      allow: (status, url) =>
        status === 403 && (url.includes("/favorite") || url.includes("/like")),
    });
    const auth = captureAuthHeader(page);
    await login(page, VIEWER);
    // 等到确实抓到过 token（登录后的第一个带鉴权请求）
    await expect(cardFor(page, "log-analyzer-a3f2")).toBeVisible();
    const token = auth.current();
    expect(token, "登录后应抓到 Authorization 头").not.toBeNull();

    const favorite = await api(page, "/api/v1/tools/log-analyzer-a3f2/favorite", token, "PUT");
    expect(favorite.status, "viewer 不能收藏（与真机一致）").toBe(403);
    const like = await api(page, "/api/v1/tools/log-analyzer-a3f2/like", token, "PUT");
    expect(like.status, "viewer 不能点赞（与真机一致）").toBe(403);

    // 读接口仍然放行（§25.1：`GET /me/favorites` 真机是 200）
    const favorites = await api(page, "/api/v1/me/favorites", token);
    expect(favorites.status).toBe(200);

    // 403 之后页面没有被搞坏：计数与说明照旧
    await page.reload();
    await expect(cardFor(page, "log-analyzer-a3f2").getByTestId("favorite-count-value")).toHaveText(
      "5",
    );
    expect(errors).toEqual([]);
  });

  test("M9 · user 的收藏行为未被改动：控件在、乐观更新在、没有多出权限说明", async ({ page }) => {
    const errors = watchPageErrors(page);
    await login(page, AUTHOR);
    await resetMock(page);
    await page.goto("/");

    const card = cardFor(page, "deploy-assistant");
    const button = card.getByTestId("card-favorite-button");
    const count = card.getByTestId("favorite-count-value");

    // 与 M8 完全一致：按钮在、初始未收藏、计数是种子值
    await expect(button).toHaveAttribute("aria-pressed", "false");
    await expect(count).toHaveText("3");
    // `user` 不会看到那行「无权限」说明
    await expect(page.getByTestId("engagement-permission-note")).toHaveCount(0);

    const settled = page.waitForResponse(
      (response) =>
        response.url().includes("/favorite") && response.request().method() === "PUT",
    );
    await button.click();
    expect((await settled).status()).toBe(200);
    await expect(button).toHaveAttribute("aria-pressed", "true");
    await expect(count).toHaveText("4");

    // 刷新后保持（乐观更新语义未变）
    await page.reload();
    const afterReload = cardFor(page, "deploy-assistant");
    await expect(afterReload.getByTestId("card-favorite-button")).toHaveAttribute(
      "aria-pressed",
      "true",
    );
    await expect(afterReload.getByTestId("favorite-count-value")).toHaveText("4");

    // 详情页也有切换控件（两个位置都没有被误伤）
    await openTool(page, "deploy-assistant");
    await expect(page.getByTestId("favorite-button")).toBeVisible();
    await expect(page.getByTestId("like-button")).toBeVisible();
    await expect(page.getByTestId("engagement-permission-note")).toHaveCount(0);

    expect(errors).toEqual([]);
  });

  test("M9 · 主题三态：权限说明在浅色 / 深色 / 跟随系统下都可读（对比度 ≥ 4.5）", async ({
    page,
  }) => {
    /*
     * 无法读取图像 ⇒ 不做「看起来对不对」的判断。这里用**可计算的**判据代替：
     * `<html class="dark">` 的实际状态（确定性），以及说明文字与它实际背景的
     * WCAG 对比度（用 canvas 把任意 CSS 颜色解析成 sRGB 后计算）。
     */
    const errors = watchPageErrors(page);
    await login(page, VIEWER);

    const cases = [
      { mode: "light", scheme: "light", expectDarkClass: false },
      { mode: "dark", scheme: "light", expectDarkClass: true },
      { mode: "system", scheme: "dark", expectDarkClass: true },
      { mode: "system", scheme: "light", expectDarkClass: false },
    ] as const;

    for (const item of cases) {
      await page.emulateMedia({ colorScheme: item.scheme });
      /*
       * 主题的落地方式是「localStorage + `<html class="dark">`」，由 index.html 的
       * 阻塞内联脚本在首帧前应用。所以这里先在同源页面写入取值，再**整页导航**
       * （新文档才会重跑那段内联脚本），而不是靠 addInitScript 的注册顺序。
       */
      await page.goto("/");
      await page.evaluate((mode: string) => {
        window.localStorage.setItem("localcraft-theme", mode);
      }, item.mode);
      await page.goto("/tools/log-analyzer-a3f2");
      await expect(page.getByTestId("engagement-permission-note")).toBeVisible();

      const observed = await page.evaluate(() => {
        const note = document.querySelector('[data-testid="engagement-permission-note"]');
        if (!note) return null;
        const canvas = document.createElement("canvas");
        canvas.width = 1;
        canvas.height = 1;
        const context = canvas.getContext("2d", { willReadFrequently: true });
        if (!context) return null;
        /*
         * 颜色取值用 **canvas 真实渲染出的像素**，而不是自己解析
         * `getComputedStyle().color` 的字符串：令牌是 `oklch(...)`，Chrome 原样返回
         * 该语法，任何手写解析都可能悄悄算错（第一版就踩了）。`getImageData` 给的是
         * sRGB 字节，等于浏览器自己的转换结果。
         */
        const toRgb = (cssColor: string): [number, number, number, number] => {
          context.clearRect(0, 0, 1, 1);
          context.fillStyle = cssColor;
          context.fillRect(0, 0, 1, 1);
          const data = context.getImageData(0, 0, 1, 1).data;
          return [data[0] ?? 0, data[1] ?? 0, data[2] ?? 0, data[3] ?? 0];
        };
        const luminance = ([r, g, b]: [number, number, number, number]): number => {
          const channel = (value: number): number => {
            const scaled = value / 255;
            return scaled <= 0.03928 ? scaled / 12.92 : ((scaled + 0.055) / 1.055) ** 2.4;
          };
          return 0.2126 * channel(r) + 0.7152 * channel(g) + 0.0722 * channel(b);
        };

        const color = window.getComputedStyle(note).color;
        const foreground = toRgb(color);
        let background: [number, number, number, number] | null = null;
        let node: Element | null = note;
        while (node) {
          const candidate = window.getComputedStyle(node).backgroundColor;
          if (candidate && candidate !== "rgba(0, 0, 0, 0)" && candidate !== "transparent") {
            const resolved = toRgb(candidate);
            if (resolved[3] > 0) {
              background = resolved;
              break;
            }
          }
          node = node.parentElement;
        }
        const effectiveBackground: [number, number, number, number] = background ?? [255, 255, 255, 255];
        const foregroundLuminance = luminance(foreground);
        const backgroundLuminance = luminance(effectiveBackground);
        const lighter = Math.max(foregroundLuminance, backgroundLuminance);
        const darker = Math.min(foregroundLuminance, backgroundLuminance);
        return {
          dark: document.documentElement.classList.contains("dark"),
          contrast: (lighter + 0.05) / (darker + 0.05),
          color,
          foreground: `rgb(${foreground[0]}, ${foreground[1]}, ${foreground[2]})`,
          background: `rgb(${effectiveBackground[0]}, ${effectiveBackground[1]}, ${effectiveBackground[2]})`,
        };
      });

      expect(observed, `${item.mode}：说明元素应存在`).not.toBeNull();
      expect(observed?.dark, `${item.mode} 下 <html> 的 dark 类`).toBe(item.expectDarkClass);
      expect(
        observed?.contrast ?? 0,
        `${item.mode} 下说明文字与背景的对比度（前景 ${observed?.foreground ?? "?"} / 背景 ${
          observed?.background ?? "?"
        }，${observed?.color ?? "?"}）`,
      ).toBeGreaterThanOrEqual(4.5);
      // 任何主题下都不给可交互控件
      await expect(page.getByTestId("favorite-button")).toHaveCount(0);
      await expect(page.getByTestId("like-button")).toHaveCount(0);
    }

    expect(errors).toEqual([]);
  });
});
