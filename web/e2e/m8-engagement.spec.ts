import { mkdtemp, writeFile } from "node:fs/promises";
import { tmpdir } from "node:os";
import { join } from "node:path";

import { expect, test, type Page } from "@playwright/test";

import { ADMIN, APPROVER, ARTIFACTS, AUTHOR, login, openTool, watchPageErrors } from "./helpers";

/**
 * M8 验收 · 收藏 / 点赞 / 上传去重 / 效率估算（任务书 F7~F12）。
 *
 * ## 为什么每个写用例开头都要 `resetMock`
 *
 * 收藏与点赞是**有状态**的：mock 把它们（以及 `estimated_saving_minutes`、注入的
 * 故障）存在 `localStorage` 里，这样「刷新后状态保持」（F12.3）才可能成立 —— 一 F5
 * 就没了的纯内存态根本测不出那条。有状态就有污染风险，所以 mock 提供
 * `POST /api/v1/__mock__/reset` 显式恢复确定性种子（F12.4）。Playwright 每个用例
 * 本来就有独立 context（localStorage 也是新的），两条防线叠加。
 */

/** 显式把 mock 的互动状态恢复成种子（需要页面已加载，MSW 才拦得到）。 */
async function resetMock(page: Page): Promise<void> {
  await page.evaluate(async () => {
    await fetch("/api/v1/__mock__/reset", { method: "POST" });
  });
}

/** 让某个端点失败 N 次（仅 mock 的控制面）。`delayMs` 让乐观更新「看得见」。 */
async function failNext(page: Page, endpoint: string, delayMs = 0): Promise<void> {
  await page.evaluate(
    async ([url, ms]) => {
      await fetch("/api/v1/__mock__/fail-next", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ endpoint: url, times: 1, delay_ms: ms }),
      });
    },
    [endpoint, delayMs] as const,
  );
}

/**
 * 读 mock 侧真正落地的 `estimated_saving_minutes`。
 *
 * UI 上看不出 `null` 与 `0` 的差别（输入框都是空的），而契约 §23.3 明确
 * 「`NULL` 不是一个可以当成 0 的值」，所以这条断言必须读落地值。
 */
async function savingMinutesOf(page: Page, toolId: number): Promise<number | null> {
  return page.evaluate(async (id) => {
    const response = await fetch(`/api/v1/__mock__/tools/${id}/saving-minutes`);
    const body = (await response.json()) as { estimated_saving_minutes: number | null };
    return body.estimated_saving_minutes;
  }, toolId);
}

async function makeTempFile(name: string, content: string): Promise<string> {
  const dir = await mkdtemp(join(tmpdir(), "localcraft-m8-"));
  const filePath = join(dir, name);
  await writeFile(filePath, content);
  return filePath;
}

/** 工具卡片（整卡是拉伸链接，`data-tool-slug` 在卡片的包裹元素上）。 */
function cardFor(page: Page, slug: string) {
  return page.locator(`[data-tool-slug="${slug}"]`);
}

/**
 * 点击切换按钮并**等这次请求真的回来**。
 *
 * 为什么需要它：乐观更新在几毫秒内就改了 DOM，而 mock 的 handler 还要几十毫秒才
 * 落库 / 落 localStorage。断言「刷新后保持」如果不先等服务端，就会在第一版那样
 * 变成竞态 —— 用例偶发失败，看起来像状态没持久化。
 */
async function clickAndSettle(
  page: Page,
  button: ReturnType<typeof cardFor>,
  urlPart: string,
  method: "PUT" | "DELETE",
): Promise<void> {
  const settled = page.waitForResponse(
    (response) => response.url().includes(urlPart) && response.request().method() === method,
    { timeout: 15_000 },
  );
  await button.click();
  const response = await settled;
  expect(response.status(), `${method} ${urlPart} 应当成功`).toBe(200);
}

