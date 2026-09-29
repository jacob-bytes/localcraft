import { expect, test, type Page } from "@playwright/test";

import {
  ADMIN,
  AUTHOR,
  MOCK_TOOL_IDS,
  RECENT_TOOLS_KEY,
  contrastOf,
  login,
  setMockFavorites,
  setRecentTools,
  trackRequests,
  watchPageErrors,
  type RecentToolFixture,
} from "./helpers";

/**
 * M14 验收 · 门户首页两个区块 + 在线工具探活状态（CONTRACT §29.3 / §29.8）。
 * **M16 起**：两个区块从「公告下方的横向堆叠」改成「门户顶部的 tab」
 * （§32.2 取代 §29.8 的堆叠布局），本文件因此多了「先点 tab 再断言」的前置操作。
 *
 * 这一轮不做新功能，做的是**把已经存在的数据放到页面上**：
 *  - 「最近访问」早就存在 `localStorage`（M7 · F6），但只喂给 ⌘K 面板；
 *  - 「我的收藏」早就有接口（M8 §23.4），但只有专门页面。
 *
 * 因此本文件的重心全部在几个「不能做错」的地方：
 *
 *  1. **默认部署零变化**：没有收藏、也没访问过时，**连整条 tab 栏都不渲染**
 *     （不是「渲染了但只有一个 tab」，也不是「渲染了但隐藏」）。这条用 DOM
 *     结构证据钉住，见用例 1 —— M16 起它同时是 §32.2 那条「★ 只有一个 tab 时
 *     隐藏整条 tab 栏」的证据。
 *  2. **匿名不发 `/me/favorites`**：不白打一个必然 401 的请求。用请求监听钉住，
 *     见用例 2（匿名 = 0 次）与用例 3（登录 ≥ 1 次）这组对照。
 *  3. **工具栏与分页只在「全部工具」下存在**（§32.2），见用例 10。
 *  4. **`?tab=` 进 URL**：可分享、F5 保持、可直接访问，见用例 11。
 *
 * 探活部分（§29.3）另有一条容易做错的地方：`null`（从未检测过）**不是** `fail`，
 * 文案必须能区分，见用例 6。
 */

/* 8 个公开的边界工具（`BOUNDARY_TOOL_SEEDS`），全部匿名可见 —— 用来构造
   「最近访问」的确定性数据，也用来验「显示全部本地记录（上限 8，不截断）」。 */
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

/*
 * M16：两个区块变成了 tab 面板 —— 面板自己不再有标题（`<h2>` 没了，tab 标签就是
 * 标题），锚点因此落在**面板自己的网格**上。它们各自有独立 testid：
 * `[data-testid="tool-grid"]` 的语义被保留给「门户卡片墙」（`helpers.openTool`、
 * m8/m9 的定位都靠它把卡片墙那张卡与别处的同 slug 卡片区分开）。
 */
const TABLIST = '[data-testid="portal-tabs"]';
const RECENT_GRID = '[data-testid="portal-recent-grid"]';
const FAVORITES_GRID = '[data-testid="portal-favorites-grid"]';
const SEE_ALL = '[data-testid="portal-favorites-see-all"]';
const CARD_WALL = '[data-testid="tool-grid"]';

