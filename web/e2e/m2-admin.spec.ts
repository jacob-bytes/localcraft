import { expect, test } from "@playwright/test";

import { ARTIFACTS, login, watchPageErrors } from "./helpers";

/**
 * M2 验收 · 审批台与门户收尾（验收 #18 ~ #24、#26、#27）。
 * 运行在 MSW mock 下；真实后端路径由 `e2e-real/` 覆盖。
 */

test.describe("M2 · 审批台", () => {
  test("18. 审批队列：等待时长高亮 + 分段筛选生效", async ({ page }) => {
    const errors = watchPageErrors(page);
    await login(page);
    await page.goto("/admin/approvals");

    await expect(page.getByTestId("admin-layout")).toBeVisible();
    await expect(page.getByTestId("approval-queue")).toBeVisible();
    const items = page.getByTestId("approval-item");
    await expect(items.first()).toBeVisible();
    const total = await items.count();
    expect(total).toBeGreaterThan(0);

    // 每条都有等待时长文字（不能只靠颜色）
    await expect(page.getByTestId("waiting-badge").first()).toContainText(/等待|小时/);
    // 积压 >24h 的条目用 warning 色（含文字）
    const warned = page.getByTestId("waiting-badge").filter({ hasText: /2[4-9]|[3-9]\d|1\d\d/ });
    expect(await warned.count()).toBeGreaterThan(0);

    // 分段筛选：新工具 + 新版本 = 全部
    await page.getByTestId("approval-filter-new-tool").click();
    await expect(page.getByTestId("approval-item").first()).toBeVisible();
    const newToolCount = await page.getByTestId("approval-item").count();
    await page.getByTestId("approval-filter-new-version").click();
    await expect(page.getByTestId("approval-item").first()).toBeVisible();
    const newVersionCount = await page.getByTestId("approval-item").count();
    expect(newToolCount + newVersionCount).toBe(total);

    await page.getByTestId("approval-filter-all").click();
    await expect(page.getByTestId("approval-item")).toHaveCount(total);
    await page.screenshot({ path: `${ARTIFACTS}/m2-18-queue.png`, fullPage: true });
    expect(errors).toEqual([]);
  });

  test("19. 审批详情抽屉：不跳转门户即可预览包内容", async ({ page }) => {
    const errors = watchPageErrors(page);
    await login(page);
    await page.goto("/admin/approvals");

    // 找一个 skill 或 file 工具的待审条目（有包内容可预览）
    const skillItem = page
      .getByTestId("approval-item")
      .filter({ hasText: /Skill|文件包/ })
      .first();
    await expect(skillItem).toBeVisible();
    await skillItem.click();

    const before = page.url();
    await expect(page.getByTestId("approval-detail")).toBeVisible();
    // 抽屉内展示变更说明 / 包信息，URL 不变（不跳门户，docs/04 §6.10）
    await expect(page.getByTestId("approval-detail")).toContainText(/变更说明|提交|版本/);
    expect(page.url()).toBe(before);

    // 展开包内容预览（按需加载）
    const previewToggle = page.getByRole("button", { name: /包内容预览|预览/ }).first();
    if (await previewToggle.count()) {
      await previewToggle.click();
      await expect(page.getByTestId("approval-detail")).toContainText(/SKILL\.md|文件|包内/);
    }
    await page.screenshot({ path: `${ARTIFACTS}/m2-19-approval-detail.png`, fullPage: true });
    expect(errors).toEqual([]);
  });

  test("20. 驳回：理由 <5 字被拦，二次确认后队列移除该条目", async ({ page }) => {
    const errors = watchPageErrors(page);
    await login(page);
    await page.goto("/admin/approvals");

    await expect(page.getByTestId("approval-item")).toHaveCount(0).catch(() => {});
    const first = page.getByTestId("approval-item").first();
    await expect(first).toBeVisible();
    const itemText = (await first.innerText()).slice(0, 30);
    const before = await page.getByTestId("approval-item").count();
    await first.click();
    await expect(page.getByTestId("approval-detail")).toBeVisible();

    // 点「驳回」先展开理由框
    await page.getByTestId("reject-button").click();
    const reason = page.getByTestId("reject-reason-input");
    await expect(reason).toBeVisible();

    // 理由太短 → 客户端拦下，不发请求
    await reason.fill("不行");
    await page.getByTestId("reject-button").click();
    await expect(page.getByText(/至少 5|5 个字|不少于/).first()).toBeVisible();

    // 合法理由 → 二次确认 → 确认驳回
    await reason.fill("包内 scripts/deploy.sh 含硬编码生产密码，请移除后重新提交。");
    await page.getByTestId("reject-button").click();
    const dialog = page.getByRole("alertdialog");
    await expect(dialog).toBeVisible();
    await expect(dialog).toContainText(/确认驳回/);
    await dialog.getByRole("button", { name: "确认驳回" }).click();

    await expect(page.getByTestId("approval-item")).toHaveCount(before - 1, { timeout: 15_000 });
    await page.screenshot({ path: `${ARTIFACTS}/m2-20-reject.png`, fullPage: true });
    expect(errors).toEqual([]);
    void itemText;
  });

  test("21. 批量批准：按钮显示已选数量，批准后列表刷新", async ({ page }) => {
    const errors = watchPageErrors(page);
    await login(page);
    await page.goto("/admin/approvals");
    // 等队列渲染完成：MSW 的 service worker 需要先接管这个 document，
    // 否则紧跟导航发出的 fetch 会绕过 mock 打到 Vite proxy（得到 500）。
    await expect(page.getByTestId("approval-queue")).toBeVisible();

    // 先制造一个待审条目（用 API：建 prompt 工具 + 上传版本并自动提交）
    const created: {
      id: number;
      uploadStatus: number;
      createStatus: number;
      error?: string;
    } = await page.evaluate(async () => {
      const client = (await import(/* @vite-ignore */ "/src/api/" + "client.ts")) as {
        getAccessToken: () => string | null;
      };
      const token = client.getAccessToken() ?? "";
      const createResponse = await fetch("/api/v1/me/tools", {
        method: "POST",
        headers: { "Content-Type": "application/json", Authorization: `Bearer ${token}` },
        body: JSON.stringify({
          name: `E2E 批量批准 ${Date.now().toString(36)}`,
          summary: "批量批准验证",
          description_md: "## 用途\n\n批量批准。",
          tool_type: "prompt",
        }),
      });
      const toolText = await createResponse.text();
      if (createResponse.status !== 201) {
        return {
          id: 0,
          uploadStatus: 0,
          createStatus: createResponse.status,
          error: toolText.slice(0, 300),
        };
      }
      /* eslint-disable-next-line */
      const tool = JSON.parse(toolText) as { id: number };
      const form = new FormData();
      form.append("version", "1.0.0");
      form.append("changelog_md", "批量批准用例");
      form.append("prompt_content", "你是一个助手。");
      form.append("auto_submit", "true");
      const uploadResponse = await fetch(`/api/v1/me/tools/${tool.id}/versions`, {
        method: "POST",
        headers: { Authorization: `Bearer ${token}` },
        body: form,
      });
      const uploadText = await uploadResponse.text();
      return {
        id: tool.id,
        uploadStatus: uploadResponse.status,
        createStatus: createResponse.status,
        error: uploadResponse.status === 201 ? undefined : uploadText.slice(0, 300),
      };
    });
    expect(created.createStatus, `创建草稿应返回 201（${created.error ?? ""}）`).toBe(201);
    expect(created.uploadStatus, `上传+提交应返回 201（${created.error ?? ""}）`).toBe(201);
    expect(created.id).toBeGreaterThan(0);

    await page.reload();
    const items = page.getByTestId("approval-item");
    await expect(items.first()).toBeVisible();
    const before = await items.count();
    const batchButton = page.getByTestId("batch-approve-button");

    // 未选择时禁用/计数为 0
    await expect(batchButton).toBeDisabled();
    const checkbox = items.first().getByRole("checkbox");
    await checkbox.check();
    await expect(batchButton).toBeEnabled();
    await expect(batchButton).toContainText(/1/);

    await batchButton.click();
    await expect(items).toHaveCount(before - 1, { timeout: 15_000 });
    await page.screenshot({ path: `${ARTIFACTS}/m2-21-batch-approve.png`, fullPage: true });
    expect(errors).toEqual([]);
  });

  test("22. 键盘快捷键：J/K 切换、R 聚焦理由、Esc 关闭、A 批准", async ({ page }) => {
    const errors = watchPageErrors(page);
    await login(page);
    await page.goto("/admin/approvals");

    const items = page.getByTestId("approval-item");
    await expect(items.first()).toBeVisible();
    const total = await items.count();
    expect(total).toBeGreaterThan(1);

    // K/J 选中条目
    await items.first().click();
    await expect(page.getByTestId("approval-detail")).toBeVisible();
    await page.keyboard.press("j");
    await page.waitForTimeout(200);
    await page.keyboard.press("k");
    await page.waitForTimeout(200);

    // R 聚焦驳回理由输入框
    await page.keyboard.press("r");
    await expect(page.getByTestId("reject-reason-input")).toBeFocused();

    // Esc 关闭抽屉
    await page.keyboard.press("Escape");
    await expect(page.getByTestId("approval-detail")).toHaveCount(0);

    // A 批准当前选中条目（低风险操作，不弹二次确认）
    await items.first().click();
    await expect(page.getByTestId("approval-detail")).toBeVisible();
    await page.keyboard.press("a");
    await expect(items).toHaveCount(total - 1, { timeout: 15_000 });
    expect(errors).toEqual([]);
  });

  test("23+24. 白名单总开关关闭时告警；审批模式切换弹警告并展示待审数量", async ({ page }) => {
    const errors = watchPageErrors(page);
    await login(page);
    await page.goto("/admin/settings");
    await expect(page.getByTestId("settings-approval-mode")).toBeVisible();

    // 24：切到「全部放行」需要先确认。
    // M3 起审批模式是 Radix Select（元信息驱动的控件），关闭态不渲染选项文案，
    // 必须先点触发器再选 option。
    await page.getByTestId("settings-approval-mode").click();
    await page.getByRole("option", { name: /全部放行/ }).click();
    const warning = page.getByRole("alertdialog");
    await expect(warning).toBeVisible();
    await warning.getByRole("button").last().click();

    // 关闭免审白名单总开关并保存
    const whitelistSwitch = page.locator("#setting-approval-whitelist_enabled");
    await expect(whitelistSwitch).toBeVisible();
    if ((await whitelistSwitch.getAttribute("aria-checked")) === "true") {
      await whitelistSwitch.click();
    }
    await expect(whitelistSwitch).toHaveAttribute("aria-checked", "false");
    await page.getByTestId("settings-save").click();

    // 保存响应里的 warnings（如有待审条目）会以对话框展示
    const warnings = page.getByRole("alertdialog");
    if (await warnings.count()) {
      await expect(warnings.first()).toContainText(/待审|放行/);
      await warnings.first().getByRole("button").last().click();
    }

    // 23：白名单页显示「功能已关闭」告警（用客户端导航，避免整页刷新重置 mock 状态）
    await page.getByRole("link", { name: /免审白名单/ }).click();
    await expect(page).toHaveURL(/\/admin\/whitelist/);
    await expect(page.getByTestId("whitelist-warning")).toBeVisible();
    await expect(page.getByTestId("whitelist-warning")).toContainText(/关闭|不生效/);
    await page.screenshot({ path: `${ARTIFACTS}/m2-23-whitelist-warning.png`, fullPage: true });
    expect(errors).toEqual([]);
  });

  test("审批历史：筛选 + 表格 + 客户端 CSV 导出", async ({ page }) => {
    const errors = watchPageErrors(page);
    await login(page);
    await page.goto("/admin/approvals/history");

    // mock 里已有审批记录（种子的提交/批准/驳回）
    await expect(page.getByRole("table")).toBeVisible();
    const rows = page.locator("tbody tr");
    await expect(rows.first()).toBeVisible();

    const downloadPromise = page.waitForEvent("download");
    await page.getByRole("button", { name: /导出 CSV/ }).click();
    const download = await downloadPromise;
    expect(download.suggestedFilename()).toMatch(/\.csv$/);
    expect(errors).toEqual([]);
  });

  test("approver 角色看不到超管专属菜单（白名单 / 系统设置）", async ({ page }) => {
    const errors = watchPageErrors(page);
    // mock 的 wangwu 是 approver
    await page.goto("/login");
    await page.getByLabel("用户名", { exact: true }).fill("wangwu");
    await page.getByLabel("密码", { exact: true }).fill("Author@12345");
    await page.getByRole("button", { name: "登录", exact: true }).click();
    await expect(page).toHaveURL(/\/$/);

    await page.goto("/admin/approvals");
    await expect(page.getByTestId("admin-layout")).toBeVisible();

    // M3 起菜单按角色过滤（docs/04 §5.2）：approver 看得到的三项 + 审批两项 + 分类/标签
    for (const label of ["概览", "审批队列", "审批历史", "全站工具", "分类", "标签"]) {
      await expect(page.getByRole("link", { name: new RegExp(label) })).toBeVisible();
    }
    // 超管专属项**不渲染**（不是禁用）——禁用态会泄露功能边界
    for (const label of [
      "免审白名单",
      "系统设置",
      "用户",
      "用户组",
      "API Token",
      "导入导出",
      "回收站",
    ]) {
      await expect(page.getByRole("link", { name: new RegExp(`^${label}$`) })).toHaveCount(0);
    }

    // 直接输 URL 也会被 RequireRole 拦成 403 页
    await page.goto("/admin/settings");
    await expect(page.getByText("没有访问权限")).toBeVisible();
    await page.goto("/admin/users");
    await expect(page.getByText("没有访问权限")).toBeVisible();
    expect(errors).toEqual([]);
  });
});

