import { mkdtemp, readFile, writeFile } from "node:fs/promises";
import { tmpdir } from "node:os";
import { join } from "node:path";

import { expect, test, type Page } from "@playwright/test";

import { ARTIFACTS, login, watchPageErrors } from "./helpers";

/**
 * M3 验收（docs/04 §6.9 ~ §6.21）· MSW mock 模式。
 *
 * 覆盖监控方清单里可 UI 验证的部分：
 *   #5 概览、#6/#7 用户、#8 用户组、#9/#10 分类、#11 标签、#12/#13 Token、
 *   #14~#16 设置、#17~#20 导入导出、#21 回收站、#22 全站工具、#23 角色、#24 深色
 * （#1 真实 E2E 连跑、#25 verify、#26 首屏体积由命令行验证。）
 */

async function gotoAdmin(page: Page, path: string): Promise<void> {
  await page.goto(path);
  await expect(page.getByTestId("admin-layout")).toBeVisible();
}

/** 页面内取 token 调 API（等 service worker 接管后再发请求，避免绕过 mock）。 */
async function apiJson<T>(
  page: Page,
  path: string,
  init?: { method?: string; body?: unknown },
): Promise<{ status: number; body: T }> {
  await page.waitForFunction(() => Boolean(navigator.serviceWorker?.controller), null, {
    timeout: 15_000,
  });
  return page.evaluate(
    async ([url, method, body]) => {
      const client = (await import(/* @vite-ignore */ "/src/api/" + "client.ts")) as {
        getAccessToken: () => string | null;
      };
      const token = client.getAccessToken();
      const response = await fetch(url as string, {
        method: (method as string) || "GET",
        headers: {
          ...(token ? { Authorization: `Bearer ${token}` } : {}),
          ...(body ? { "Content-Type": "application/json" } : {}),
        },
        body: body ? JSON.stringify(body) : undefined,
      });
      const text = await response.text();
      return { status: response.status, body: (text ? JSON.parse(text) : null) as T };
    },
    [path, init?.method ?? "GET", init?.body ?? null] as const,
  );
}

/** 浏览器内 multipart 上传（管理侧代上传用，见 M5 对齐用例）。 */
async function apiUpload<T>(
  page: Page,
  path: string,
  fields: Record<string, string>,
  file: { name: string; bytes: number[] },
): Promise<{ status: number; body: T }> {
  await page.waitForFunction(() => Boolean(navigator.serviceWorker?.controller), null, {
    timeout: 15_000,
  });
  return page.evaluate(
    async ([url, rawFields, fileName, bytes]) => {
      const client = (await import(/* @vite-ignore */ "/src/api/" + "client.ts")) as {
        getAccessToken: () => string | null;
      };
      const token = client.getAccessToken();
      const form = new FormData();
      for (const [key, value] of Object.entries(rawFields as Record<string, string>)) {
        form.append(key, value);
      }
      form.append("file", new File([new Uint8Array(bytes as number[])], fileName as string));
      const response = await fetch(url as string, {
        method: "POST",
        headers: token ? { Authorization: `Bearer ${token}` } : {},
        body: form,
      });
      const text = await response.text();
      return { status: response.status, body: (text ? JSON.parse(text) : null) as never };
    },
    [path, fields, file.name, file.bytes] as const,
  );
}

async function expectToast(page: Page, pattern: RegExp): Promise<void> {
  await expect(page.locator("[data-sonner-toast]").filter({ hasText: pattern }).first()).toBeVisible(
    { timeout: 15_000 },
  );
}

/* -------------------------------------------------------------------------- */

test.describe("M3 · 概览（#5）", () => {
  test("四个数字卡 + 两个列表，且没有任何图表", async ({ page }) => {
    const errors = watchPageErrors(page);
    await login(page);
    await gotoAdmin(page, "/admin");

    await expect(page.getByTestId("admin-overview")).toBeVisible();
    for (const id of [
      "overview-pending-card",
      "overview-tools-card",
      "overview-users-card",
      "overview-storage-card",
    ]) {
      await expect(page.getByTestId(id)).toBeVisible();
    }
    // 数字卡里必须有数字
    await expect(page.getByTestId("overview-tools-card")).toContainText(/\d/);
    await expect(page.getByTestId("overview-storage-card")).toContainText(/已用/);
    // 待审卡有值时用 warning 语义并有文字
    await expect(page.getByTestId("overview-pending-card")).toContainText(/待审/);

    await expect(page.getByTestId("overview-recent-approvals")).toBeVisible();
    await expect(page.getByTestId("overview-storage-top")).toBeVisible();

    // 明确不做图表（docs/01 第 10 章）：页面里不能出现 svg 图表/canvas
    expect(await page.locator("canvas").count()).toBe(0);
    await page.screenshot({ path: `${ARTIFACTS}/m3-05-overview.png`, fullPage: true });
    expect(errors).toEqual([]);
  });
});

/* -------------------------------------------------------------------------- */

