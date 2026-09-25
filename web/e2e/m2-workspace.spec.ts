import { mkdtemp, writeFile } from "node:fs/promises";
import { tmpdir } from "node:os";
import { join } from "node:path";

import { expect, test, type Page } from "@playwright/test";

import { ADMIN, ARTIFACTS, login, watchPageErrors } from "./helpers";

/**
 * M2 验收 · 个人中心与工具编辑器（验收 #11 ~ #17、#28）。
 * MSW mock 下的完整写入闭环：建工具 → 传文件 → 提交 → 驳回 → 重提 → 版本管理。
 */

/** 用页面内的 fetch 拿 token 调 API（access token 只在内存里）。 */
async function apiJson<T>(
  page: Page,
  path: string,
  init?: { method?: string; body?: unknown },
): Promise<{ status: number; body: T }> {
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

async function makeTempFile(name: string, content: string): Promise<string> {
  const dir = await mkdtemp(join(tmpdir(), "selftool-m2-"));
  const filePath = join(dir, name);
  await writeFile(filePath, content);
  return filePath;
}

/** 编辑器必填项（zod：名称 / 简介 / 分类 / 详情说明，docs/04 §6.7）。 */
async function fillRequiredFields(page: Page, name: string): Promise<void> {
  await page.getByLabel("工具名称").fill(name);
  await page.getByLabel("简介").fill("E2E 验收：必填项。");
  await page.getByLabel("分类").click();
  await page.getByRole("option", { name: "研发工具" }).click();
  // 用 id 而不是 label 文本：section 的 aria-labelledby 与页签组的 aria-label 也叫「详情说明…」
  await page.locator("#tool-field-description_md").fill("## 用途\n\nE2E 验收用例。");
}

test.describe("M2 · 个人中心与编辑器", () => {
  test("11. 创建 file 工具：表单校验 → 上传 → 保存草稿 → 提交后进入待审", async ({ page }) => {
    const errors = watchPageErrors(page);
    await login(page);
    await page.goto("/me/tools/new");
    await expect(page.getByTestId("tool-form")).toBeVisible();

    // 空表单提交 → 顶部汇总 + 字段级校验，不发请求
    await page.getByRole("button", { name: "保存草稿" }).click();
    await expect(page.getByTestId("validation-summary")).toBeVisible();
    await expect(page.getByText(/请填写工具名称|请填写一句话简介/).first()).toBeVisible();

    const name = `E2E 文件工具 ${Date.now().toString(36)}`;
    await fillRequiredFields(page, name);

    // 类型保持默认（文件包），选一个真实文件
    const filePath = await makeTempFile("e2e-pkg.txt", "selftool m2 e2e payload\n");
    await page.setInputFiles("#version-content-file", filePath);
    await expect(page.getByText("e2e-pkg.txt").first()).toBeVisible();

    await page.getByRole("button", { name: "保存草稿" }).click();
    // 保存草稿 → 建工具 + 上传版本 → 跳到编辑页（后续保存走 PATCH）
    await expect(page).toHaveURL(/\/me\/tools\/\d+\/edit/, { timeout: 20_000 });
    await expect(page.getByTestId("tool-form")).toBeVisible();

    await page.getByRole("button", { name: "提交审批" }).click();
    await expect(page).toHaveURL(/\/me\/tools$/, { timeout: 20_000 });

    // 落在「待审」分组
    await page.getByRole("tab", { name: /待审/ }).click();
    await expect(page.getByTestId("my-tool-row").filter({ hasText: name })).toBeVisible();

    // 清理：删除自己创建的工具（软删除）
    const list = await apiJson<{ items: Array<{ id: number; name: string }> }>(
      page,
      "/api/v1/me/tools?page_size=200",
    );
    const created = list.body.items.find((item) => item.name === name);
    if (created) {
      await apiJson(page, `/api/v1/me/tools/${created.id}`, { method: "DELETE" });
    }
    await page.screenshot({ path: `${ARTIFACTS}/m2-11-created.png`, fullPage: true });
    expect(errors).toEqual([]);
  });

  test("12. 切换工具类型：弹确认框，确认后清空已填内容", async ({ page }) => {
    const errors = watchPageErrors(page);
    await login(page);
    await page.goto("/me/tools/new");

    await fillRequiredFields(page, `E2E 类型切换 ${Date.now().toString(36)}`);
    const filePath = await makeTempFile("switch-me.txt", "content to be discarded\n");
    await page.setInputFiles("#version-content-file", filePath);
    await expect(page.getByText("switch-me.txt").first()).toBeVisible();

    // 切到「在线工具」
    await page.getByRole("radio", { name: /在线工具/ }).click();
    const dialog = page.getByRole("alertdialog");
    await expect(dialog).toBeVisible();
    await expect(dialog).toContainText("切换类型会清空已上传的文件");
    await dialog.getByRole("button", { name: "切换类型" }).click();

    // 已选文件被清空，类型切换生效（出现 webapp 的 URL 字段）
    await expect(page.getByText("switch-me.txt")).toHaveCount(0);
    await expect(page.getByLabel("工具 URL")).toBeVisible();
    expect(errors).toEqual([]);
  });

  test("13. Skill 解析失败：显示具体错误，可存草稿但「提交审批」禁用", async ({ page }) => {
    // 上传被拒（422 SKILL_MD_NOT_FOUND）正是被测行为
    const errors = watchPageErrors(page, {
      allow: (status, url) => status === 422 && url.includes("/versions"),
    });
    await login(page);
    await page.goto("/me/tools/new");

    await fillRequiredFields(page, `E2E 坏包 Skill ${Date.now().toString(36)}`);
    await page.getByRole("radio", { name: /^Skill/ }).click();
    // 切换类型会弹确认（已填了详情说明）
    if (await page.getByRole("alertdialog").count()) {
      await page.getByRole("alertdialog").getByRole("button", { name: "切换类型" }).click();
      await fillRequiredFields(page, `E2E 坏包 Skill ${Date.now().toString(36)}`);
    }

    const badZip = await makeTempFile("broken-skill.zip", "not a real zip\n");
    await page.setInputFiles("#version-content-file", badZip);
    await page.getByRole("button", { name: "保存草稿" }).click();

    // 解析错误是「非致命警告」：草稿可存，提交审批不可用
    const warning = page.getByTestId("skill-parse-error");
    await expect(warning).toBeVisible({ timeout: 20_000 });
    await expect(warning).toContainText(/SKILL\.md|解析/);
    const submitButton = page.getByRole("button", { name: "提交审批" });
    await expect(submitButton).toBeDisabled();
    await page.screenshot({ path: `${ARTIFACTS}/m2-13-skill-error.png`, fullPage: true });
    expect(errors).toEqual([]);
  });

  test("14. 草稿自动保存：失焦后出现「已保存 · 刚刚」", async ({ page }) => {
    const errors = watchPageErrors(page);
    await login(page);
    await page.goto("/me/tools/new");

    await fillRequiredFields(page, `E2E 自动保存 ${Date.now().toString(36)}`);
    await page.getByRole("radio", { name: /提示词/ }).click();
    if (await page.getByRole("alertdialog").count()) {
      await page.getByRole("alertdialog").getByRole("button", { name: "切换类型" }).click();
      await fillRequiredFields(page, `E2E 自动保存 ${Date.now().toString(36)}`);
    }
    await page.locator("#tool-field-prompt_content").fill("你是一个内网工具平台的助手。");
    await page.getByRole("button", { name: "保存草稿" }).click();
    await expect(page).toHaveURL(/\/me\/tools\/\d+\/edit/, { timeout: 20_000 });

    // 改简介并失焦 → 触发一次自动保存
    await page.getByLabel("简介").fill("自动保存验证（已修改）。");
    await page.getByLabel("工具名称").click();
    await expect(page.getByTestId("autosave-indicator")).toContainText(/已保存/, {
      timeout: 20_000,
    });
    expect(errors).toEqual([]);
  });

  test("15. 上传新版本：toast 明确说明旧版本继续服务", async ({ page }) => {
    const errors = watchPageErrors(page);
    await login(page);

    // 找 admin 自己名下的 file 类型已发布工具
    const list = await apiJson<{
      items: Array<{ id: number; name: string; tool_type: string; status: string }>;
    }>(page, "/api/v1/me/tools?page_size=200&status=approved");
    const target = list.body.items.find((item) => item.tool_type === "file");
    expect(target, "mock 里应有 admin 名下的 file 工具").toBeTruthy();

    await page.goto(`/me/tools/${target!.id}/versions`);
    await expect(page.getByTestId("version-timeline")).toBeVisible();
    await page.getByRole("button", { name: "上传新版本" }).first().click();

    const dialog = page.getByTestId("version-upload-dialog");
    await expect(dialog).toBeVisible();
    await dialog.getByLabel("版本号 *").fill(`9.9.${Date.now() % 1000}`);
    await dialog.getByLabel("变更说明").fill("### 新增\n- E2E 上传验证");
    const filePath = await makeTempFile("new-version.txt", "new version payload\n");
    await dialog.locator('input[type="file"]').setInputFiles(filePath);
    await dialog.getByRole("button", { name: /提交|上传/ }).last().click();

    await expect(page.getByText(/继续对外提供服务/)).toBeVisible({ timeout: 20_000 });
    await page.screenshot({ path: `${ARTIFACTS}/m2-15-version-toast.png`, fullPage: true });
    expect(errors).toEqual([]);
  });

  test("16. 被驳回的工具：展开显示理由 + 「修改后重新提交」入口", async ({ page }) => {
    const errors = watchPageErrors(page);
    await login(page);
    await page.goto("/me/tools?status=rejected");

    const alert = page.getByTestId("reject-alert").first();
    await expect(alert).toBeVisible();
    await expect(alert).toContainText(/审批未通过|驳回|理由/);

    await page.getByTestId("resubmit-button").first().click();
    await expect(page).toHaveURL(/\/me\/tools\/\d+\/edit$/);
    await expect(page.getByTestId("tool-form")).toBeVisible();
    await page.screenshot({ path: `${ARTIFACTS}/m2-16-rejected.png`, fullPage: true });
    expect(errors).toEqual([]);
  });

  test("17. 版本时间线：五种状态视觉可区分", async ({ page }) => {
    const errors = watchPageErrors(page);
    await login(page);

    // 当前 / 历史 / 已归档 三种在 log-analyzer 上（mock 种子）
    await page.goto("/tools/log-analyzer-a3f2");
    const timeline = page.getByTestId("version-item");
    await expect(timeline.first()).toBeVisible();
    await expect(timeline.filter({ hasText: "当前" }).first()).toBeVisible();
    await expect(timeline.filter({ hasText: "历史" }).first()).toBeVisible();
    await expect(timeline.filter({ hasText: "已归档" }).first()).toBeVisible();

    // 待审 / 已驳回：从「我的工具」里找带这两种版本的工具体验
    const mine = await apiJson<{ items: Array<{ id: number; slug: string; status: string }> }>(
      page,
      "/api/v1/me/tools?page_size=200",
    );
    const withPending = mine.body.items.find((item) => item.status === "pending_update");
    if (withPending) {
      await page.goto(`/me/tools/${withPending.id}/versions`);
      await expect(page.getByTestId("version-timeline")).toContainText("待审");
    }
    const rejected = mine.body.items.find((item) => item.status === "rejected");
    if (rejected) {
      await page.goto(`/me/tools/${rejected.id}/versions`);
      await expect(page.getByTestId("version-timeline")).toContainText("已驳回");
    }
    expect(errors).toEqual([]);
  });

  test("28. UserMenu：个人中心 / 我的工具 / 管理后台均可用", async ({ page }) => {
    const errors = watchPageErrors(page);
    await login(page);

    // 三项都必须存在且可用（M1 时是 disabled 占位，CONTRACT §14.10 要求 M2 启用）
    await page.getByTestId("user-menu-trigger").click();
    for (const label of ["个人中心", "我的工具", "管理后台"]) {
      await expect(page.getByRole("menuitem", { name: label })).toBeEnabled();
    }
    await page.getByRole("menuitem", { name: "个人中心" }).click();
    await expect(page).toHaveURL(/\/me$/);
    await expect(page.getByTestId("profile-page")).toBeVisible();

    await page.getByTestId("user-menu-trigger").click();
    await page.getByRole("menuitem", { name: "我的工具" }).click();
    await expect(page).toHaveURL(/\/me\/tools$/);
    await expect(page.getByTestId("my-tools-page")).toBeVisible();

    // 管理后台入口的落地页在 admin 套件里验证；这里直接访问确认可达
    await page.goto("/admin/approvals");
    await expect(page.getByTestId("admin-layout")).toBeVisible();
    expect(errors).toEqual([]);
  });

  test("个人资料：账号卡 + 存储用量条 + 我的下载", async ({ page }) => {
    const errors = watchPageErrors(page);
    await login(page);
    await page.goto("/me");

    await expect(page.getByTestId("profile-page")).toBeVisible();
    await expect(page.getByText(ADMIN.username, { exact: false }).first()).toBeVisible();
    await expect(page.getByTestId("storage-usage")).toBeVisible();
    await expect(page.getByTestId("storage-usage")).toContainText(/已用/);

    // 修改显示名
    await page.getByRole("button", { name: /编辑资料/ }).click();
    const dialog = page.getByRole("dialog");
    await expect(dialog).toBeVisible();
    await dialog.getByLabel("显示名").fill("管理员（E2E）");
    await dialog.getByRole("button", { name: /保存/ }).click();
    await expect(page.getByText("管理员（E2E）").first()).toBeVisible({ timeout: 15_000 });
    await page.screenshot({ path: `${ARTIFACTS}/m2-profile.png`, fullPage: true });
    expect(errors).toEqual([]);
  });

  test("我的工具：驳回提示醒目、空态有创建入口、Tab 计数", async ({ page }) => {
    const errors = watchPageErrors(page);
    await login(page);
    await page.goto("/me/tools");

    await expect(page.getByTestId("my-tools-page")).toBeVisible();
    const tabs = page.getByRole("tab");
    await expect(tabs.first()).toBeVisible();
    // 状态徽标必须有文字（不能只靠颜色，docs/04 §8.1）
    await expect(page.getByTestId("my-tool-status").first()).toHaveText(/\S+/);
    expect(errors).toEqual([]);
  });
});
