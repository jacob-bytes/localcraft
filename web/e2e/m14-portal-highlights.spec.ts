import { expect, test, type Page } from "@playwright/test";

import {
  AUTHOR,
  RECENT_TOOLS_KEY,
  contrastOf,
  login,
  setRecentTools,
  trackRequests,
  watchPageErrors,
  type RecentToolFixture,
} from "./helpers";

/**
 * M14 验收 · 门户首页两个区块 + 在线工具探活状态（CONTRACT §29.3 / §29.8）。
 *
 * 这一轮不做新功能，做的是**把已经存在的数据放到页面上**：
 *  - 「最近访问」早就存在 `localStorage`（M7 · F6），但只喂给 ⌘K 面板；
 *  - 「我的收藏」早就有接口（M8 §23.4），但只有专门页面。
 *
 * 因此本文件的重心全部在两个「不能做错」的地方：
 *
 *  1. **默认部署零变化**：没有收藏、也没访问过时，两个区块**一个元素都不产生**
 *     （不是「渲染了但隐藏」）。这一条用 DOM 结构证据钉住，见用例 1。
 *  2. **匿名不发 `/me/favorites`**：不白打一个必然 401 的请求。用请求监听钉住，
 *     见用例 2（匿名 = 0 次）与用例 3（登录 ≥ 1 次）这组对照。
 *
 * 探活部分（§29.3）另有一条容易做错的地方：`null`（从未检测过）**不是** `fail`，
 * 文案必须能区分，见用例 6。
 */

/* 8 个公开的边界工具（`BOUNDARY_TOOL_SEEDS`），全部匿名可见 —— 用来构造
   「最近访问」的确定性数据，也用来验「最多 6 个」。 */
const PUBLIC_SLUGS = [
  { slug: "log-analyzer-a3f2", name: "日志分析器" },
  { slug: "deploy-assistant", name: "部署助手" },
  { slug: "code-review-skill", name: "代码审查 Skill" },
  { slug: "inspection-scripts", name: "巡检脚本集" },
  { slug: "architecture-review-prompt", name: "架构评审提示词" },
  { slug: "openapi-doc-generator", name: "接口文档生成器" },
  { slug: "gray-release-orchestrator", name: "灰度发布编排" },
  { slug: "slow-sql-helper", name: "慢 SQL 助手" },
] as const;

/** 最近访问的形状与生产一致：`{slug, name, at}`，`at` 只是 epoch ms。 */
function recentFixtures(count: number): RecentToolFixture[] {
  return PUBLIC_SLUGS.slice(0, count).map((item, index) => ({
    slug: item.slug,
    name: item.name,
    // 越靠前越新（`readRecentTools` 与 `recordRecentTool` 的约定是 newest first）。
    at: 1_742_000_000_000 - index * 60_000,
  }));
}

const CONTINUE = '[data-testid="portal-continue-using"]';
const FAVORITES = '[data-testid="portal-favorites-highlight"]';

/** 门户根容器：hero `<section>` 的父节点（`PortalPage` 的最外层 div）。 */
function portalRootChildren(page: Page) {
  return page.evaluate(() => {
    const hero = document.querySelector('[aria-labelledby="portal-hero-title"]');
    const root = hero?.parentElement;
    if (!root) return null;
    return Array.from(root.children).map((child) => ({
      tag: child.tagName.toLowerCase(),
      label:
        child.getAttribute("aria-labelledby") ??
        child.getAttribute("aria-label") ??
        child.getAttribute("data-testid") ??
        "",
      className: child.getAttribute("class") ?? "",
    }));
  });
}