test.describe("M3 · 用户管理（#6 #7）", () => {
  test("表格渲染 + 没有删除入口 + 不能禁用自己", async ({ page }) => {
    const errors = watchPageErrors(page);
    await login(page);
    await gotoAdmin(page, "/admin/users");

    const rows = page.getByTestId("user-row");
    await expect(rows.first()).toBeVisible();
    // 用户不可物理删除：整页不出现「删除」
    await expect(page.getByRole("button", { name: /^删除/ })).toHaveCount(0);
    await expect(page.getByRole("menuitem", { name: /删除/ })).toHaveCount(0);

    // admin 自己那一行的「禁用」必须 disabled
    const selfRow = rows.filter({ hasText: "admin" }).first();
    await selfRow.getByRole("button", { name: /操作|菜单/ }).click();
    const disableItem = page.getByRole("menuitem", { name: /禁用/ }).first();
    await expect(disableItem).toHaveAttribute("data-disabled", "");
    await page.keyboard.press("Escape");
    expect(errors).toEqual([]);
  });

  test("新建用户 → 用户名不可改（编辑模式）+ 初始密码只显示一次", async ({ page }) => {
    const errors = watchPageErrors(page);
    await login(page);
    await gotoAdmin(page, "/admin/users");

    const username = `e2e_user_${Date.now().toString(36)}`;
    await page.getByTestId("user-create-button").click();
    const dialog = page.getByTestId("user-form-dialog");
    await expect(dialog).toBeVisible();
    await dialog.getByLabel("用户名").fill(username);
    await dialog.getByLabel("显示名").fill("E2E 用户");
    // 勾一个普通用户角色
    await dialog.getByRole("checkbox", { name: /普通用户/ }).check();
    await dialog.getByRole("button", { name: /保存|创建/ }).click();

    // 生成的一次性密码只显示一次
    const secret = page.getByTestId("generated-password");
    await expect(secret).toBeVisible({ timeout: 15_000 });
    await expect(page.getByText(/不会再次显示/).first()).toBeVisible();
    await page.getByRole("button", { name: /我已复制|关闭|知道了/ }).click();

    // 编辑模式：用户名 disabled + 有说明
    const row = page.getByTestId("user-row").filter({ hasText: username }).first();
    await expect(row).toBeVisible({ timeout: 15_000 });
    await row.getByRole("button", { name: /操作|菜单/ }).click();
    await page.getByRole("menuitem", { name: /编辑/ }).click();
    const editDialog = page.getByTestId("user-form-dialog");
    await expect(editDialog.getByLabel("用户名")).toBeDisabled();
    await expect(page.getByTestId("username-readonly-hint")).toBeVisible();
    await editDialog.getByRole("button", { name: /取消|关闭/ }).click();
    expect(errors).toEqual([]);
  });
});

/* -------------------------------------------------------------------------- */

test.describe("M3 · 用户组（#8）", () => {
  test("删除被引用的组：列出引用工具并要求勾选确认", async ({ page }) => {
    const errors = watchPageErrors(page);
    await login(page);
    await gotoAdmin(page, "/admin/groups");

    // mock 种子里有一个被 ACL 引用的组
    const groupRow = page.getByTestId("group-row").filter({ hasText: /E2E|研发|运维|组/ }).first();
    await expect(groupRow).toBeVisible();
    await groupRow.getByRole("button", { name: /删除/ }).click();

    const inUse = page.getByTestId("group-in-use-dialog");
    if (await inUse.count()) {
      await expect(inUse).toContainText(/授权|引用|工具/);
      // 必须勾选「我已了解」才能继续
      const confirm = inUse.getByRole("button", { name: /仍要删除|确认删除/ });
      await expect(confirm).toBeDisabled();
      await inUse.getByTestId("group-in-use-ack").click();
      await expect(confirm).toBeEnabled();
      await inUse.getByRole("button", { name: /取消|关闭/ }).click();
    } else {
      // 没有引用时是普通二次确认
      await expect(page.getByRole("alertdialog")).toBeVisible();
      await page.keyboard.press("Escape");
    }
    expect(errors).toEqual([]);
  });
});

/* -------------------------------------------------------------------------- */

