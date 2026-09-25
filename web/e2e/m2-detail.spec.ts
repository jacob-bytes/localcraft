import { expect, test } from "@playwright/test";

import {
  ARTIFACTS,
  AUTHOR,
  VIEWER,
  captureClipboard,
  copiedValues,
  login,
  openTool,
  trackRequests,
  watchPageErrors,
} from "./helpers";

/**
 * M2 验收 · 工具详情与下载（验收 #1 ~ #10）。
 * 运行在 MSW mock 下；真实后端路径由 `e2e-real/` 覆盖。
 */

const FILE_SLUG = "log-analyzer-a3f2";

test.describe("M2 · 详情页与下载", () => {
  test("1. 门户点卡片进详情（不再是 404）", async ({ page }) => {
    const errors = watchPageErrors(page);
    await login(page);
    await expect(page.getByTestId("tool-card").first()).toBeVisible();

    await page.getByTestId("tool-card").first().click();
    await expect(page).toHaveURL(/\/tools\//);
    await expect(page.getByTestId("tool-detail")).toBeVisible();
    await expect(page.getByTestId("tool-breadcrumb")).toBeVisible();
    await expect(page.getByRole("heading", { level: 1 })).toBeVisible();
    await page.screenshot({ path: `${ARTIFACTS}/m2-01-detail.png`, fullPage: true });
    expect(errors).toEqual([]);
  });

  test("2+3. file 详情：文件信息卡 + SHA256 可复制 + 下载走票据", async ({ page }) => {
    const errors = watchPageErrors(page);
    await captureClipboard(page);
    await login(page);
    await openTool(page, FILE_SLUG);

    // 文件信息卡：文件名 / 大小 / 扩展名 / 完整 SHA256
    const sha = page.getByTestId("sha256-value").first();
    await expect(sha).toBeVisible();
    const shaText = (await sha.innerText()).trim();
    expect(shaText).toMatch(/^[0-9a-f]{16,64}$/i);
    await expect(page.getByText("文件包").first()).toBeVisible();
    await expect(page.getByText(/MB|KB|B$/).first()).toBeVisible();

    // 复制 SHA256
    await sha.locator("xpath=ancestor::*[1]").getByRole("button").first().click();
    expect(await copiedValues(page)).toContain(shaText);

    // 下载：必须走 download-ticket，然后是 <a> 触发
    const ticketCalls = trackRequests(page, "/download-ticket");
    const downloadPromise = page.waitForEvent("download");
    await page.getByTestId("download-button").first().click();
    const download = await downloadPromise;
    expect(download.suggestedFilename()).toBeTruthy();
    expect(ticketCalls.count()).toBeGreaterThan(0);
    expect(errors).toEqual([]);
  });

  test("4. 历史版本下载带 ?version_id=", async ({ page }) => {
    const errors = watchPageErrors(page);
    await login(page);
    await openTool(page, FILE_SLUG);

    const items = page.getByTestId("version-item");
    await expect(items.first()).toBeVisible();
    expect(await items.count()).toBeGreaterThan(1);

    // 第二个版本（mock 里是 superseded 历史版本）
    const ticketUrls: string[] = [];
    page.on("request", (request) => {
      if (request.url().includes("/download-ticket")) ticketUrls.push(request.url());
    });

    const second = items.nth(1);
    await second.getByRole("button", { name: /下载/ }).click();
    await expect.poll(() => ticketUrls.length).toBeGreaterThan(0);
    expect(ticketUrls.some((url) => /version_id=\d+/.test(url))).toBe(true);
    expect(errors).toEqual([]);
  });

  test("5. webapp 详情：打开工具新标签页 + URL 可复制", async ({ page }) => {
    const errors = watchPageErrors(page);
    await login(page);
    await page.goto("/?type=webapp");
    const card = page.getByTestId("tool-card").first();
    await expect(card).toBeVisible();
    await card.click();
    await expect(page.getByTestId("tool-detail")).toBeVisible();

    const openLink = page.getByRole("link", { name: /打开工具/ });
    await expect(openLink).toBeVisible();
    const href = await openLink.getAttribute("href");
    expect(href).toMatch(/^https?:\/\//);
    expect(await openLink.getAttribute("target")).toBe("_blank");
    expect(await openLink.getAttribute("rel")).toContain("noopener");
    await expect(page.getByText(href!)).toBeVisible();
    expect(errors).toEqual([]);
  });

  test("6. skill 详情：三 Tab + 切 Tab 才发 skill-preview 请求", async ({ page }) => {
    const errors = watchPageErrors(page);
    await login(page);
    await page.goto("/?type=skill");
    await page.getByTestId("tool-card").first().click();
    await expect(page.getByTestId("skill-tabs")).toBeVisible();

    // 首屏不得请求 skill-preview（大 JSON 按需拉取，docs/04 §6.4）
    const previewCalls = trackRequests(page, "/skill-preview");
    await page.waitForTimeout(600);
    expect(previewCalls.count(), "首屏不应请求 skill-preview").toBe(0);

    // 三个页签都在
    await expect(page.getByTestId("skill-tab-readme")).toBeVisible();
    await expect(page.getByTestId("skill-tab-files")).toBeVisible();
    await expect(page.getByTestId("skill-tab-meta")).toBeVisible();

    // 切到「包内文件」才发请求
    await page.getByTestId("skill-tab-files").click();
    await expect(page.getByTestId("skill-tree-panel")).toBeVisible();
    await expect.poll(() => previewCalls.count()).toBeGreaterThan(0);
    await expect(page.getByTestId("skill-file-tree")).toContainText("SKILL.md");

    // SKILL.md 页签渲染 Markdown
    await page.getByTestId("skill-tab-readme").click();
    await expect(page.getByTestId("skill-md-panel")).toBeVisible();
    await page.screenshot({ path: `${ARTIFACTS}/m2-06-skill.png`, fullPage: true });
    expect(errors).toEqual([]);
  });

  test("7. prompt 详情：一键复制内容与原文完全一致", async ({ page }) => {
    const errors = watchPageErrors(page);
    await captureClipboard(page);
    await login(page);
    await page.goto("/?type=prompt");
    await page.getByTestId("tool-card").first().click();
    await expect(page.getByTestId("tool-detail")).toBeVisible();

    const content = (await page.getByTestId("prompt-content").innerText()).trim();
    expect(content.length).toBeGreaterThan(10);
    await page.getByTestId("prompt-copy").click();
    const copied = await copiedValues(page);
    expect(copied.length).toBeGreaterThan(0);
    expect(copied[0]?.trim()).toBe(content);
    // 字符数统计
    await expect(page.getByText(/\d+\s*字/).first()).toBeVisible();
    expect(errors).toEqual([]);
  });

  test("8. purged 版本：显示「已归档」且下载禁用", async ({ page }) => {
    const errors = watchPageErrors(page);
    await login(page);
    await openTool(page, FILE_SLUG);

    const archived = page.getByTestId("version-item").filter({ hasText: "已归档" });
    await expect(archived.first()).toBeVisible();
    const downloadButton = archived.first().getByRole("button", { name: /下载/ });
    await expect(downloadButton).toBeDisabled();
    expect(errors).toEqual([]);
  });

  test("9. viewer 角色：可看详情但 can_download=false 且有说明", async ({ page }) => {
    const errors = watchPageErrors(page);
    await login(page, VIEWER);
    await openTool(page, FILE_SLUG);

    const downloadButton = page.getByTestId("download-button").first();
    await expect(downloadButton).toBeDisabled();
    // 禁用原因必须可读（Tooltip），不能只靠置灰。disabled 的按钮不接收指针事件，
    // 所以 hover 落在包住它的 tooltip trigger 上。
    const tooltipTrigger = page
      .locator('[data-slot="tooltip-trigger"][aria-label*="无下载权限"]')
      .first();
    await expect(tooltipTrigger).toBeAttached();
    await tooltipTrigger.hover();
    await expect(page.getByText(/当前角色无下载权限/).first()).toBeVisible();
    await page.screenshot({ path: `${ARTIFACTS}/m2-09-viewer.png`, fullPage: true });
    expect(errors).toEqual([]);
  });

  test("10. 无权访问的 private 工具：渲染 404 页（不是 403、不崩溃）", async ({ page, context }) => {
    // admin 建一个 private 工具，再用普通用户访问
    const errors = watchPageErrors(page);
    await login(page);
    const created = await page.evaluate(async () => {
      const client = (await import(/* @vite-ignore */ "/src/api/" + "client.ts")) as {
        getAccessToken: () => string | null;
      };
      const token = client.getAccessToken();
      const response = await fetch("/api/v1/me/tools", {
        method: "POST",
        headers: { "Content-Type": "application/json", Authorization: `Bearer ${token ?? ""}` },
        body: JSON.stringify({
          name: `E2E 私有工具 ${Date.now()}`,
          summary: "私有可见性验证",
          description_md: "private",
          tool_type: "prompt",
          visibility: "private",
        }),
      });
      return { status: response.status, body: (await response.json()) as { slug?: string } };
    });
    expect(created.status).toBe(201);
    const slug = created.body.slug!;

    const other = await context.browser()?.newContext({ locale: "zh-CN" });
    const otherPage = await other!.newPage();
    // 这个 404 就是被测行为本身（无权访问必须 404），不视为缺陷
    const otherErrors = watchPageErrors(otherPage, {
      allow: (status, url) => status === 404 && url.includes("/api/v1/tools/"),
    });
    await login(otherPage, AUTHOR);
    await otherPage.goto(`/tools/${slug}`);
    await expect(otherPage.getByText("页面不存在或你没有访问权限")).toBeVisible();
    await other!.close();

    expect(errors).toEqual([]);
    expect(otherErrors).toEqual([]);
  });

  test("详情页展示状态理由（offline / rejected 对 owner 可见）", async ({ page }) => {
    const errors = watchPageErrors(page);
    await login(page);

    // 从「我的工具」拿一个 offline 工具的 slug，再打开它的详情页
    const offlineSlug = await page.evaluate(async () => {
      const client = (await import(/* @vite-ignore */ "/src/api/" + "client.ts")) as {
        getAccessToken: () => string | null;
      };
      const token = client.getAccessToken();
      const response = await fetch("/api/v1/me/tools?status=offline", {
        headers: { Authorization: `Bearer ${token ?? ""}` },
      });
      const body = (await response.json()) as { items: Array<{ slug: string }> };
      return body.items[0]?.slug ?? null;
    });
    expect(offlineSlug).toBeTruthy();

    await page.goto(`/tools/${offlineSlug}`);
    await expect(page.getByTestId("tool-detail")).toBeVisible();
    await expect(page.getByTestId("offline-reason")).toBeVisible();
    expect(errors).toEqual([]);
  });
});