test.describe("M2 · 门户收尾", () => {
  test("26. 分页：26 个工具在 12/24/48 档分别得到 3/2/1 页并可真翻页", async ({ page }) => {
    const errors = watchPageErrors(page);
    await login(page);
    // 等门户渲染，确保 MSW 已接管（否则 in-page fetch 会绕过 mock）
    await expect(page.getByTestId("tool-card").first()).toBeVisible();

    const total = await page.evaluate(async () => {
      const client = (await import(/* @vite-ignore */ "/src/api/" + "client.ts")) as {
        getAccessToken: () => string | null;
      };
      const token = client.getAccessToken();
      const response = await fetch("/api/v1/tools?page=1&page_size=48", {
        headers: { Authorization: `Bearer ${token ?? ""}` },
      });
      const body = (await response.json()) as { total: number };
      return body.total;
    });
    // CONTRACT §14.6 要求「12/24/48 档分别得到 3/2/1 页」，这要求门户可见工具数在
    // [25, 36]：mock 的 26 个种子工具 + 2 个 `pending_update` 状态工具（它们按
    // FR-VER-03 也必须出现在门户）= 28。
    test.info().annotations.push({ type: "portal-total", description: String(total) });
    expect(total, "门户可见工具数需落在 3/2/1 页的区间内").toBeGreaterThanOrEqual(25);
    expect(total).toBeLessThanOrEqual(36);

    for (const [pageSize, expectedPages] of [
      [12, 3],
      [24, 2],
      [48, 1],
    ] as const) {
      await page.goto(`/?page_size=${pageSize}`);
      await expect(page.getByTestId("tool-card").first()).toBeVisible();
      const pagination = page.getByRole("navigation", { name: "分页" });
      if (expectedPages === 1) {
        await expect(pagination).toHaveCount(0);
        expect(await page.getByTestId("tool-card").count()).toBe(total);
        continue;
      }
      await expect(pagination).toBeVisible();
      const pageButtons = pagination.getByRole("button", { name: /^第 \d+ 页$/ });
      expect(await pageButtons.count()).toBe(expectedPages);
      // 实际翻页：第 2 页内容与第 1 页不同
      const firstPageName = await page.getByTestId("tool-card-name").first().innerText();
      await pagination.getByRole("button", { name: "第 2 页" }).click();
      await expect(page).toHaveURL(/page=2/);
      await expect(page.getByTestId("tool-card").first()).toBeVisible();
      // keepPreviousData 会先渲染上一页的数据，必须等到新一页到位再比较
      await expect
        .poll(async () => page.getByTestId("tool-card-name").first().innerText(), {
          message: "第 2 页内容必须与第 1 页不同",
        })
        .not.toBe(firstPageName);
      // 左侧分类计数在翻页后仍在（facets 只在 page=1 返回）
      await expect(page.getByRole("navigation", { name: "按分类筛选" })).toContainText(/\d/);
    }
    await page.screenshot({ path: `${ARTIFACTS}/m2-26-pagination.png`, fullPage: true });
    expect(errors).toEqual([]);
  });

  test("27. ⌘K 搜索选中工具 → 跳 /tools/:slug（不是 /?q=）", async ({ page }) => {
    const errors = watchPageErrors(page);
    await login(page);
    await expect(page.getByTestId("tool-card").first()).toBeVisible();

    await page.keyboard.press("ControlOrMeta+k");
    const dialog = page.getByRole("dialog");
    await expect(dialog).toBeVisible();
    await dialog.getByRole("combobox").fill("日志");
    // 结果列表里还有一行「在门户中搜索…」（跳 ?q=），验收要的是**工具结果**
    const toolOption = dialog.getByRole("option").filter({ hasNotText: "在门户中搜索" }).first();
    await expect(toolOption).toBeVisible();
    await toolOption.click();

    await expect(page).toHaveURL(/\/tools\/[^?]+$/);
    await expect(page.getByTestId("tool-detail")).toBeVisible();
    expect(errors).toEqual([]);
  });

  test("25. Badge warning / success 变体在用（不再散落显式颜色类）", async ({ page }) => {
    const errors = watchPageErrors(page);
    await login(page);

    // 待审工具的详情页有 warning 徽标；已在版本时间线上用 success 表示「当前」
    await page.goto("/me/tools?status=pending_update");
    const row = page.getByTestId("my-tool-row").first();
    if (await row.count()) {
      await expect(row.getByText(/待审|新版/).first()).toBeVisible();
    }
    await page.goto("/tools/log-analyzer-a3f2");
    await expect(page.getByTestId("version-item").filter({ hasText: "当前" }).first()).toBeVisible();
    const variants = await page
      .locator('[data-slot="badge"]')
      .evaluateAll((els) => els.map((el) => el.getAttribute("data-variant")));
    expect(variants).toContain("success");
    expect(errors).toEqual([]);
  });

  test("无障碍：交互元素可聚焦、图标按钮有名称（详情页抽查）", async ({ page }) => {
    const errors = watchPageErrors(page);
    await login(page);
    await page.goto("/tools/log-analyzer-a3f2");
    await expect(page.getByTestId("tool-detail")).toBeVisible();

    const nameless = await page.evaluate(() => {
      const out: string[] = [];
      for (const button of Array.from(document.querySelectorAll("button"))) {
        const name = (button.getAttribute("aria-label") || button.textContent || "").trim();
        if (!name) out.push(button.outerHTML.slice(0, 60));
      }
      return out;
    });
    expect(nameless).toEqual([]);

    // 键盘可达：Tab 至少能落到几个可交互元素上
    await page.keyboard.press("Tab");
    const focused = await page.evaluate(() => document.activeElement?.tagName ?? "");
    expect(["A", "BUTTON", "INPUT"]).toContain(focused);
    expect(errors).toEqual([]);
  });
});