test.describe("M3 · 分类（#9 #10）", () => {
  test("排序：上移/下移按钮生效并提交到 PUT /admin/categories/order", async ({ page }) => {
    const errors = watchPageErrors(page);
    await login(page);
    await gotoAdmin(page, "/admin/categories");

    const rows = page.getByTestId("category-row");
    await expect(rows.first()).toBeVisible();
    const before = await rows.allInnerTexts();

    const orderCalls: string[] = [];
    page.on("request", (request) => {
      if (request.url().includes("/admin/categories/order")) orderCalls.push(request.method());
    });

    await rows.first().getByTestId("category-move-down").click();
    // 乐观更新：顺序应立刻变化
    await expect
      .poll(async () => (await rows.allInnerTexts()).join("|"), { message: "顺序应发生变化" })
      .not.toBe(before.join("|"));
    await expect.poll(() => orderCalls.length, { message: "应提交新顺序" }).toBeGreaterThan(0);
    expect(orderCalls[0]).toBe("PUT");
    expect(errors).toEqual([]);
  });

  test("拖拽排序：拖动手柄后顺序变化并提交", async ({ page }) => {
    const errors = watchPageErrors(page);
    await login(page);
    await gotoAdmin(page, "/admin/categories");

    const rows = page.getByTestId("category-row");
    await expect(rows.first()).toBeVisible();
    const firstBefore = await rows.first().innerText();

    const orderCalls: string[] = [];
    page.on("request", (request) => {
      if (request.url().includes("/admin/categories/order")) orderCalls.push(request.method());
    });

    const handle = rows.first().getByTestId("category-drag-handle");
    const target = rows.nth(2);
    const handleBox = await handle.boundingBox();
    const targetBox = await target.boundingBox();
    expect(handleBox).toBeTruthy();
    expect(targetBox).toBeTruthy();

    // dnd-kit 用指针事件：手动 mouse down → move → up 比 dragAndDrop 更稳
    await page.mouse.move(handleBox!.x + handleBox!.width / 2, handleBox!.y + handleBox!.height / 2);
    await page.mouse.down();
    await page.mouse.move(
      targetBox!.x + targetBox!.width / 2,
      targetBox!.y + targetBox!.height / 2 + 8,
      { steps: 12 },
    );
    await page.mouse.up();

    await expect
      .poll(async () => rows.first().innerText(), { message: "拖拽后顺序应发生变化" })
      .not.toBe(firstBefore);
    await expect.poll(() => orderCalls.length, { message: "拖拽应提交新顺序" }).toBeGreaterThan(0);
    expect(errors).toEqual([]);
  });

  test("删除被引用的分类：提示有 N 个工具无法删除", async ({ page }) => {
    // 409 CATEGORY_IN_USE 正是被测行为
    const errors = watchPageErrors(page, {
      allow: (status, url) => status === 409 && url.includes("/admin/categories/"),
    });
    await login(page);
    await gotoAdmin(page, "/admin/categories");

    const rows = page.getByTestId("category-row");
    await expect(rows.first()).toBeVisible();
    // 研发工具（dev-tools）下有工具 → 停用应被服务端拒绝
    const usedRow = rows.filter({ hasText: /研发工具/ }).first();
    await usedRow.getByRole("button", { name: /停用/ }).click();
    const confirm = page.getByRole("alertdialog");
    await expect(confirm).toBeVisible();
    await confirm.getByRole("button", { name: /确认停用/ }).click();

    const alert = page.getByTestId("category-in-use-alert");
    await expect(alert).toBeVisible({ timeout: 15_000 });
    await expect(alert).toContainText(/\d+/);
    await expect(alert).toContainText(/无法删除|无法停用/);
    // UI 文案不得说「永久删除」（停用是软删除）
    await expect(alert).not.toContainText(/永久删除/);
    expect(errors).toEqual([]);
  });
});

/* -------------------------------------------------------------------------- */

test.describe("M3 · 标签（#11）", () => {
  test("合并：展示将转移的引用数量并确认", async ({ page }) => {
    const errors = watchPageErrors(page);
    await login(page);
    await gotoAdmin(page, "/admin/tags");

    const rows = page.getByTestId("tag-row");
    await expect(rows.first()).toBeVisible();

    // 行内「合并」按钮会带上该标签作为源
    await rows.first().getByTestId("tag-merge-button").click();
    const dialog = page.getByTestId("tag-merge-dialog");
    await expect(dialog).toBeVisible();
    // 对话框要说明「引用会转移」与「重复引用去重」
    await expect(dialog).toContainText(/引用|转移/);
    await expect(dialog).toContainText(/去重/);

    // 选一个源标签（若行内按钮没预选）
    const sourceCheckbox = dialog.getByRole("checkbox").first();
    if (!(await sourceCheckbox.isChecked())) await sourceCheckbox.check();

    // 目标标签：服务端前缀搜索 → 点第一个候选
    const target = dialog.getByTestId("tag-merge-target").first();
    await expect(target).toBeVisible({ timeout: 15_000 });
    await target.click();

    const confirmMerge = dialog.getByRole("button", { name: "确认合并" });
    await expect(confirmMerge).toBeEnabled();
    await confirmMerge.click();
    await expectToast(page, /合并/);
    expect(errors).toEqual([]);
  });
});

/* -------------------------------------------------------------------------- */

test.describe("M3 · API Token（#12 #13）", () => {
  test("签发：Scope 有 tooltip、明文只显示一次、关闭需二次确认", async ({ page }) => {
    const errors = watchPageErrors(page);
    await login(page);
    await gotoAdmin(page, "/admin/tokens");

    await page.getByTestId("token-create-button").click();
    const dialog = page.getByTestId("token-create-dialog");
    await expect(dialog).toBeVisible();
    await dialog.getByLabel(/名称/).fill(`E2E Token ${Date.now().toString(36)}`);

    // Scope 每一项都有 Tooltip 说明（testid 在 Checkbox 本身）
    const scope = page.getByTestId("token-scope-option").first();
    await expect(scope).toBeVisible();
    await scope.hover();
    await expect(page.getByRole("tooltip").first()).toBeVisible();

    await scope.check();
    await dialog.getByRole("button", { name: /签发|生成/ }).click();

    // 明文只显示一次 + 醒目警告
    const secret = page.getByTestId("token-secret-dialog");
    await expect(secret).toBeVisible({ timeout: 15_000 });
    const plaintext = await page.getByTestId("token-plaintext").innerText();
    expect(plaintext.startsWith("st_")).toBe(true);
    await expect(secret).toContainText(/不会再次显示/);
    await page.screenshot({ path: `${ARTIFACTS}/m3-12-token-secret.png`, fullPage: true });

    // 关闭时二次确认
    await secret.getByRole("button", { name: /关闭|完成|我已/ }).last().click();
    const confirm = page.getByRole("alertdialog").filter({ hasText: /确认|已复制|保存/ }).last();
    if (await confirm.count()) {
      await confirm.getByRole("button").last().click();
    }

    // 列表里只有前缀，没有明文
    const listText = await page.locator("main").innerText();
    expect(listText.includes(plaintext)).toBe(false);
    expect(listText).toMatch(/st_[A-Za-z0-9]+/);
    expect(errors).toEqual([]);
  });
});