/** tab 面板：`role="tab"` 的按钮（Radix），按可见文案定位。 */
function tab(page: Page, name: RegExp) {
  return page.getByRole("tab", { name });
}

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
  test("1. 默认部署（无最近访问、无收藏）：连 tab 栏都不渲染（DOM 结构证据）", async ({
    page,
  }) => {
    const errors = watchPageErrors(page);
    const favoritesRequests = trackRequests(page, "/api/v1/me/favorites");

    await page.goto("/");
    await expect(page.getByTestId("tool-card").first()).toBeVisible();

    /*
     * ① ★ M16 最强的一条（§32.2「只有一个 tab 时隐藏整条 tab 栏」）：
     *    整条 tab 栏**不存在** —— 不是「渲染了但只有一个 tab」，也不是「隐藏」。
     *    `role="tab"`/`role="tabpanel"`/`data-slot="tabs"` 一并钉住：单 tab 时连
     *    Radix 的 Tabs 外壳都不渲染（否则会留下一个指向不存在触发器的空 tabpanel）。
     */
    await expect(page.locator(TABLIST)).toHaveCount(0);
    await expect(page.getByRole("tablist")).toHaveCount(0);
    await expect(page.getByRole("tab")).toHaveCount(0);
    await expect(page.getByRole("tabpanel")).toHaveCount(0);
    await expect(page.locator('[data-slot="tabs"]')).toHaveCount(0);

    // ② 两个面板的网格也都不存在（不是「存在但为空 / 隐藏」）。
    await expect(page.locator(RECENT_GRID)).toHaveCount(0);
    await expect(page.locator(FAVORITES_GRID)).toHaveCount(0);

    // ③ 连标题文字都不存在 —— 「不放空标题」。
    await expect(page.getByRole("heading", { name: "继续使用" })).toHaveCount(0);
    await expect(page.getByRole("heading", { name: "我的收藏" })).toHaveCount(0);

    /*
     * ④ 门户根容器的**直接子元素正好是 3 个**，且顺序就是 hero → 公告 → 内容网格，
     *    第 3 个就是改动前那一层 `lg:grid`。任何「空的外壳容器」都会让这个数字变成
     *    4、或把第 3 个换成 Tabs 外壳 —— 那正是任务书禁止的「默认部署页面不一致」。
     *    这条断言不依赖我写了什么条件判断，它读的是真实 DOM。
     */
    const children = await portalRootChildren(page);
    expect(children, "门户根容器应可定位").not.toBeNull();
    expect(children ?? []).toHaveLength(3);
    expect(children?.[0]?.label).toBe("portal-hero-title");
    expect(children?.[1]?.label).toBe("公告");
    expect(children?.[2]?.tag).toBe("div");
    expect(children?.[2]?.className).toContain("lg:grid");

    // ⑤ 卡片墙本身还是那一层，而且 `tool-grid` 仍然唯一（面板用的是自己的 testid）。
    await expect(page.locator(CARD_WALL)).toHaveCount(1);

    /*
     * ⑥ URL 里带一个**当前不可用**的 tab（匿名 + 无收藏 + 无最近访问）也保持默认部署
     *    形态：仍然连 tab 栏都没有，而且不会因为这个参数去打 /me/favorites。
     */
    await page.goto("/?tab=favorites");
    await expect(page.locator(CARD_WALL)).toBeVisible();
    await expect(page.getByRole("tablist")).toHaveCount(0);
    await expect(page.getByRole("tabpanel")).toHaveCount(0);
    expect(favoritesRequests.count(), "`?tab=favorites` 也不得让匿名发请求").toBe(0);

    // ⑦ 匿名不请求收藏（不白打 401）。
    expect(favoritesRequests.count()).toBe(0);
    expect(errors).toEqual([]);
  });

  test("2. 匿名：「继续使用」是本地数据所以出现在 tab 栏；「我的收藏」不渲染且**不发请求**", async ({
    page,
  }) => {
    const errors = watchPageErrors(page);
    const favoritesRequests = trackRequests(page, "/api/v1/me/favorites");
    await setRecentTools(page, recentFixtures(2));

    await page.goto("/");
    await expect(page.getByTestId("tool-card").first()).toBeVisible();

    /*
     * ① tab 栏出现，且**只有两个 tab**（匿名没有收藏 → 「我的收藏」不出现）。
     *    `?tab=` 缺省 = 「全部工具」且它默认选中。
     */
    await expect(page.locator(TABLIST)).toBeVisible();
    await expect(page.getByRole("tab")).toHaveCount(2);
    await expect(tab(page, /全部工具/)).toHaveAttribute("aria-selected", "true");
    await expect(tab(page, /我的收藏/)).toHaveCount(0);

    // ② 默认 tab 下「继续使用」面板**没挂载**（Radix 只挂载激活的 TabsContent）。
    await expect(page.locator(RECENT_GRID)).toHaveCount(0);

    // ③ 点「继续使用」→ 面板出现，2 张卡，且 URL 记下了这个 tab（§32.2）。
    await tab(page, /继续使用/).click();
    await expect(page).toHaveURL(/[?&]tab=recent/);

    const panel = page.locator(RECENT_GRID);
    await expect(panel).toBeVisible();
    await expect(panel.locator("[data-tool-slug]")).toHaveCount(2);
    await expect(panel.locator('[data-tool-slug="log-analyzer-a3f2"]')).toBeVisible();
    await expect(panel.locator('[data-tool-slug="deploy-assistant"]')).toBeVisible();

    // ④ 匿名看不到「我的收藏」：既不渲染区块，也不发那个请求（对照用例 3）。
    await expect(page.locator(FAVORITES_GRID)).toHaveCount(0);
    await expect(page.getByRole("link", { name: "我的收藏" })).toHaveCount(0);
    expect(favoritesRequests.count(), "匿名不得请求 /me/favorites").toBe(0);
    expect(errors).toEqual([]);
  });

  test("3. 登录且有收藏：「我的收藏」tab 出现（对照：确实发了 /me/favorites）", async ({
    page,
  }) => {
    const errors = watchPageErrors(page);
    const favoritesRequests = trackRequests(page, "/api/v1/me/favorites");

    // zhangsan 的种子收藏是 tool 1（log-analyzer-a3f2）。
    await login(page, AUTHOR);
    await expect(page.getByTestId("tool-card").first()).toBeVisible();

    // 全新上下文里没有最近访问 → 只有「全部工具」+「我的收藏」两个 tab。
    await expect(page.locator(TABLIST)).toBeVisible();
    await expect(page.getByRole("tab")).toHaveCount(2);
    await expect(tab(page, /继续使用/)).toHaveCount(0);

    await tab(page, /我的收藏/).click();
    const panel = page.locator(FAVORITES_GRID);
    await expect(panel).toBeVisible();
    await expect(panel.locator('[data-tool-slug="log-analyzer-a3f2"]')).toBeVisible();

    expect(
      favoritesRequests.count(),
      "登录用户应当请求了 /me/favorites",
    ).toBeGreaterThanOrEqual(1);
    expect(errors).toEqual([]);
  });

  test("4. 「继续使用」显示**全部**本地记录（8 条也不截断，§32.2 取代 §29.8 的「最多 6 个」）", async ({
    page,
  }) => {
    const errors = watchPageErrors(page);
    await setRecentTools(page, recentFixtures(8));

    await page.goto("/");
    await expect(page.getByTestId("tool-card").first()).toBeVisible();

    // tab 标签上的计数就是**本地记录条数**（8），不是渲染出来的卡片数。
    const recentTab = tab(page, /继续使用/);
    await expect(recentTab).toBeVisible();
    await expect(recentTab).toContainText("(8)");

    await recentTab.click();
    const panel = page.locator(RECENT_GRID);
    await expect(panel).toBeVisible();

    // 8 个 slug 都是公开可见的，所以详情报文全部成功 → 正好 8 张卡（M14 是 6）。
    await expect(panel.locator("[data-tool-slug]")).toHaveCount(8);
    await expect(panel.locator('[data-tool-slug="slow-sql-helper"]')).toBeVisible();
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
           * 「继续使用」tab（M16 起是 tab，M14 时是区块）。这里清掉它，让页面回到
           * 「默认部署」形态（**连 tab 栏都没有**），量的就是卡片墙上那一个标记。
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

  test("8. 窄屏（375px）：tab 栏、面板与卡片都不产生页级横向滚动", async ({ page }) => {
    const errors = watchPageErrors(page);
    await page.setViewportSize({ width: 375, height: 720 });
    await setRecentTools(page, recentFixtures(6));

    await page.goto("/");
    await expect(page.locator(TABLIST)).toBeVisible();
    await expect(tab(page, /继续使用/)).toBeVisible();

    /*
     * M16：面板不再是「自己横滚的横向区块」（§32.2 取代了 §29.8 的堆叠布局），
     * 而是一个正常的自适应网格 —— 所以原来那条「`<ul>` 内部可以横向滚动」的断言
     * **随结构一起作废**，换成更强的两条：页面不横滚，**而且每一张卡都完全落在
     * 视口内**（内容没有被压扁、也没有溢出到屏幕外）。
     */
    await tab(page, /继续使用/).click();
    const panel = page.locator(RECENT_GRID);
    await expect(panel.locator("[data-tool-slug]")).toHaveCount(6);

    const metrics = await page.evaluate(() => {
      const root = document.documentElement;
      const list = document.querySelector('[data-testid="portal-tabs"]');
      const cards = Array.from(
        document.querySelectorAll('[data-testid="portal-recent-grid"] [data-testid="tool-card"]'),
      );
      return {
        pageScrollWidth: root.scrollWidth,
        pageClientWidth: root.clientWidth,
        tablistRight: list ? Math.round(list.getBoundingClientRect().right) : -1,
        tablistScrollWidth: list?.scrollWidth ?? 0,
        tablistClientWidth: list?.clientWidth ?? 0,
        cardCount: cards.length,
        cardRight: cards.length
          ? Math.round(Math.max(...cards.map((card) => card.getBoundingClientRect().right)))
          : -1,
      };
    });

    // 页面本身不横向滚动（+1 容差给亚像素舍入）。
    expect(metrics.pageScrollWidth).toBeLessThanOrEqual(metrics.pageClientWidth + 1);
    // 6 张卡都渲染出来了，且最右边那张的右边缘仍在视口内。
    expect(metrics.cardCount).toBe(6);
    expect(metrics.cardRight).toBeLessThanOrEqual(metrics.pageClientWidth);
    // tab 栏自己也没撑破页面（3 个 tab 的极端情况另见用例 10 的窄屏断言）。
    expect(metrics.tablistRight).toBeLessThanOrEqual(metrics.pageClientWidth);
    expect(metrics.tablistScrollWidth).toBeLessThanOrEqual(metrics.tablistClientWidth + 1);

    expect(errors).toEqual([]);
  });

  test("9. 「继续使用」复用详情页缓存：从详情退回门户再切到该 tab，不会为同一条目再发一次请求", async ({
    page,
  }) => {
    const errors = watchPageErrors(page);
    /*
     * `recentTools` 只存 `{slug, name, at}`，卡片要显示封面/分类/计数就得有完整的
     * `ToolListItem`。实现上走**与详情页同一个 queryKey**（`toolDetailQueryKey`）
     * 且 `staleTime` 都是 5 分钟 —— 所以主场景「看完工具退回门户、再点『继续使用』」
     * 是零额外请求，数据直接在 react-query 缓存里。这条用例把这个说法钉住。
     *
     * M16 起这个「零请求」还多了一层保证：面板只在 tab 激活时挂载（Radix 默认
     * 不挂载非激活的 TabsContent），所以在「全部工具」下**一次详情请求都不发**。
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

    // 默认 tab 是「全部工具」：此时面板还没挂载，不应有任何详情请求。
    await expect(page.locator(TABLIST)).toBeVisible();
    await expect(page.locator(RECENT_GRID)).toHaveCount(0);
    expect(
      detailRequests.length,
      "默认 tab 下不应挂载「继续使用」面板，也就不该补详情请求",
    ).toBe(afterDetail);

    await tab(page, /继续使用/).click();
    const panel = page.locator(RECENT_GRID);
    await expect(panel.locator('[data-tool-slug="log-analyzer-a3f2"]')).toBeVisible();
    expect(
      detailRequests.length,
      "「继续使用」应命中详情页留下的缓存，不应重复请求",
    ).toBe(afterDetail);

    expect(errors).toEqual([]);
  });

  test("10. 工具栏与分页只在「全部工具」下存在：切走消失、切回恢复（DOM 证据）", async ({
    page,
  }) => {
    const errors = watchPageErrors(page);
    await setRecentTools(page, recentFixtures(2));
    // `page_size=12` 让 27 个工具必然有多页 → 分页控件一定渲染（默认 24 时也可能只有 2 页，不写死）。
    await page.goto("/?page_size=12");
    await expect(page.getByTestId("tool-card").first()).toBeVisible();

    const toolbar = () => page.getByLabel("排序方式");
    const viewToggle = () => page.getByLabel("视图切换");
    const pageSize = () => page.getByLabel("每页条数");
    const total = () => page.getByText(/^共 \d+ 个工具$/);
    const pagination = () => page.locator('nav[aria-label="分页"]');

    // ① 「全部工具」下四件套 + 分页都在。
    await expect(toolbar()).toBeVisible();
    await expect(viewToggle()).toBeVisible();
    await expect(pageSize()).toBeVisible();
    await expect(total()).toBeVisible();
    await expect(pagination()).toBeVisible();

    // ② 切到「继续使用」→ 全部消失（不是隐藏：`hidden` 的空 tabpanel 里没有它们）。
    await tab(page, /继续使用/).click();
    await expect(page.locator(RECENT_GRID)).toBeVisible();
    await expect(toolbar()).toHaveCount(0);
    await expect(viewToggle()).toHaveCount(0);
    await expect(pageSize()).toHaveCount(0);
    await expect(total()).toHaveCount(0);
    await expect(pagination()).toHaveCount(0);
    await expect(page.locator(CARD_WALL)).toHaveCount(0);

    // ③ 切回「全部工具」→ 全部恢复。
    await tab(page, /全部工具/).click();
    await expect(toolbar()).toBeVisible();
    await expect(viewToggle()).toBeVisible();
    await expect(pageSize()).toBeVisible();
    await expect(total()).toBeVisible();
    await expect(pagination()).toBeVisible();
    await expect(page.locator(CARD_WALL)).toHaveCount(1);

    expect(errors).toEqual([]);
  });

  test("11. `?tab=` 进 URL：点 tab 写入、刷新保持、可直接访问、非法值回落默认", async ({
    page,
  }) => {
    const errors = watchPageErrors(page);
    await setRecentTools(page, recentFixtures(3));

    await page.goto("/");
    await expect(page.locator(TABLIST)).toBeVisible();

    // ① 默认 tab = 全部工具，且**不占 URL 参数**（默认值不进 URL）。
    expect(new URL(page.url()).searchParams.get("tab")).toBeNull();
    await expect(tab(page, /全部工具/)).toHaveAttribute("aria-selected", "true");

    // ② 点「继续使用」→ 参数进 URL。
    await tab(page, /继续使用/).click();
    await expect(page).toHaveURL(/[?&]tab=recent/);
    await expect(page.locator(RECENT_GRID).locator("[data-tool-slug]")).toHaveCount(3);

    // ③ F5 保持。
    await page.reload();
    await expect(page.locator(RECENT_GRID).locator("[data-tool-slug]")).toHaveCount(3);
    await expect(tab(page, /继续使用/)).toHaveAttribute("aria-selected", "true");

    // ④ 可直接访问（新文档、直接带参数进来，不经过点击）。
    await page.goto("/?tab=recent");
    await expect(page.locator(RECENT_GRID).locator("[data-tool-slug]")).toHaveCount(3);

    // ⑤ 切回「全部工具」→ 参数被清掉。
    await tab(page, /全部工具/).click();
    await expect(page.locator(CARD_WALL)).toBeVisible();
    expect(new URL(page.url()).searchParams.get("tab")).toBeNull();

    // ⑥ `?tab=all` 是「默认值」的合法写法，照样接受。
    await page.goto("/?tab=all");
    await expect(page.locator(CARD_WALL)).toBeVisible();
    await expect(tab(page, /全部工具/)).toHaveAttribute("aria-selected", "true");

    // ⑦ 非法值 → 回落默认（不白屏、不渲染空面板）。
    await page.goto("/?tab=nonsense");
    await expect(page.locator(CARD_WALL)).toBeVisible();
    await expect(tab(page, /全部工具/)).toHaveAttribute("aria-selected", "true");

    // ⑧ 匿名访问 `?tab=favorites`：这个 tab 根本不存在（匿名没有收藏）→ 回落
    //    「全部工具」，不渲染空面板，也不为这个参数发一次 /me/favorites。
    const favoritesRequests = trackRequests(page, "/api/v1/me/favorites");
    await page.goto("/?tab=favorites");
    await expect(page.locator(CARD_WALL)).toBeVisible();
    await expect(tab(page, /我的收藏/)).toHaveCount(0);
    await expect(tab(page, /全部工具/)).toHaveAttribute("aria-selected", "true");
    expect(favoritesRequests.count(), "匿名不得请求 /me/favorites").toBe(0);

    expect(errors).toEqual([]);
  });

  test("12. 「我的收藏」tab：只给第一页 24 条 + 「查看全部 N 个 →」，tab 内不分页", async ({
    page,
  }) => {
    const errors = watchPageErrors(page);
    /*
     * 种子用户里收藏最多的也只有 1 条，凑不出 `total > 24`。这里把 admin（id 1）的
     * 收藏夹种成**全部 26 个工具**（走 mock 自己的 `localcraft.msw.engagement` 键，
     * 见 helpers 里的说明）—— 这样 `total` 必然 > 24，才验得到「第一页 + 查看全部」。
     */
    await setMockFavorites(page, 1, MOCK_TOOL_IDS);
    await login(page, ADMIN);
    await expect(page.getByTestId("tool-card").first()).toBeVisible();

    await tab(page, /我的收藏/).click();
    const panel = page.locator(FAVORITES_GRID);
    await expect(panel).toBeVisible();

    // ① 只渲染第一页的 24 张（`DEFAULT_PAGE_SIZE`），而不是全部。
    await expect(panel.locator("[data-tool-slug]")).toHaveCount(24);

    // ② `total > 24` → 一条「查看全部 N 个 →」，指向既有的 `/me/favorites`。
    const seeAll = page.locator(SEE_ALL);
    await expect(seeAll).toBeVisible();
    const text = (await seeAll.innerText()).replace(/\s+/g, " ").trim();
    const parsed = /^查看全部 (\d+) 个/.exec(text);
    expect(parsed, `链接文案应形如「查看全部 N 个 →」，实测「${text}」`).not.toBeNull();
    const total = Number(parsed?.[1]);
    expect(total, `total 应大于 24，实测 ${total}`).toBeGreaterThan(24);
    await expect(seeAll).toHaveAttribute("href", "/me/favorites");

    // ③ tab 标签上的计数与 `total` 一致。
    await expect(tab(page, /我的收藏/)).toContainText(`(${total})`);

    // ④ 不在 tab 内再造一套分页 / 工具栏。
    await expect(page.locator('nav[aria-label="分页"]')).toHaveCount(0);
    await expect(page.getByLabel("排序方式")).toHaveCount(0);

    // ⑤ 链接真的落到既有页面（SPA 内导航，不是死链）。
    await seeAll.click();
    await expect(page).toHaveURL(/\/me\/favorites$/);
    await expect(page.getByTestId("my-favorites-page")).toBeVisible();

    expect(errors).toEqual([]);
  });

  test("13. tab 栏复用 `ui/tabs`：role/aria/aria-controls 齐全、方向键可切换、三态主题下都过 AA", async ({
    page,
  }) => {
    const errors = watchPageErrors(page);
    await setRecentTools(page, recentFixtures(2));

    await page.goto("/");
    await expect(page.locator(TABLIST)).toBeVisible();

    /*
     * ① 这一条是「**复用 `components/ui/tabs`，不手写**」（§32.2 冻结）的行为证据：
     *    tablist 有可访问名、每个 tab 有 `aria-selected` 且 `aria-controls` 指向一个
     *    **真实存在**的 `role="tabpanel"`。手写一排按钮最容易漏掉的就是这些。
     */
    await expect(page.getByRole("tablist")).toHaveAttribute("aria-label", "门户视图");
    const wiring = await page.evaluate(() =>
      Array.from(document.querySelectorAll('[role="tab"]')).map((item) => {
        const controls = item.getAttribute("aria-controls");
        const panel = controls ? document.getElementById(controls) : null;
        return {
          text: (item as HTMLElement).innerText.replace(/\s+/g, " ").trim(),
          selected: item.getAttribute("aria-selected"),
          controls,
          panelRole: panel?.getAttribute("role") ?? null,
        };
      }),
    );
    expect(wiring).toHaveLength(2);
    for (const item of wiring) {
      expect(item.controls, `${item.text} 应有 aria-controls`).toBeTruthy();
      expect(item.panelRole, `${item.text} 的 aria-controls 应指向 role=tabpanel`).toBe(
        "tabpanel",
      );
    }
    expect(wiring[0]?.selected).toBe("true");
    expect(wiring[1]?.selected).toBe("false");

    /*
     * ② 方向键切换（Radix 自带）。这一条**不能**靠「我 import 了 ui/tabs」证明 ——
     *    它必须在真实键盘事件下把焦点与激活态一起搬过去。
     */
    await tab(page, /全部工具/).focus();
    await page.keyboard.press("ArrowRight");
    await expect(tab(page, /继续使用/)).toHaveAttribute("aria-selected", "true");
    await expect(tab(page, /全部工具/)).toHaveAttribute("aria-selected", "false");
    await expect(page.locator(RECENT_GRID)).toBeVisible();
    await expect(page).toHaveURL(/[?&]tab=recent/);

    /*
     * ③ 三态主题（light / dark / system）下，**选中**与**未选中**两种 tab 的文字
     *    对比度都要过 AA（14px 正文 → 4.5:1；计数是 12px 小字，同样 4.5:1）。
     *    未选中的那一档是 `text-muted-foreground` on `bg-muted` —— 主题令牌里最容易
     *    差一点点的地方，所以逐个量真实像素。
     */
    const cases = [
      { mode: "light", scheme: "light", expectDarkClass: false },
      { mode: "dark", scheme: "light", expectDarkClass: true },
      { mode: "system", scheme: "dark", expectDarkClass: true },
      { mode: "system", scheme: "light", expectDarkClass: false },
    ] as const;

    for (const item of cases) {
      await page.emulateMedia({ colorScheme: item.scheme });
      /*
       * 与用例 7 同一套做法：主题落在 `localStorage` + `<html class="dark">`，由
       * index.html 的阻塞内联脚本在首帧前应用 —— 所以先写取值，再**整页导航**让新
       * 文档重跑那段脚本。`setRecentTools` 走的是 `addInitScript`，每次文档加载都会
       * 重新写入，所以切主题不会把 tab 栏弄丢。
       */
      await page.goto("/");
      await page.evaluate((mode: string) => {
        window.localStorage.setItem("localcraft-theme", mode);
      }, item.mode);

      await page.goto("/?tab=recent");
      await expect(page.locator(TABLIST)).toBeVisible();
      await expect(tab(page, /继续使用/)).toHaveAttribute("aria-selected", "true");

      const selected = await contrastOf(page, `${TABLIST} [role="tab"][aria-selected="true"]`);
      const unselected = await contrastOf(
        page,
        `${TABLIST} [role="tab"][aria-selected="false"]`,
      );
      // 计数是独立的小字元素（12px），单独量一次。
      const count = await contrastOf(page, `${TABLIST} [role="tab"] span`);

      expect(selected?.dark, `${item.mode} 下 <html> 的 dark 类`).toBe(item.expectDarkClass);
      expect(
        selected?.contrast ?? 0,
        `${item.mode} 选中的 tab 对比度（前景 ${selected?.foreground ?? "?"} / 背景 ${
          selected?.background ?? "?"
        }）`,
      ).toBeGreaterThanOrEqual(4.5);
      expect(
        unselected?.contrast ?? 0,
        `${item.mode} 未选中的 tab 对比度（前景 ${unselected?.foreground ?? "?"} / 背景 ${
          unselected?.background ?? "?"
        }）`,
      ).toBeGreaterThanOrEqual(4.5);
      expect(
        count?.contrast ?? 0,
        `${item.mode} tab 计数小字对比度（前景 ${count?.foreground ?? "?"} / 背景 ${
          count?.background ?? "?"
        }）`,
      ).toBeGreaterThanOrEqual(4.5);
    }

    expect(errors).toEqual([]);
  });

  test("14. 登录但收藏夹为空：不会出现「我的收藏 (0)」——仍然只有一个 tab，连 tab 栏都不渲染", async ({
    page,
  }) => {
    const errors = watchPageErrors(page);
    const favoritesRequests = trackRequests(page, "/api/v1/me/favorites");

    // 种子里 admin（id 1）的收藏夹是**空的** —— 用它验「有权限但没内容」。
    const favoritesResponse = page.waitForResponse((response) =>
      response.url().includes("/me/favorites"),
    );
    await login(page, ADMIN);
    await favoritesResponse;
    await expect(page.getByTestId("tool-card").first()).toBeVisible();

    /*
     * 这条断言不是空转：先钉住「即使要渲染 tab 栏，前提也齐了」——
     *  · 左栏出现「我的收藏」入口 → 会话与角色判定已就绪（与收藏查询**同一个门**）；
     *  · `共 N 个工具` 已渲染 → 「全部工具」tab 的计数与技术前提已就绪。
     * 在这个前提下 tab 栏仍然不存在，唯一的解释就是「我的收藏」这个 tab 因为
     * **没有内容**而没有出现（§32.2「只显示有内容的 tab」），而不是「还没算出来」。
     */
    await expect(page.getByRole("link", { name: "我的收藏" })).toBeVisible();
    await expect(page.getByText(/^共 \d+ 个工具$/)).toBeVisible();
    await expect(page.getByRole("tablist")).toHaveCount(0);
    await expect(page.getByRole("tabpanel")).toHaveCount(0);
    await expect(page.locator(TABLIST)).toHaveCount(0);
    await expect(page.locator(FAVORITES_GRID)).toHaveCount(0);
    await expect(page.locator(CARD_WALL)).toHaveCount(1);

    // 对照：请求**确实发了**（不是「没请求所以没 tab」）。
    expect(favoritesRequests.count()).toBeGreaterThanOrEqual(1);

    expect(errors).toEqual([]);
  });
});