test.describe("M8 · 收藏 / 点赞 / 去重 / 数字概览", () => {
  /* ------------------------------------------------------------------ F7/F8 */
  test("F7/F8 · 匿名：卡片只显示计数、不给收藏按钮；详情页也没有切换入口", async ({ page }) => {
    const errors = watchPageErrors(page);
    await page.goto("/");

    const card = cardFor(page, "log-analyzer-a3f2");
    await expect(card).toBeVisible();
    // 计数照常返回（契约 §23.5）：tool 1 基线 4 + 种子里 zhangsan 的 1 = 5
    await expect(card.getByTestId("favorite-count-value")).toHaveText("5");
    await expect(card.getByTestId("like-count-value")).toHaveText("8");
    // 未登录不显示按钮（与「登录后可下载」同一套匿名策略）
    await expect(card.getByTestId("card-favorite-button")).toHaveCount(0);

    await openTool(page, "log-analyzer-a3f2");
    await expect(page.getByTestId("favorite-button")).toHaveCount(0);
    await expect(page.getByTestId("like-button")).toHaveCount(0);
    // 信息栏仍然给出两个计数（匿名也能看互动数据）
    const sidebar = page.getByTestId("tool-detail");
    await expect(sidebar.locator("dt", { hasText: /^收藏$/ })).toBeVisible();
    await expect(sidebar.locator("dt", { hasText: /^点赞$/ })).toBeVisible();

    await page.screenshot({ path: `${ARTIFACTS}/m8-anonymous-counts.png`, fullPage: true });
    expect(errors).toEqual([]);
  });

  test("F7 · 卡片收藏：乐观 +1 → 刷新后保持 → 取消 -1，且不触发跳转", async ({ page }) => {
    const errors = watchPageErrors(page);
    await login(page, AUTHOR);
    await resetMock(page);
    await page.goto("/");

    const card = cardFor(page, "deploy-assistant");
    const button = card.getByTestId("card-favorite-button");
    const count = card.getByTestId("favorite-count-value");

    await expect(button).toHaveAttribute("aria-pressed", "false");
    await expect(count).toHaveText("3");

    await clickAndSettle(page, button, "/favorite", "PUT");
    // 乐观更新：按钮与计数一起变
    await expect(button).toHaveAttribute("aria-pressed", "true");
    await expect(count).toHaveText("4");
    await expect(button).toHaveAccessibleName(/取消收藏/);
    // 点星标不该导航（拉伸链接是兄弟节点，不是父节点）
    await expect(page).toHaveURL(/\/$/);

    // 刷新后保持：mock 把收藏状态持久化在 localStorage（见 handlers 的注释）
    await page.reload();
    const afterReload = cardFor(page, "deploy-assistant");
    await expect(afterReload.getByTestId("card-favorite-button")).toHaveAttribute(
      "aria-pressed",
      "true",
    );
    await expect(afterReload.getByTestId("favorite-count-value")).toHaveText("4");

    // 取消收藏 → 计数回落
    await clickAndSettle(
      page,
      afterReload.getByTestId("card-favorite-button"),
      "/favorite",
      "DELETE",
    );
    await expect(afterReload.getByTestId("card-favorite-button")).toHaveAttribute(
      "aria-pressed",
      "false",
    );
    await expect(afterReload.getByTestId("favorite-count-value")).toHaveText("3");

    expect(errors).toEqual([]);
  });

  test("F7.5 · 收藏失败要回滚：计数与按钮状态一起回到原状，并给出提示", async ({ page }) => {
    // 这个用例**故意**打出一次 500，所以把它登记成预期失败。
    const errors = watchPageErrors(page, {
      allow: (status, url) => status === 500 && url.includes("/favorite"),
    });
    await login(page, AUTHOR);
    await resetMock(page);
    await page.goto("/");

    const card = cardFor(page, "deploy-assistant");
    const button = card.getByTestId("card-favorite-button");
    const count = card.getByTestId("favorite-count-value");
    await expect(count).toHaveText("3");

    // 让 PUT 失败，但延迟 800ms 再失败 —— 这样先能看到乐观更新，再看到回滚。
    await failNext(page, "PUT /api/v1/tools/deploy-assistant/favorite", 800);
    await button.click();

    // ① 乐观：立刻是「已收藏 4」
    await expect(button).toHaveAttribute("aria-pressed", "true");
    await expect(count).toHaveText("4");

    // ② 失败到达：**两者一起**回滚（不存在只回滚一个的半截状态）
    await expect(button).toHaveAttribute("aria-pressed", "false");
    await expect(count).toHaveText("3");
    await expect(page.getByText(/收藏失败，已恢复原状态/)).toBeVisible();

    // 回滚后仍然可以正常操作（不是一次失败就卡死）
    await clickAndSettle(page, button, "/favorite", "PUT");
    await expect(count).toHaveText("4");

    await page.screenshot({ path: `${ARTIFACTS}/m8-favorite-rollback.png`, fullPage: true });
    expect(errors).toEqual([]);
  });

  test("F8 · 详情页点赞：整数计数切换、刷新后保持，且没有平均分/星级", async ({ page }) => {
    const errors = watchPageErrors(page);
    await login(page, AUTHOR);
    await resetMock(page);
    await page.goto("/tools/deploy-assistant");

    const like = page.getByTestId("like-button");
    await expect(like).toHaveAttribute("aria-pressed", "false");
    await expect(page.getByTestId("like-button-count")).toHaveText("6");
    // 卡片上**没有**可点的点赞按钮（F8.2：避免误触）
    await expect(page.getByTestId("card-like-button")).toHaveCount(0);

    // 只有整数，没有小数（§23.7：不做 1~5 平均分）
    await clickAndSettle(page, like, "/like", "PUT");
    await expect(like).toHaveAttribute("aria-pressed", "true");
    await expect(like).toHaveAccessibleName(/取消点赞/);
    await expect(page.getByTestId("like-button-count")).toHaveText(/^\d+$/);
    await expect(page.getByTestId("like-button-count")).toHaveText("7");

    await page.reload();
    await expect(page.getByTestId("like-button")).toHaveAttribute("aria-pressed", "true");
    await expect(page.getByTestId("like-button-count")).toHaveText("7");

    await clickAndSettle(page, page.getByTestId("like-button"), "/like", "DELETE");
    await expect(page.getByTestId("like-button-count")).toHaveText("6");

    expect(errors).toEqual([]);
  });

  test("F7.3/F7.4 · 我的收藏：左栏入口 + 复用门户卡片 + 取消后从列表消失", async ({ page }) => {
    const errors = watchPageErrors(page);
    await login(page, AUTHOR);
    await resetMock(page);
    await page.goto("/");

    // 左栏「我的收藏」（lg 以上可见）
    await page.getByTestId("nav-my-favorites").click();
    await expect(page).toHaveURL(/\/me\/favorites$/);
    await expect(page.getByTestId("my-favorites-page")).toBeVisible();

    // 种子里 zhangsan 只收藏了 log-analyzer；卡片用的是门户同一个组件
    await expect(page.getByTestId("tool-card")).toHaveCount(1);
    const card = cardFor(page, "log-analyzer-a3f2");
    await expect(card).toBeVisible();
    await expect(card.getByTestId("card-favorite-button")).toHaveAttribute("aria-pressed", "true");

    // 取消收藏 → invalidate 后从收藏夹消失
    await card.getByTestId("card-favorite-button").click();
    await expect(page.getByTestId("tool-card")).toHaveCount(0);
    await expect(page.getByText("还没有收藏任何工具")).toBeVisible();

    // UserMenu 里有直达链接
    await page.getByTestId("user-menu-trigger").click();
    await expect(page.getByRole("menuitem", { name: "我的收藏" })).toBeVisible();

    await page.screenshot({ path: `${ARTIFACTS}/m8-my-favorites-empty.png`, fullPage: true });
    expect(errors).toEqual([]);
  });

  test("F7.4 · 匿名不显示「我的收藏」入口（与卡片不显示按钮同一套策略）", async ({ page }) => {
    const errors = watchPageErrors(page);
    await page.goto("/");
    await expect(page.getByTestId("tool-card").first()).toBeVisible();
    await expect(page.getByTestId("portal-side-nav")).toHaveCount(0);
    await expect(page.getByTestId("nav-my-favorites")).toHaveCount(0);
    expect(errors).toEqual([]);
  });

  /* ---------------------------------------------------------------------- F9 */
  test("F9 · 上传重复文件：非阻塞提示 + 跳转链接 + 可关闭，上传本身仍然成功", async ({ page }) => {
    const errors = watchPageErrors(page);
    await login(page, AUTHOR);
    await resetMock(page);
    await page.goto("/me/tools/1/versions");
    await expect(page.getByTestId("version-timeline")).toBeVisible();

    await page.getByRole("button", { name: "上传新版本" }).first().click();
    const dialog = page.getByTestId("version-upload-dialog");
    await expect(dialog).toBeVisible();
    await dialog.getByLabel("版本号 *").fill(`8.8.${Date.now() % 1000}`);
    await dialog.getByLabel("变更说明").fill("### 重复文件（去重提示验收）");
    // mock 的确定性入口：文件名含 `duplicate-hint` → 响应回带 duplicate_of
    const filePath = await makeTempFile("duplicate-hint.zip", "m8 duplicate payload\n");
    await dialog.locator('input[type="file"]').setInputFiles(filePath);
    await dialog.getByRole("button", { name: /上传版本/ }).click();

    // ① 「上传成功」这个事实照旧：toast 说明新版本已提交、旧版本继续服务
    await expect(page.getByText(/继续对外提供服务/)).toBeVisible({ timeout: 20_000 });

    // ② 去重提示是**附加**信息，不遮挡成功
    const notice = page.getByTestId("duplicate-upload-notice");
    await expect(notice).toBeVisible();
    await expect(notice).toContainText(/上传成功/);
    await expect(notice).toContainText(/完全相同/);
    const link = page.getByTestId("duplicate-upload-link");
    await expect(link).toHaveAttribute("href", /^\/tools\/[a-z0-9-]+$/);

    // ③ 可以关闭，关掉之后不再出现
    await page.getByTestId("duplicate-upload-dismiss").click();
    await expect(notice).toHaveCount(0);
    await expect(page.getByTestId("version-timeline")).toBeVisible();

    await page.screenshot({ path: `${ARTIFACTS}/m8-duplicate-notice.png`, fullPage: true });
    expect(errors).toEqual([]);
  });

  test("F9 · 编辑器里的另一个上传入口同样给出提示（两处接线都覆盖）", async ({ page }) => {
    const errors = watchPageErrors(page);
    await login(page, AUTHOR);
    await page.goto("/me/tools/new");
    await expect(page.getByTestId("tool-form")).toBeVisible();
    await page.getByLabel("工具名称").fill(`E2E 去重 ${Date.now().toString(36)}`);
    await page.getByLabel("简介").fill("E2E：编辑器里的去重提示。");
    await page.getByLabel("分类").click();
    await page.getByRole("option", { name: "研发工具" }).click();
    await page.locator("#tool-field-description_md").fill("## 用途\n\n编辑器去重提示。");

    const filePath = await makeTempFile("duplicate-hint.zip", "m8 editor duplicate\n");
    await page.setInputFiles("#version-content-file", filePath);
    await page.getByRole("button", { name: "保存草稿" }).click();

    const notice = page.getByTestId("duplicate-upload-notice");
    await expect(notice).toBeVisible({ timeout: 20_000 });
    await expect(notice).toContainText(/完全相同/);
    // 草稿照常保存成功（提示不阻塞）：落到了编辑页
    await expect(page).toHaveURL(/\/me\/tools\/\d+\/edit/, { timeout: 20_000 });

    expect(errors).toEqual([]);
  });

  /* --------------------------------------------------------------------- F10 */  test("F10 · 预计节省时长：1~1440 校验、留空提交 null（不是 0）", async ({ page }) => {
    const errors = watchPageErrors(page);
    await login(page, AUTHOR);

    await page.goto("/me/tools/new");
    await expect(page.getByTestId("tool-form")).toBeVisible();
    await page.getByLabel("工具名称").fill(`E2E 估算 ${Date.now().toString(36)}`);
    await page.getByLabel("简介").fill("E2E：预计节省时长。");
    await page.getByLabel("分类").click();
    await page.getByRole("option", { name: "研发工具" }).click();
    await page.locator("#tool-field-description_md").fill("## 用途\n\n预计节省时长验收。");

    const field = page.getByLabel("预计节省时长（分钟，可选）");
    // 越界值被前端拦下（不发出请求）
    await field.fill("1500");
    await page.getByRole("button", { name: "保存草稿" }).click();
    await expect(page.getByText(/预计节省时长需在 1 ~ 1440/)).toBeVisible();

    // 说明文案必须点明「单次使用」与「用于平台效率估算」
    await expect(page.getByTestId("section-efficiency")).toContainText("单次使用");
    await expect(page.getByTestId("section-efficiency")).toContainText("效率估算");

    // 留空 → 提交 null
    await field.fill("");
    await page.getByRole("button", { name: "保存草稿" }).click();
    await expect(page).toHaveURL(/\/me\/tools\/\d+\/edit/, { timeout: 20_000 });
    const toolId = Number(/\/me\/tools\/(\d+)\/edit/.exec(page.url())?.[1]);
    expect(Number.isFinite(toolId)).toBe(true);
    await expect.poll(() => savingMinutesOf(page, toolId)).toBeNull();

    // 合法值 → 落地为整数
    await page.getByLabel("预计节省时长（分钟，可选）").fill("60");
    await page.getByRole("button", { name: "保存草稿" }).click();
    await expect.poll(() => savingMinutesOf(page, toolId)).toBe(60);

    await page.screenshot({ path: `${ARTIFACTS}/m8-saving-minutes.png`, fullPage: true });
    expect(errors).toEqual([]);
  });

  test("F10 · 编辑页回显当前值，清空即从估算里移除", async ({ page }) => {
    const errors = watchPageErrors(page);
    await login(page, AUTHOR);
    await resetMock(page);
    await page.goto("/me/tools/1/edit");
    await expect(page.getByTestId("tool-form")).toBeVisible();

    const field = page.getByLabel("预计节省时长（分钟，可选）");
    // 种子里 log-analyzer 填的是 30 分钟
    await expect(field).toHaveValue("30");

    await field.fill("");
    await page.getByRole("button", { name: "保存草稿" }).click();
    await expect.poll(() => savingMinutesOf(page, 1)).toBeNull();

    expect(errors).toEqual([]);
  });

  /* --------------------------------------------------------------------- F11 */
  test("F11 · 数字概览：口径 + 覆盖率 + 估算数字同屏；低覆盖率显式提示；不做图表", async ({
    page,
  }) => {
    const errors = watchPageErrors(page);
    await login(page, ADMIN);
    await page.goto("/admin");
    await expect(page.getByTestId("admin-overview")).toBeVisible();

    const panel = page.getByTestId("admin-insights");
    await expect(panel).toBeVisible();

    // ① 30 日趋势：逐日数字列表（30 条），不是图表
    const daily = page.getByTestId("insights-daily-list");
    await expect(daily.locator("li")).toHaveCount(30);
    await expect(panel.locator("canvas")).toHaveCount(0);

    // ② 活跃贡献者 / 分类分布
    await expect(page.getByTestId("insights-contributors")).toContainText("活跃贡献者");
    await expect(page.getByTestId("insights-categories")).toContainText("分类分布");
    await expect(page.getByTestId("insights-category-list").locator("li").first()).toBeVisible();

    // ③ 效率估算：口径文案、覆盖率、估算数字**同时可见**
    const savings = page.getByTestId("insights-savings");
    await expect(savings).toBeVisible();
    await expect(savings.getByTestId("insights-savings-number")).toContainText("估算");
    await expect(savings.getByTestId("insights-savings-basis")).toContainText("非实测");
    await expect(savings.getByTestId("insights-savings-basis")).toContainText("author_estimate");
    const coverage = savings.getByTestId("insights-savings-coverage");
    await expect(coverage).toContainText("填写覆盖率");
    await expect(coverage).toContainText("未填写预计节省时长");
    await expect(coverage).toContainText(/\d+ \/ \d+/);

    // ④ 覆盖率很低时（种子里只有 4 个工具填了）→ 显式说明数据不足以支撑结论
    await expect(page.getByTestId("insights-savings-low-coverage")).toBeVisible();
    await expect(page.getByTestId("insights-savings-low-coverage")).toContainText(
      /数据不足以支撑结论/,
    );

    await page.screenshot({ path: `${ARTIFACTS}/m8-admin-insights.png`, fullPage: true });
    expect(errors).toEqual([]);
  });

  test("F11 · approver 打开概览页：数字概览降级为说明性空态，页面其余部分照常", async ({
    page,
  }) => {
    // 这两个统计端点真机上是超管专属 → approver 会拿到 403，登记为预期失败。
    // （`/admin/stats/storage` 是既有的，`/admin/stats/insights` 是本轮的 ——
    //  两者在页面上走同一套「区块级降级」处理。）
    const errors = watchPageErrors(page, {
      allow: (status, url) => status === 403 && url.includes("/admin/stats/"),
    });
    await login(page, APPROVER);
    await page.goto("/admin");

    // 四个轻量数字卡照常（`/admin/overview` 对 approver 开放）
    await expect(page.getByTestId("overview-tools-card")).toBeVisible();
    // 重聚合区块降级为说明，而不是让整页报错
    await expect(page.getByTestId("insights-forbidden")).toBeVisible();
    await expect(page.getByTestId("insights-forbidden")).toContainText("仅超级管理员可见");

    expect(errors).toEqual([]);
  });
});