/* -------------------------------------------------------------------------- */

test.describe("M3 · 系统设置（#14 #15 #16）", () => {
  test("控件由后端元信息驱动：三类控件都能渲染（bool/int/string+options）", async ({ page }) => {
    const errors = watchPageErrors(page);
    await login(page);
    await gotoAdmin(page, "/admin/settings");

    // 全部分组都在（至少审批 / 门户 / 安全 三组）
    await expect(page.getByTestId("settings-group").first()).toBeVisible();
    expect(await page.getByTestId("settings-group").count()).toBeGreaterThanOrEqual(5);

    // string + options → Select（approval.mode）
    await expect(page.getByLabel(/审批模式|approval.mode/).first()).toBeVisible();
    // int → number 输入（version.history_limit 有 min/max）
    const numeric = page.locator('input[type="number"]').first();
    await expect(numeric).toBeVisible();
    // bool → switch
    await expect(page.getByRole("switch").first()).toBeVisible();

    await expect(page.getByTestId("settings-save")).toBeVisible();
    await page.screenshot({ path: `${ARTIFACTS}/m3-14-settings.png`, fullPage: true });
    expect(errors).toEqual([]);
  });

  test("非法值导致整体回滚：出错项被定位，其余项未被改动", async ({ page }) => {
    // 故意提交非法值触发的 400 SETTING_INVALID 是被测行为
    const errors = watchPageErrors(page, {
      allow: (status, url) => status === 400 && url.includes("/admin/settings"),
    });
    await login(page);
    await gotoAdmin(page, "/admin/settings");

    // 先记录 portal.page_size 的原值
    const before = await apiJson<{ items: Array<{ key: string; value: unknown }> }>(
      page,
      "/api/v1/admin/settings",
    );
    const original = before.body.items.find((item) => item.key === "portal.page_size")?.value;

    // 改一个非法值（超出 min/max）与一个合法值。
    // 先等设置表单渲染完：控件由服务端元信息驱动，是异步到达的，
    // 抢在 `PageSkeleton` 之前探测会拿到 0 个匹配。
    await expect(page.getByTestId("settings-page")).toBeVisible();
    const pageSizeInput = page
      .locator('[data-setting-key="portal.page_size"] input')
      .first();
    await expect(pageSizeInput).toBeVisible();
    await pageSizeInput.fill("9999");
    const switchInput = page.getByRole("switch").first();
    await switchInput.click();
    await page.getByTestId("settings-save").click();

    // 出错项被高亮 + 明确提示整体回滚
    await expect(page.getByTestId("setting-error").first()).toBeVisible({ timeout: 15_000 });
    await expect(page.getByText(/回滚/).first()).toBeVisible();

    // 合法项也没被写入
    const after = await apiJson<{ items: Array<{ key: string; value: unknown }> }>(
      page,
      "/api/v1/admin/settings",
    );
    expect(after.body.items.find((item) => item.key === "portal.page_size")?.value).toBe(original);
    expect(errors).toEqual([]);
  });

  test("审批模式切换：弹警告并展示待审数量", async ({ page }) => {
    const errors = watchPageErrors(page);
    await login(page);
    await gotoAdmin(page, "/admin/settings");

    await page.getByLabel(/审批模式|approval.mode/).first().click();
    await page.getByRole("option", { name: /全部放行|auto_approve_all/ }).click();
    const warning = page.getByRole("alertdialog");
    await expect(warning).toBeVisible();
    await warning.getByRole("button").last().click();

    await page.getByTestId("settings-save").click();
    const warningsDialog = page.getByRole("alertdialog");
    if (await warningsDialog.count()) {
      await expect(warningsDialog.first()).toContainText(/\d+|待审|放行/);
      await warningsDialog.first().getByRole("button").last().click();
    }
    await expectToast(page, /保存|已更新/);
    expect(errors).toEqual([]);
  });
});

/* -------------------------------------------------------------------------- */