test.describe("M14 · 门户首页区块与探活状态", () => {
  test("1. 默认部署（无最近访问、无收藏）：两个区块一个元素都不产生（DOM 结构证据）", async ({
    page,
  }) => {
    const errors = watchPageErrors(page);
    const favoritesRequests = trackRequests(page, "/api/v1/me/favorites");

    await page.goto("/");
    await expect(page.getByTestId("tool-card").first()).toBeVisible();

    // ① 两个区块的容器节点都不存在（不是「存在但为空 / 隐藏」）。
    await expect(page.locator(CONTINUE)).toHaveCount(0);
    await expect(page.locator(FAVORITES)).toHaveCount(0);

    // ② 连标题文字都不存在 —— 「不放空标题」。
    await expect(page.getByRole("heading", { name: "继续使用" })).toHaveCount(0);
    await expect(page.getByRole("heading", { name: "我的收藏" })).toHaveCount(0);

    /*
     * ③ 最强的一条：门户根容器的**直接子元素正好是 3 个**，且顺序就是
     *    hero → 公告 → 内容网格。任何「空的外壳容器」都会让这个数字变成 4 ——
     *    那正是任务书禁止的「留空白间距」。这条断言不依赖我写了什么条件判断，
     *    它读的是真实 DOM。
     */
    const children = await portalRootChildren(page);
    expect(children, "门户根容器应可定位").not.toBeNull();
    expect(children ?? []).toHaveLength(3);
    expect(children?.[0]?.label).toBe("portal-hero-title");
    expect(children?.[1]?.label).toBe("公告");
    expect(children?.[2]?.tag).toBe("div");
    expect(children?.[2]?.className).toContain("lg:grid");

    // ④ 匿名不请求收藏（不白打 401）。
    expect(favoritesRequests.count()).toBe(0);
    expect(errors).toEqual([]);
  });

  test("2. 匿名：「继续使用」是本地数据所以照常显示；「我的收藏」不渲染且**不发请求**", async ({
    page,
  }) => {
    const errors = watchPageErrors(page);
    const favoritesRequests = trackRequests(page, "/api/v1/me/favorites");
    await setRecentTools(page, recentFixtures(2));

    await page.goto("/");
    await expect(page.getByTestId("tool-card").first()).toBeVisible();

    const continueSection = page.locator(CONTINUE);
    await expect(continueSection).toBeVisible();
    await expect(
      continueSection.getByRole("heading", { name: "继续使用" }),
    ).toBeVisible();
    await expect(continueSection.locator("[data-tool-slug]")).toHaveCount(2);
    await expect(
      continueSection.locator('[data-tool-slug="log-analyzer-a3f2"]'),
    ).toBeVisible();
    await expect(
      continueSection.locator('[data-tool-slug="deploy-assistant"]'),
    ).toBeVisible();

    // 匿名看不到「我的收藏」：既不渲染区块，也不发那个请求（对照用例 3）。
    await expect(page.locator(FAVORITES)).toHaveCount(0);
    await expect(page.getByRole("link", { name: "我的收藏" })).toHaveCount(0);
    expect(favoritesRequests.count(), "匿名不得请求 /me/favorites").toBe(0);
    expect(errors).toEqual([]);
  });

  test("3. 登录且有收藏：「我的收藏」区块出现（对照：确实发了 /me/favorites）", async ({
    page,
  }) => {
    const errors = watchPageErrors(page);
    const favoritesRequests = trackRequests(page, "/api/v1/me/favorites");

    // zhangsan 的种子收藏是 tool 1（log-analyzer-a3f2）。
    await login(page, AUTHOR);
    await expect(page.getByTestId("tool-card").first()).toBeVisible();

    const favoritesSection = page.locator(FAVORITES);
    await expect(favoritesSection).toBeVisible();
    await expect(
      favoritesSection.getByRole("heading", { name: "我的收藏" }),
    ).toBeVisible();
    await expect(
      favoritesSection.locator('[data-tool-slug="log-analyzer-a3f2"]'),
    ).toBeVisible();

    expect(
      favoritesRequests.count(),
      "登录用户应当请求了 /me/favorites",
    ).toBeGreaterThanOrEqual(1);
    expect(errors).toEqual([]);
  });

  test("4. 最近访问最多显示 6 个（存储里有 8 条也一样）", async ({ page }) => {
    const errors = watchPageErrors(page);
    await setRecentTools(page, recentFixtures(8));

    await page.goto("/");
    const continueSection = page.locator(CONTINUE);
    await expect(continueSection).toBeVisible();

    // 8 个 slug 都是公开可见的，所以详情报文全部成功 → 正好 6 张卡。
    await expect(continueSection.locator("[data-tool-slug]")).toHaveCount(6);
    await expect(
      continueSection.locator('[data-tool-slug="slow-sql-helper"]'),
    ).toHaveCount(0);
    expect(errors).toEqual([]);
  });

  test("5. 探活标记：unhealthy 的在线工具卡片有标记，ok / 从未检测的没有", async ({
    page,
  }) => {
    const errors = watchPageErrors(page);
    await page.goto("/?type=webapp");
    await expect(page.getByTestId("tool-card").first()).toBeVisible();

    const badge = '[data-testid="webapp-unhealthy-badge"]';

    // fail（§29.3 的派生字段为 true）→ 有标记。
    const failed = page.locator('[data-tool-slug="k8s-inspect-2d47"]');
    await expect(failed).toBeVisible();
    await expect(failed.locator(badge)).toBeVisible();
    await expect(failed.locator(badge)).toHaveText("可能已失效");

    // timeout 同样算不健康（§29.3：`in ('fail','timeout')`）。
    await expect(
      page.locator('[data-tool-slug="trace-collector-webapp"]').locator(badge),
    ).toBeVisible();

    // ok → 无标记。
    await expect(
      page.locator('[data-tool-slug="deploy-assistant"]').locator(badge),
    ).toHaveCount(0);
    // 从未检测过（NULL）→ 同样无标记，否则刚部署就会满屏告警。
    await expect(
      page.locator('[data-tool-slug="api-mock-server-7b1c"]').locator(badge),
    ).toHaveCount(0);

    expect(errors).toEqual([]);
  });

  test("6. 详情页：区分「检测失败」/「运行正常」/「尚未检测」（两种文案的实测取值）", async ({
    page,
  }) => {
    const errors = watchPageErrors(page);

    const readHealth = async (slug: string) => {
      await page.goto(`/tools/${slug}`);
      await expect(page.getByTestId("tool-detail")).toBeVisible();
      return page.evaluate(() => {
        const row = document.querySelector('[data-testid="webapp-health"]');
        const label = document.querySelector('[data-testid="webapp-health-label"]');
        const time = row?.querySelector("time");
        return {
          exists: row !== null,
          status: row?.getAttribute("data-health") ?? null,
          label: label?.textContent?.trim() ?? null,
          text: row?.textContent?.replace(/\s+/g, " ").trim() ?? null,
          checkedAt: time?.getAttribute("datetime") ?? null,
        };
      });
    };

    // fail：文案是「检测失败」，并且带上**最后检查时间**（原始 ISO 由 <time datetime> 承载）。
    const failed = await readHealth("k8s-inspect-2d47");
    expect(failed.status).toBe("fail");
    expect(failed.label).toBe("检测失败");
    expect(failed.checkedAt).toBe("2025-03-16T08:00:00Z");
    expect(failed.text).toContain("最后检查");

    // timeout：文案单独一档，不混进 fail。
    const timedOut = await readHealth("trace-collector-webapp");
    expect(timedOut.status).toBe("timeout");
    expect(timedOut.label).toBe("检测超时");
    expect(timedOut.checkedAt).toBe("2025-03-16T08:00:00Z");

    // ok。
    const healthy = await readHealth("deploy-assistant");
    expect(healthy.status).toBe("ok");
    expect(healthy.label).toBe("运行正常");

    /*
     * ★ 从未检测过（NULL）：**不得**渲染成「失败」。
     * 这条断言是 F2.2 的核心 —— 逐字检查「尚未检测」存在且「失败」不存在。
     */
    const unchecked = await readHealth("api-mock-server-7b1c");
    expect(unchecked.status).toBe("unchecked");
    expect(unchecked.label).toBe("尚未检测");
    expect(unchecked.text).toContain("从未检测");
    expect(unchecked.text).not.toContain("检测失败");
    expect(unchecked.checkedAt).toBeNull();

    // 非 webapp 工具没有这一行（探活只对在线工具有意义）。
    await page.goto("/tools/log-analyzer-a3f2");
    await expect(page.getByTestId("tool-detail")).toBeVisible();
    await expect(page.getByTestId("webapp-health")).toHaveCount(0);

    expect(errors).toEqual([]);
  });

  test("7. 主题三态（light / dark / system）下标记都在，且对比度过 AA", async ({ page }) => {
    const errors = watchPageErrors(page);

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
       * 阻塞内联脚本在首帧前应用。所以先在同源页面写入取值，再**整页导航**
       * （新文档才会重跑那段内联脚本）。
       */
      await page.goto("/");
      await page.evaluate(
        ([mode, recentKey]: readonly [string, string]) => {
          window.localStorage.setItem("localcraft-theme", mode);
          /*
           * 循环里上一轮去过详情页，会写下「最近访问」——那样门户首页会多出一个
           * 「继续使用」区块，同一个 slug 就出现两张卡，选择器会撞上 strict mode。
           * 这里清掉它，让页面回到「默认部署」形态，量的就是卡片墙上的那一个标记。
           */
          window.localStorage.removeItem(recentKey);
        },
        [item.mode, RECENT_TOOLS_KEY] as const,
      );

      await page.goto("/?type=webapp");
      const badge = page.locator(
        '[data-tool-slug="k8s-inspect-2d47"] [data-testid="webapp-unhealthy-badge"]',
      );
      await expect(badge).toBeVisible();

      const badgeContrast = await contrastOf(
        page,
        '[data-tool-slug="k8s-inspect-2d47"] [data-testid="webapp-unhealthy-badge"]',
      );
      expect(badgeContrast, `${item.mode}：标记应可量到颜色`).not.toBeNull();
      expect(badgeContrast?.dark, `${item.mode} 下 <html> 的 dark 类`).toBe(
        item.expectDarkClass,
      );
      // 12px 小字 → AA 要求 4.5:1。
      expect(
        badgeContrast?.contrast ?? 0,
        `${item.mode} 卡片标记对比度（前景 ${badgeContrast?.foreground ?? "?"} / 背景 ${
          badgeContrast?.background ?? "?"
        }，原始值 ${badgeContrast?.color ?? "?"}）`,
      ).toBeGreaterThanOrEqual(4.5);

      // 详情页那一行：文字本身用前景色（远高于 AA），状态圆点是**纯图形** → 3:1。
      await page.goto("/tools/k8s-inspect-2d47");
      await expect(page.getByTestId("webapp-health")).toBeVisible();
      const labelContrast = await contrastOf(page, '[data-testid="webapp-health-label"]');
      expect(
        labelContrast?.contrast ?? 0,
        `${item.mode} 详情状态文字对比度（前景 ${labelContrast?.foreground ?? "?"} / 背景 ${
          labelContrast?.background ?? "?"
        }）`,
      ).toBeGreaterThanOrEqual(4.5);

      /*
       * 圆点量的是「**圆点自身的填充** vs 卡片底色」（`subject: "fill"`），
       * 不是「文字 vs 圆点填充」—— 后者是另一回事（WCAG 1.4.11 管的是图形本身
       * 与相邻颜色的对比度）。fail 是 destructive 填充。
       */
      const dotContrast = await contrastOf(
        page,
        '[data-testid="webapp-health"] > span[aria-hidden="true"]',
        "fill",
      );
      expect(
        dotContrast?.contrast ?? 0,
        `${item.mode} 详情状态圆点（fail）对比度（填充 ${dotContrast?.foreground ?? "?"} / 底色 ${
          dotContrast?.background ?? "?"
        }）`,
      ).toBeGreaterThanOrEqual(3);
      expect(dotContrast?.color).toContain("oklch");

      // 三种状态圆点都要过 3:1：ok → success，unchecked → muted-foreground。
      await page.goto("/tools/deploy-assistant");
      await expect(page.getByTestId("webapp-health")).toHaveAttribute("data-health", "ok");
      const okDot = await contrastOf(
        page,
        '[data-testid="webapp-health"] > span[aria-hidden="true"]',
        "fill",
      );
      expect(
        okDot?.contrast ?? 0,
        `${item.mode} 详情状态圆点（ok）对比度（填充 ${okDot?.foreground ?? "?"} / 底色 ${
          okDot?.background ?? "?"
        }）`,
      ).toBeGreaterThanOrEqual(3);

      await page.goto("/tools/api-mock-server-7b1c");
      await expect(page.getByTestId("webapp-health")).toHaveAttribute(
        "data-health",
        "unchecked",
      );
      const uncheckedDot = await contrastOf(
        page,
        '[data-testid="webapp-health"] > span[aria-hidden="true"]',
        "fill",
      );
      expect(
        uncheckedDot?.contrast ?? 0,
        `${item.mode} 详情状态圆点（未检测）对比度（填充 ${
          uncheckedDot?.foreground ?? "?"
        } / 底色 ${uncheckedDot?.background ?? "?"}）`,
      ).toBeGreaterThanOrEqual(3);
    }

    expect(errors).toEqual([]);
  });

  test("8. 窄屏（375px）：横向区块自己滚动，不产生页级横向滚动", async ({ page }) => {
    const errors = watchPageErrors(page);
    await page.setViewportSize({ width: 375, height: 720 });
    await setRecentTools(page, recentFixtures(6));

    await page.goto("/");
    const continueSection = page.locator(CONTINUE);
    await expect(continueSection).toBeVisible();
    await expect(continueSection.locator("[data-tool-slug]")).toHaveCount(6);

    const metrics = await page.evaluate(() => {
      const list = document.querySelector('[data-testid="portal-continue-using"] ul');
      const root = document.documentElement;
      return {
        pageScrollWidth: root.scrollWidth,
        pageClientWidth: root.clientWidth,
        listScrollWidth: list?.scrollWidth ?? 0,
        listClientWidth: list?.clientWidth ?? 0,
      };
    });

    // 页面本身不横向滚动（+1 容差给亚像素舍入）。
    expect(metrics.pageScrollWidth).toBeLessThanOrEqual(metrics.pageClientWidth + 1);
    // 但列表内部确实可以横向滚动 —— 说明内容没有被压扁，只是**被容器吃掉了**。
    expect(metrics.listScrollWidth).toBeGreaterThan(metrics.listClientWidth);

    expect(errors).toEqual([]);
  });

  test("9. 「继续使用」复用详情页缓存：从详情退回门户，不会为同一条目再发一次请求", async ({
    page,
  }) => {
    const errors = watchPageErrors(page);
    /*
     * `recentTools` 只存 `{slug, name, at}`，卡片要显示封面/分类/计数就得有完整的
     * `ToolListItem`。实现上走**与详情页同一个 queryKey**（`toolDetailQueryKey`）
     * 且 `staleTime` 都是 5 分钟 —— 所以主场景「看完工具退回门户」是零额外请求，
     * 数据直接在 react-query 缓存里。这条用例把这个说法钉住，别让它悄悄退化。
     */
    const detailRequests: string[] = [];
    page.on("request", (request) => {
      if (new URL(request.url()).pathname === "/api/v1/tools/log-analyzer-a3f2") {
        detailRequests.push(request.url());
      }
    });

    await page.goto("/tools/log-analyzer-a3f2");
    await expect(page.getByTestId("tool-detail")).toBeVisible();
    /*
     * 详情页自己会请求 1~2 次（dev 下 React 18 StrictMode 的挂载-卸载-再挂载会让
     * 首个在途请求被取消后重发）。所以这里只记**基线**，不写死 1 —— 要验的是
     * 「回到门户之后不再增加」，那才是缓存复用的定义。
     */
    const afterDetail = detailRequests.length;
    expect(afterDetail, "详情页至少要请求一次").toBeGreaterThanOrEqual(1);

    // 面包屑「首页」是 SPA 内导航（不整页重载），缓存因此在同一个 QueryClient 里。
    await page.getByTestId("tool-breadcrumb").getByRole("link", { name: "首页" }).click();

    const continueSection = page.locator(CONTINUE);
    await expect(continueSection).toBeVisible();
    await expect(
      continueSection.locator('[data-tool-slug="log-analyzer-a3f2"]'),
    ).toBeVisible();
    expect(
      detailRequests.length,
      "「继续使用」应命中详情页留下的缓存，不应重复请求",
    ).toBe(afterDetail);

    expect(errors).toEqual([]);
  });
});