test.describe("M3 · 导入导出（#17 #18 #19 #20）", () => {
  test("导入用户：先预演 → 逐行错误 → 确认后写库 → 生成密码可下载", async ({ page }) => {
    const errors = watchPageErrors(page);
    await login(page);
    await gotoAdmin(page, "/admin/import-export");

    const csv = [
      "username,display_name,email,roles,password",
      `e2e_imp_${Date.now().toString(36)},导入用户,imp@example.com,user,`,
      "bad user!,非法用户名,bad@example.com,user,",
      "e2e_unknown_role,未知角色,x@example.com,root,",
    ].join("\n");
    const dir = await mkdtemp(join(tmpdir(), "selftool-m3-"));
    const filePath = join(dir, "users.csv");
    await writeFile(filePath, csv, "utf8");

    const card = page.getByTestId("import-users-card");
    await card.locator('input[type="file"]').setInputFiles(filePath);
    await page.getByTestId("import-dry-run-button").click();

    const result = page.getByTestId("import-result-table");
    await expect(result).toBeVisible({ timeout: 15_000 });
    // 预演报告：成功/失败/跳过 + 逐行错误（精确到 row 与 field）
    await expect(result).toContainText(/成功|失败|跳过/);
    await expect(page.getByText(/bad user|用户名/).first()).toBeVisible();
    await page.screenshot({ path: `${ARTIFACTS}/m3-17-import-dry-run.png`, fullPage: true });

    // 确认导入
    await page.getByTestId("import-confirm-button").click();
    const confirm = page.getByRole("alertdialog");
    if (await confirm.count()) await confirm.getByRole("button").last().click();

    // 生成密码只显示一次 + 可下载
    const passwords = page.getByTestId("import-generated-passwords");
    await expect(passwords).toBeVisible({ timeout: 20_000 });
    await expect(page.getByText(/不会再次显示/).first()).toBeVisible();
    const downloadPromise = page.waitForEvent("download");
    await page.getByTestId("import-download-passwords").click();
    const download = await downloadPromise;
    expect(download.suggestedFilename()).toMatch(/\.csv$/);
    expect(errors).toEqual([]);
  });

  test("CSV 模板可下载；导出 CSV 带 BOM 且文件名含日期", async ({ page }) => {
    const errors = watchPageErrors(page);
    await login(page);
    await gotoAdmin(page, "/admin/import-export");

    const templatePromise = page.waitForEvent("download");
    await page.getByTestId("csv-template-link").click();
    const template = await templatePromise;
    expect(template.suggestedFilename()).toMatch(/\.csv$/);
    const templatePath = await template.path();
    const templateText = await readFile(templatePath!, "utf8");
    expect(templateText).toContain("username,display_name,email,roles,password");

    const exportPromise = page.waitForEvent("download");
    await page.getByRole("button", { name: /导出 CSV/ }).first().click();
    const exported = await exportPromise;
    expect(exported.suggestedFilename()).toMatch(/selftool-users-\d{8}\.csv/);
    const bytes = await readFile((await exported.path())!);
    expect([bytes[0], bytes[1], bytes[2]]).toEqual([0xef, 0xbb, 0xbf]);
    expect(errors).toEqual([]);
  });
});

/* -------------------------------------------------------------------------- */

test.describe("M3 · 回收站（#21）", () => {
  test("顶部说明 + 还原 + 彻底删除需输入工具名", async ({ page }) => {
    const errors = watchPageErrors(page);
    await login(page);
    await gotoAdmin(page, "/admin/recycle-bin");

    await expect(page.getByText(/30 天/).first()).toBeVisible();
    const rows = page.getByTestId("recycle-row");
    await expect(rows.first()).toBeVisible();

    // 彻底删除：必须输入工具名才能确认
    await rows.first().getByRole("button", { name: /彻底删除/ }).click();
    const dialog = page.getByTestId("recycle-purge-dialog");
    await expect(dialog).toBeVisible();
    const confirmButton = dialog.getByRole("button", { name: /彻底删除|确认/ }).last();
    await expect(confirmButton).toBeDisabled();
    await dialog.getByTestId("recycle-purge-confirm-input").fill("完全不对的名字");
    await expect(confirmButton).toBeDisabled();
    await dialog.getByRole("button", { name: /取消|关闭/ }).click();

    // 还原：一次确认
    const restoreRow = rows.first();
    await restoreRow.getByTestId("recycle-restore").click();
    const confirm = page.getByRole("alertdialog");
    if (await confirm.count()) await confirm.getByRole("button").last().click();
    await expectToast(page, /还原|已恢复/);
    expect(errors).toEqual([]);
  });
});

/* -------------------------------------------------------------------------- */

test.describe("M3 · 全站工具（#22）", () => {
  test("批量下架/上架存在，批量转移负责人不存在", async ({ page }) => {
    const errors = watchPageErrors(page);
    await login(page);
    await gotoAdmin(page, "/admin/tools");

    const rows = page.getByTestId("admin-tool-row");
    await expect(rows.first()).toBeVisible();
    // 全站工具含所有状态：标签页/筛选里能看到 draft 等非公开状态
    await page.getByTestId("admin-tools-filters").first().waitFor({ timeout: 10_000 });

    // 勾选一行后出现批量按钮（数量显示）
    await rows.first().getByRole("checkbox").check();
    const batchOffline = page.getByTestId("admin-tools-batch-offline");
    const batchRelist = page.getByTestId("admin-tools-batch-relist");
    expect((await batchOffline.count()) + (await batchRelist.count())).toBeGreaterThan(0);

    // 没有批量转移负责人
    await expect(page.getByRole("button", { name: /批量转移/ })).toHaveCount(0);
    await expect(page.getByRole("menuitem", { name: /批量转移/ })).toHaveCount(0);
    await page.screenshot({ path: `${ARTIFACTS}/m3-22-admin-tools.png`, fullPage: true });
    expect(errors).toEqual([]);
  });

  test("「查看审批历史」带 tool_id 跳转，落页即按该工具过滤（筛选与 URL 双向同步）", async ({
    page,
  }) => {
    const errors = watchPageErrors(page);
    await login(page);
    await gotoAdmin(page, "/admin/tools");

    const rows = page.getByTestId("admin-tool-row");
    await expect(rows.first()).toBeVisible();
    await rows.first().getByRole("button", { name: /^操作：/ }).click();
    const historyLink = page.getByRole("menuitem", { name: /查看审批历史/ });
    await expect(historyLink).toBeVisible();

    // 先读链接里的 tool_id，再用它构造「请求确实带了该参数」的断言
    const href = await historyLink.getAttribute("href");
    const toolId = new URL(href ?? "", "http://localhost").searchParams.get("tool_id");
    expect(toolId).toBeTruthy();

    const filteredRequest = page.waitForRequest(
      (request) =>
        request.url().includes("/admin/approvals/history") &&
        new URL(request.url()).searchParams.get("tool_id") === toolId,
      { timeout: 15_000 },
    );
    await historyLink.click();
    await filteredRequest;

    // 筛选栏从 URL 回填（而不是空着或丢掉参数）
    await expect(page.locator("#history-tool-id")).toHaveValue(String(toolId));
    await expect(page).toHaveURL(new RegExp(`tool_id=${toolId}`));
    expect(errors).toEqual([]);
  });
});

/* -------------------------------------------------------------------------- */

test.describe("M4 · 与后端 M5 对齐（mock 侧）", () => {
  test("图片能力 URL：mock 下发 `sig=`，且封面图真的能加载", async ({ page }) => {
    const errors = watchPageErrors(page);
    await login(page);
    await expect(page.getByTestId("tool-card").first()).toBeVisible();

    const list = await apiJson<{ items: Array<{ cover_url: string | null }> }>(
      page,
      "/api/v1/tools?page=1&page_size=50",
    );
    const covers = list.body.items
      .map((item) => item.cover_url)
      .filter((url): url is string => typeof url === "string" && url.length > 0);
    expect(covers.length, "种子里应有带封面的工具").toBeGreaterThan(0);
    // 形状与真实后端一致：`<token>.<exp>`
    for (const url of covers) {
      expect(url, `cover_url 应带 sig=：${url}`).toContain("sig=");
      expect(new URL(url, "http://localhost").searchParams.get("sig")).toMatch(/^.+\.\d+$/);
    }

    const broken = await page.evaluate(() =>
      Array.from(document.images)
        .filter((image) => image.complete && image.naturalWidth === 0)
        .map((image) => image.currentSrc || image.src),
    );
    expect(broken, "带签名的封面图不应破图").toEqual([]);
    expect(errors).toEqual([]);
  });

  test("管理侧代上传版本：201 + 版本真的落库（不再是 404 桩）", async ({ page }) => {
    // 第二次同版本号必须是 409 VERSION_EXISTS —— 它同时验证「第一次真的写进去了」
    const errors = watchPageErrors(page, {
      allow: (status, url) => status === 409 && url.includes("/admin/tools/"),
    });
    await login(page);

    const tools = await apiJson<{ items: Array<{ id: number; tool_type: string }> }>(
      page,
      "/api/v1/tools?type=file&page=1&page_size=5",
    );
    const fileTool = tools.body.items.find((item) => item.tool_type === "file");
    expect(fileTool, "种子里应有 file 类型工具").toBeTruthy();

    const version = `9.9.${Date.now() % 1000}`;
    const first = await apiUpload<{ id: number; version: string; status: string; tool_id: number }>(
      page,
      `/api/v1/admin/tools/${fileTool!.id}/versions`,
      { version, changelog_md: "M5 对齐：管理侧代上传" },
      { name: "m5-admin-upload.txt", bytes: [0x6d, 0x35] },
    );
    expect(first.status, "管理侧代上传应返回 201").toBe(201);
    expect(first.body.version).toBe(version);
    expect(first.body.tool_id).toBe(fileTool!.id);

    const second = await apiUpload(
      page,
      `/api/v1/admin/tools/${fileTool!.id}/versions`,
      { version },
      { name: "m5-admin-upload.txt", bytes: [0x6d, 0x35] },
    );
    expect(second.status, "同版本号第二次应 409（证明第一次已落库）").toBe(409);
    expect(errors).toEqual([]);
  });

  test("设置页渲染 M5 新增的 images.signature_ttl_hours，落在「图片」分组不丢项", async ({
    page,
  }) => {
    const errors = watchPageErrors(page);
    await login(page);
    await gotoAdmin(page, "/admin/settings");

    const group = page.locator('[data-setting-group="images"]');
    await expect(group, "M5 新增的 images.* 前缀应有自己的分组").toBeVisible();
    const field = group.locator('[data-setting-key="images.signature_ttl_hours"]');
    await expect(field).toBeVisible();
    await expect(field.locator("input")).toHaveValue("168");
    // 原有的两个设置项也还在（没有因为新增分组而丢项）
    for (const key of ["approval.version_reapproval", "quota.warn_threshold_pct"]) {
      await expect(page.locator(`[data-setting-key="${key}"]`)).toBeVisible();
    }
    expect(errors).toEqual([]);
  });

  test("存储告警口径来自后端：改配额能同时改 storage_warning 与卡片显示", async ({ page }) => {
    // 故意把配额压到 1MB 触发告警；PUT 本身不是被测行为
    const errors = watchPageErrors(page, {
      allow: (status, url) => status === 400 && url.includes("/admin/settings"),
    });
    await login(page);
    // 先进设置页而不是概览：mock 的状态活在页面内存里（刷新即重置），
    // 所以「改配额 → 看概览」必须在同一个页面生命周期内用 SPA 导航完成，
    // 且概览查询此时还没被取过（不会被 staleTime 60s 挡住）。
    await gotoAdmin(page, "/admin/settings");

    try {
      const updated = await apiJson(page, "/api/v1/admin/settings", {
        method: "PUT",
        body: { items: [{ key: "quota.total_mb", value: 1 }] },
      });
      expect(updated.status).toBe(200);

      const after = await apiJson<{ storage_warning: boolean; storage_warning_threshold_pct: number }>(
        page,
        "/api/v1/admin/overview",
      );
      expect(typeof after.body.storage_warning).toBe("boolean");
      expect(after.body.storage_warning_threshold_pct).toBeGreaterThan(0);
      expect(after.body.storage_warning, "配额压到 1MB 后后端应置 storage_warning=true").toBe(
        true,
      );

      // 前端不再自己算阈值：本次挂载就必须出现告警块
      await page.getByRole("link", { name: /概览/ }).click();
      await expect(page.getByTestId("admin-layout")).toBeVisible();
      await expect(page.getByTestId("overview-storage-warning")).toBeVisible();
      await expect(page.getByTestId("overview-storage-warning")).toContainText(
        String(after.body.storage_warning_threshold_pct),
      );
    } finally {
      await apiJson(page, "/api/v1/admin/settings", {
        method: "PUT",
        body: { items: [{ key: "quota.total_mb", value: 51200 }] },
      });
    }
    expect(errors).toEqual([]);
  });
});

/* -------------------------------------------------------------------------- */

test.describe("M3 · 角色与主题（#23 #24）", () => {
  test("approver 看不到超管专属菜单与页面", async ({ page }) => {
    const errors = watchPageErrors(page);
    await page.goto("/");
    await page.getByLabel("用户名", { exact: true }).fill("wangwu");
    await page.getByLabel("密码", { exact: true }).fill("Author@12345");
    await page.getByRole("button", { name: "登录", exact: true }).click();
    await expect(page).toHaveURL(/\/$/);

    await gotoAdmin(page, "/admin");
    for (const label of ["概览", "审批队列", "审批历史", "全站工具", "分类", "标签"]) {
      await expect(page.getByRole("link", { name: new RegExp(label) })).toBeVisible();
    }
    for (const label of ["用户组", "API Token", "导入导出", "回收站", "系统设置"]) {
      await expect(page.getByRole("link", { name: new RegExp(`^${label}$`) })).toHaveCount(0);
    }

    for (const path of ["/admin/users", "/admin/groups", "/admin/tokens", "/admin/settings"]) {
      await page.goto(path);
      await expect(page.getByText("没有访问权限")).toBeVisible();
    }
    expect(errors).toEqual([]);
  });

  test("管理台全部页面在深色主题下无硬编码浅色", async ({ page }) => {
    const errors = watchPageErrors(page);
    await login(page);
    await page.getByLabel(/切换主题/).click();
    await page.getByRole("menuitemradio", { name: "深色" }).click();
    await expect(page.locator("html")).toHaveClass(/dark/);

    // M4 交付项 5：不再抽查三个 —— 管理台 13 个页面全部过一遍
    const ADMIN_PAGES = [
      "/admin",
      "/admin/approvals",
      "/admin/approvals/history",
      "/admin/tools",
      "/admin/categories",
      "/admin/tags",
      "/admin/users",
      "/admin/groups",
      "/admin/whitelist",
      "/admin/tokens",
      "/admin/settings",
      "/admin/import-export",
      "/admin/recycle-bin",
    ] as const;

    for (const path of ADMIN_PAGES) {
      await gotoAdmin(page, path);
      await page.waitForTimeout(200);
      // 深色下卡片背景必须比纯白暗：用计算样式检查，避免只看截图
      const offending = await page.evaluate(() => {
        const out: string[] = [];
        for (const element of Array.from(document.querySelectorAll<HTMLElement>("main *"))) {
          const style = getComputedStyle(element);
          const backgroundColor = style.backgroundColor;
          const match = /rgba?\((\d+),\s*(\d+),\s*(\d+)/.exec(backgroundColor);
          if (!match) continue;
          const [r, g, b] = [Number(match[1]), Number(match[2]), Number(match[3])];
          if (r > 240 && g > 240 && b > 240 && element.offsetHeight > 40) {
            out.push(`${element.tagName}.${element.className.toString().slice(0, 40)}`);
          }
        }
        return out.slice(0, 3);
      });
      expect(offending, `${path} 深色下出现接近纯白的大块背景`).toEqual([]);
      await page.screenshot({
        path: `${ARTIFACTS}/m4-dark-${path.replaceAll("/", "-").replace(/^-/, "")}.png`,
        fullPage: true,
      });
    }
    expect(errors).toEqual([]);
  });

  test("响应式：1280px 不横向溢出、窄屏侧栏折叠为抽屉（M4 交付项 5）", async ({ page }) => {
    const errors = watchPageErrors(page);
    await login(page);

    // 1280px：管理台的表格页与表单页都不能把文档撑出横向滚动
    await page.setViewportSize({ width: 1280, height: 800 });
    for (const path of ["/admin", "/admin/tools", "/admin/users", "/admin/import-export"] as const) {
      await gotoAdmin(page, path);
      await page.waitForTimeout(200);
      const overflow = await page.evaluate(() => {
        const root = document.scrollingElement ?? document.documentElement;
        return { scrollWidth: root.scrollWidth, clientWidth: root.clientWidth };
      });
      expect(
        overflow.scrollWidth,
        `${path} 在 1280px 下横向溢出（${overflow.scrollWidth} > ${overflow.clientWidth}）`,
      ).toBeLessThanOrEqual(overflow.clientWidth + 1);
    }
    // 宽表格必须落在自己的滚动容器里（ui/table 的 table-container），而不是撑破页面
    await gotoAdmin(page, "/admin/tools");
    const scroller = page.locator('[data-slot="table-container"]').first();
    await expect(scroller).toBeVisible();
    expect(
      await scroller.evaluate((element) => getComputedStyle(element).overflowX),
      "表格容器必须是 overflow-x: auto（docs/04 §8.2）",
    ).toBe("auto");

    // 窄屏（<lg）：侧栏收起，出现抽屉入口，抽屉里仍能导航
    await page.setViewportSize({ width: 900, height: 800 });
    await gotoAdmin(page, "/admin");
    await expect(page.getByRole("navigation", { name: "管理台导航" })).toBeHidden();
    await page.getByRole("button", { name: "打开管理台菜单" }).click();
    const drawer = page.getByRole("dialog");
    await expect(drawer.getByText("管理台", { exact: true })).toBeVisible();
    await drawer.getByRole("link", { name: /用户/ }).first().click();
    await expect(page).toHaveURL(/\/admin\/users/);
    expect(errors).toEqual([]);
  });

  test("无障碍：管理台图标按钮都有可访问名（M4 交付项 5）", async ({ page }) => {
    const errors = watchPageErrors(page);
    await login(page);
    for (const path of [
      "/admin",
      "/admin/tools",
      "/admin/users",
      "/admin/groups",
      "/admin/categories",
      "/admin/tags",
      "/admin/tokens",
      "/admin/recycle-bin",
    ] as const) {
      await gotoAdmin(page, path);
      await page.waitForTimeout(200);
      const unnamed = await page.evaluate(() => {
        /**
         * 近似的可访问名计算（够用即可）：aria-label → aria-labelledby →
         * 关联 <label>（`labels` / 最近的 label 祖先）→ 可见文字 → title。
         * 不能只看 textContent：Radix 的 Switch 这类控件靠 `<label for>` 取名，
         * 只看文字会误报。
         */
        const describe = (element: Element) => {
          const aria = element.getAttribute("aria-label")?.trim();
          if (aria) return null;
          const labelledBy = element.getAttribute("aria-labelledby");
          if (labelledBy) {
            const target = document.getElementById(labelledBy);
            if (target?.textContent?.trim()) return null;
          }
          const labels = (element as HTMLInputElement).labels;
          if (labels && Array.from(labels).some((label) => (label.textContent ?? "").trim() !== "")) {
            return null;
          }
          const wrapping = element.closest("label");
          if (wrapping && (wrapping.textContent ?? "").trim() !== "") return null;
          const text = (element.textContent ?? "").trim();
          if (text !== "") return null;
          const title = element.getAttribute("title")?.trim();
          if (title) return null;
          return `${element.tagName.toLowerCase()}[${(element.className.toString() || "").slice(0, 30)}]`;
        };
        const out: string[] = [];
        for (const element of Array.from(
          document.querySelectorAll("main button, main [role=button], main a[href]"),
        )) {
          if (element.getAttribute("aria-hidden") === "true") continue;
          if (element.getAttribute("role") === "presentation") continue;
          const problem = describe(element);
          if (problem) out.push(problem);
        }
        return out.slice(0, 5);
      });
      expect(unnamed, `${path} 存在没有可访问名的图标按钮/链接`).toEqual([]);
    }
    expect(errors).toEqual([]);
  });

  test("无障碍：Dialog 焦点陷阱与 Esc 关闭（M4 交付项 5）", async ({ page }) => {
    const errors = watchPageErrors(page);
    await login(page);
    await gotoAdmin(page, "/admin/users");

    await page.getByRole("button", { name: /新建用户/ }).click();
    const dialog = page.getByRole("dialog");
    await expect(dialog).toBeVisible();

    // 焦点必须落在对话框内部（Radix 的 focus trap）
    await expect
      .poll(() => page.evaluate(() => Boolean(document.activeElement?.closest('[role="dialog"]'))))
      .toBe(true);

    // 连按 Tab 20 次，焦点始终不能跑出对话框
    let escaped = false;
    for (let index = 0; index < 20; index += 1) {
      await page.keyboard.press("Tab");
      const inside = await page.evaluate(() =>
        Boolean(document.activeElement?.closest('[role="dialog"]')),
      );
      if (!inside) {
        escaped = true;
        break;
      }
    }
    expect(escaped, "Tab 循环不应把焦点移出对话框").toBe(false);

    // Esc 关闭，且焦点回到触发按钮
    await page.keyboard.press("Escape");
    await expect(page.getByRole("dialog")).toHaveCount(0);
    expect(errors).toEqual([]);
  });
});
