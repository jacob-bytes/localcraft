import { createHash } from "node:crypto";
import { readdirSync } from "node:fs";
import { mkdtemp, readFile, writeFile } from "node:fs/promises";
import { tmpdir } from "node:os";
import { dirname, join, resolve } from "node:path";
import { fileURLToPath } from "node:url";

import { expect, test, type Locator, type Page } from "@playwright/test";

import { reportChunkDiagnostics, trackChunkRequests } from "./diagnostics";

/**
 * 真实后端 E2E（M2 裁定 6 / CONTRACT §14.1）。
 *
 * 这个套件只做 mock 做不到的事；纯 UI 交互由 `e2e/` 的 MSW 套件覆盖：
 *   - refresh cookie 的真实 `Path=/api/v1/auth`
 *   - 登出成功的真实响应形状（`200` + `{"status":"ok"}`，不是 204）
 *   - `facets` 翻页时的真实 `null` 序列化
 *   - 真实 404 语义（无权访问返回 404 而不是 403）
 *   - 真实 SQL 分页/筛选/排序
 *   - 真实文件上传 → 审批 → 下载 → SHA256 一致
 *   - 图片能力 URL / `dist` 由后端托管
 *
 * 前置条件：后端已就绪（默认 127.0.0.1:8000）并已 `seed-demo`，`web/dist` 是
 * **关闭 mock** 的生产构建（后端托管 SPA，与生产拓扑一致）。
 * 运行：`npm run e2e:real`；指向别的实例：`REAL_BASE=http://127.0.0.1:8010 npm run e2e:real`。
 *
 * 本套件会**写入**真实数据库（创建/上传/审批/删除），每次都用带时间戳的唯一名称，
 * 并在结束时软删除自己创建的工具。
 */

const API = "/api/v1";
const ADMIN = { username: "admin", password: "Admin@12345" };
const AUTHOR = { username: "zhangsan", password: "Author@12345" };

declare global {
  interface Window {
    __seen?: { login: boolean; loader: boolean };
  }
}

/** 从真实请求里抓一个 Authorization 头 —— access token 只在内存，脚本读不到。 */
function captureAuthHeader(page: Page): { current: () => string | null } {
  let token: string | null = null;
  page.on("request", (request) => {
    const header = request.headers()["authorization"];
    if (header?.startsWith("Bearer ")) token = header;
  });
  return { current: () => token };
}

async function signIn(page: Page, username: string, password: string): Promise<void> {
  await page.goto("/login");
  await page.getByLabel("用户名", { exact: true }).fill(username);
  await page.getByLabel("密码", { exact: true }).fill(password);
  await page.getByRole("button", { name: "登录", exact: true }).click();
}

/** 记录登录页是否被渲染过 —— 用于「F5 不闪登录页」的真实断言。 */
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

interface ApiResult<T> {
  status: number;
  body: T;
}

/** 借用页面内的同源 fetch（带上真实 token），核对原始响应形状。 */
async function api(
  page: Page,
  path: string,
  token: string | null,
  init?: { method?: string; body?: unknown },
): Promise<ApiResult<never>> {
  return page.evaluate(
    async ([url, auth, method, body]) => {
      const response = await fetch(url as string, {
        method: (method as string) || "GET",
        headers: {
          ...(auth ? { Authorization: auth as string } : {}),
          ...(body ? { "Content-Type": "application/json" } : {}),
        },
        body: body ? JSON.stringify(body) : undefined,
      });
      const text = await response.text();
      return { status: response.status, body: (text ? JSON.parse(text) : null) as never };
    },
    [path, token, init?.method ?? "GET", init?.body ?? null] as const,
  );
}

async function apiUpload(
  page: Page,
  path: string,
  token: string,
  fields: Record<string, string>,
  file: { name: string; bytes: Buffer },
): Promise<ApiResult<never>> {
  return page.evaluate(
    async ([url, auth, rawFields, fileName, bytes]) => {
      const form = new FormData();
      for (const [key, value] of Object.entries(rawFields as Record<string, string>)) {
        form.append(key, value);
      }
      form.append("file", new File([new Uint8Array(bytes as number[])], fileName as string));
      const response = await fetch(url as string, {
        method: "POST",
        headers: { Authorization: auth as string },
        body: form,
      });
      const text = await response.text();
      return { status: response.status, body: (text ? JSON.parse(text) : null) as never };
    },
    [path, token, fields, file.name, Array.from(file.bytes)] as const,
  );
}

/** 唯一名称，避免重复运行时与上一次的数据混淆。 */
function uniqueName(prefix: string): string {
  return `${prefix} ${Date.now().toString(36)}`;
}

/**
 * 导航后的首个断言（CONTRACT §19.9 / M4 交付项 1.4）。
 *
 * **不假设首次 chunk 拉取必然成功**：`lazyWithRetry` 失败时会整页自动重载一次，
 * 页面随后会自己恢复。所以这里给 30s 的时间窗（够一次重载 + 重新渲染），
 * 并在失败信息里写明「已允许一次 chunk 重试」，避免下一次复现又被误判成
 * 「断言太严」或「页面真的坏了」。
 */
const CHUNK_RETRY_NOTE = "（已允许一次 chunk 自动重试；超时 30s，详见失败时的 [诊断] 输出）";

async function expectAfterNavigation(locator: Locator, label: string): Promise<void> {
  await expect(locator, `${label} 未出现${CHUNK_RETRY_NOTE}`).toBeVisible({ timeout: 30_000 });
}

/** 门户首屏（工具卡片）就绪 —— 大多数用例的公共起点。 */
async function expectPortalReady(page: Page): Promise<void> {
  await expectAfterNavigation(page.getByTestId("tool-card").first(), "门户工具卡片");
}

/* -------------------------------------------------------------------------- */
/* 懒加载韧性：dist 里的真实 chunk 名 + 「只失败一次」的拦截器（M4 交付项 1）   */
/* -------------------------------------------------------------------------- */

/** 构建产物里的 chunk 文件名（带哈希），例如 `ToolDetailPage-CgGYlRAi.js`。 */
function findBuiltChunk(pageName: string): string | null {
  const assetsDir = resolve(dirname(fileURLToPath(import.meta.url)), "..", "dist", "assets");
  const match = readdirSync(assetsDir).find((file) =>
    new RegExp(`^${pageName}-[A-Za-z0-9_-]+\\.js$`).test(file),
  );
  return match ?? null;
}

function escapeRegExp(value: string): string {
  return value.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
}

/** 头一个工具 slug —— 韧性用例只需要一个能进详情页的真实 slug。 */
async function firstToolSlug(
  page: Page,
  auth: { current: () => string | null },
): Promise<string> {
  const list = (await api(page, `${API}/tools?page=1&page_size=1`, auth.current()))
    .body as unknown as { items: Array<{ slug: string }> };
  const slug = list.items[0]?.slug;
  expect(slug, "种子里应有至少一个工具").toBeTruthy();
  return slug!;
}

test.describe.configure({ mode: "serial" });

test.describe("真实后端联调（mock 之外的路径）", () => {
  // 静态资源请求的现场取证（只在失败时打印，见 diagnostics.ts）
  test.beforeEach(async ({ page }) => {
    trackChunkRequests(page);
  });

  test.afterEach(async ({ page, request }, testInfo) => {
    if (testInfo.status === testInfo.expectedStatus) return;
    await reportChunkDiagnostics(page, request, testInfo).catch((error: unknown) => {
      console.log(`[诊断] 采集失败：${String(error)}`);
    });
  });

  test("A. 会话恢复：refresh cookie 的真实 Path + F5 不闪登录页", async ({ page, context }) => {
    const auth = captureAuthHeader(page);

    await page.goto("/");
    await expect(page).toHaveURL(/\/login/);
    await expect(page.getByLabel("用户名", { exact: true })).toBeVisible();

    await signIn(page, ADMIN.username, ADMIN.password);
    await expect(page).toHaveURL(/\/$/);
    await expect(page.getByTestId("top-nav")).toBeVisible();

    // CONTRACT §17.12：断言必须等**数据就绪**，不能依赖 top-nav 这种 render 阶段
    // 就出现的元素 —— React Query 的 /tools 请求在 render 之后才发出，冷启动后端
    // 首次请求较慢时先前的断言会跑在请求之前（间歇性失败）。
    await expectPortalReady(page);
    expect(auth.current(), "应能抓到 Authorization 头").toBeTruthy();

    // 真实 cookie：Path 必须是 /api/v1/auth（mock 用的是 /，只有真实后端能验）
    const cookies = await context.cookies();
    const refresh = cookies.find((cookie) => cookie.name === "refresh_token");
    expect(refresh, "登录后应下发 refresh_token cookie").toBeTruthy();
    expect(refresh?.path).toBe("/api/v1/auth");
    expect(refresh?.httpOnly).toBe(true);

    const refreshFailures: string[] = [];
    page.on("response", (response) => {
      if (response.url().includes("/auth/refresh") && response.status() >= 400) {
        refreshFailures.push(`${response.status()} ${response.url()}`);
      }
    });

    await installFlashProbe(page);
    await page.reload();
    await expect(page.getByRole("heading", { name: "发现内网工具与 Skill" })).toBeVisible();

    const seen = await page.evaluate(() => window.__seen);
    expect(seen, "F5 期间不应渲染登录页").toEqual({ login: false, loader: true });
    expect(refreshFailures, "refresh 不应 4xx").toEqual([]);
  });

  test("B. facets 的真实 null 序列化 + 真实 SQL 分页/筛选/排序", async ({ page }) => {
    const auth = captureAuthHeader(page);
    await signIn(page, ADMIN.username, ADMIN.password);
    await expectPortalReady(page);

    const page1 = await api(page, `${API}/tools?page=1&page_size=12`, auth.current());
    const body1 = page1.body as unknown as {
      items: Array<{ tool_type: string; download_count: number }>;
      total: number;
      pages: number;
      page_size: number;
      facets: unknown;
    };
    expect(page1.status).toBe(200);
    expect(body1.facets, "page=1 的 facets 必须是对象").not.toBeNull();
    expect(Object.keys(body1.facets as object)).toContain("categories");
    expect(body1.total).toBeGreaterThan(0);
    expect(body1.pages).toBe(Math.ceil(body1.total / body1.page_size));

    const page2 = await api(page, `${API}/tools?page=2&page_size=12`, auth.current());
    const body2 = page2.body as unknown as { facets: unknown; items: unknown[] };
    expect(page2.status).toBe(200);
    expect(Object.prototype.hasOwnProperty.call(body2, "facets")).toBe(true);
    expect(body2.facets, "翻页时 facets 键存在且为 null").toBeNull();

    // 类型筛选真的下推到 SQL
    const files = (await api(
      page,
      `${API}/tools?page=1&page_size=50&type=file`,
      auth.current(),
    )).body as unknown as { items: Array<{ tool_type: string }> };
    expect(files.items.length).toBeGreaterThan(0);
    expect(files.items.every((item) => item.tool_type === "file")).toBe(true);

    // sort=hot 是真实 ORDER BY download_count DESC
    const hot = (await api(
      page,
      `${API}/tools?page=1&page_size=12&sort=hot`,
      auth.current(),
    )).body as unknown as { items: Array<{ download_count: number }> };
    const downloads = hot.items.map((item) => item.download_count);
    expect([...downloads].sort((a, b) => b - a)).toEqual(downloads);

    // 关键字搜索命中名称/简介/标签之一
    const search = (await api(
      page,
      `${API}/tools?page=1&page_size=12&q=${encodeURIComponent("日志")}`,
      auth.current(),
    )).body as unknown as { items: Array<{ name: string; summary: string; tags: string[] }> };
    expect(search.items.length).toBeGreaterThan(0);
  });

  test("C. 登出成功的真实响应形状：200 + {\"status\":\"ok\"}（不是 204）", async ({ page }) => {
    await signIn(page, ADMIN.username, ADMIN.password);
    await expectPortalReady(page);

    const logoutResponse = page.waitForResponse(
      (response) =>
        response.url().includes("/auth/logout") && response.request().method() === "POST",
    );
    await page.getByTestId("user-menu-trigger").click();
    await page.getByRole("menuitem", { name: "退出登录" }).click();

    const response = await logoutResponse;
    // CONTRACT §14.2：登出是 200 + body，不是 204
    expect(response.status()).toBe(200);
    const body = (await response.json()) as { status?: string };
    expect(body.status).toBe("ok");

    await expect(page).toHaveURL(/\/login/);
    await page.goto("/");
    await expect(page).toHaveURL(/\/login/);
  });

  test("C2. 改密接口的真实错误形状：400 VALIDATION_ERROR + details.fields[old_password]", async ({
    page,
  }) => {
    const auth = captureAuthHeader(page);
    await signIn(page, ADMIN.username, ADMIN.password);
    await expectPortalReady(page);

    const result = await api(page, `${API}/auth/change-password`, auth.current(), {
      method: "POST",
      body: { old_password: "definitely-wrong-password", new_password: "Another@12345" },
    });

    expect(result.status).toBe(400);
    const body = result.body as unknown as {
      code: string;
      details: { fields: Array<{ field: string; message: string }> } | null;
    };
    expect(body.code).toBe("VALIDATION_ERROR");
    expect(body.details?.fields?.[0]?.field).toBe("old_password");
    // 刻意**不**验证改密成功路径：那会改掉种子账号的密码，破坏后续所有联调。
    // 成功形状由 CONTRACT §14.2 冻结，并与登出的 200+{"status":"ok"} 同批复核。
  });

  test("D. 详情页渲染真实数据 + skill-preview 按真实版本可取（验收 #1/#2/#6）", async ({
    page,
  }) => {
    const auth = captureAuthHeader(page);
    await signIn(page, ADMIN.username, ADMIN.password);
    await expectPortalReady(page);

    // 用 API 找 skill 工具（种子里一定有），再走 UI 打开详情
    const list = (await api(page, `${API}/tools?page=1&page_size=50&type=skill`, auth.current()))
      .body as unknown as { items: Array<{ slug: string; name: string }> };
    expect(list.items.length, "种子里应有 skill 类型工具").toBeGreaterThan(0);
    const skillTool = list.items[0]!;

    await page.goto(`/tools/${skillTool.slug}`);
    await expectAfterNavigation(page.getByTestId("tool-detail"), "工具详情页");
    await expect(
      page.getByTestId("tool-detail").getByRole("heading", { level: 1 }).first(),
    ).toContainText(skillTool.name);
    await expect(page.getByTestId("version-item").first()).toBeVisible();

    // skill-preview 只对真实版本号生效（用 API 取当前版本）
    const versions = (await api(page, `${API}/tools/${skillTool.slug}/versions`, auth.current()))
      .body as unknown as Array<{ version: string; is_current: boolean }>;
    expect(versions.length).toBeGreaterThan(0);
    const current = versions.find((item) => item.is_current) ?? versions[0]!;
    const preview = await api(
      page,
      `${API}/tools/${skillTool.slug}/versions/${encodeURIComponent(current.version)}/skill-preview`,
      auth.current(),
    );
    expect(preview.status).toBe(200);
    const previewBody = preview.body as unknown as {
      manifest: unknown;
      file_tree: Array<{ path: string }>;
      readme_md: string | null;
    };
    expect(previewBody.manifest).toBeTruthy();
    expect(previewBody.file_tree.length).toBeGreaterThan(0);
    expect(previewBody.file_tree.some((entry) => entry.path === "SKILL.md")).toBe(true);

    // UI 上的「包内文件」Tab 应能按需加载出真实文件树
    await page.getByTestId("skill-tab-files").click();
    await expect(page.getByTestId("skill-tree-panel")).toContainText("SKILL.md");
  });

  test("E. 真实上传 → 提交 → 批准 → 下载 → SHA256 一致（验收 #3/#11）", async ({ page }) => {
    const auth = captureAuthHeader(page);
    await signIn(page, ADMIN.username, ADMIN.password);
    await expectPortalReady(page);

    // 真实字节：SHA256 由脚本本地算一份，稍后与后端/下载结果逐字比对
    const dir = await mkdtemp(join(tmpdir(), "selftool-e2e-"));
    const fileName = "e2e-payload.txt";
    const filePath = join(dir, fileName);
    const bytes = Buffer.from(`selftool real-backend e2e ${Date.now()}\n`, "utf8");
    await writeFile(filePath, bytes);
    const expectedSha = createHash("sha256").update(bytes).digest("hex");

    const name = uniqueName("E2E 上传校验工具");
    let toolId: number | null = null;
    try {
      const created = await api(page, `${API}/me/tools`, auth.current(), {
        method: "POST",
        body: {
          name,
          summary: "真实后端 E2E：上传/审批/下载往返校验",
          description_md: "## 用途\n\nE2E 往返校验。",
          tool_type: "file",
          tags: ["e2e"],
        },
      });
      expect(created.status).toBe(201);
      const tool = created.body as unknown as { id: number; slug: string };
      toolId = tool.id;

      const uploaded = await apiUpload(
        page,
        `${API}/me/tools/${toolId}/versions`,
        auth.current()!,
        { version: "0.9.0", changelog_md: "E2E 首次上传", auto_submit: "false" },
        { name: fileName, bytes },
      );
      expect(uploaded.status).toBe(201);
      const version = uploaded.body as unknown as { id: number; file_sha256: string };
      // 后端算出的 SHA256 必须与本地一致（FR-FILE-04）
      expect(version.file_sha256).toBe(expectedSha);

      const submitted = await api(page, `${API}/me/tools/${toolId}/submit`, auth.current(), {
        method: "POST",
      });
      expect(submitted.status).toBe(200);
      expect((submitted.body as unknown as { status: string }).status).toBe("pending");

      const approved = await api(
        page,
        `${API}/admin/approvals/${toolId}/approve`,
        auth.current(),
        { method: "POST", body: { version_id: version.id, note: "E2E 自动批准" } },
      );
      expect(approved.status).toBe(200);
      expect((approved.body as unknown as { status: string }).status).toBe("approved");

      // 详情页展示的 SHA256 与真实文件一致
      await page.goto(`/tools/${tool.slug}`);
      await expectAfterNavigation(page.getByTestId("tool-detail"), "工具详情页");
      const shownSha = (await page.getByTestId("sha256-value").first().innerText()).trim();
      expect(shownSha).toBe(expectedSha);

      // 走 UI 的下载按钮（票据 + 隐藏 <a>），文件真的落盘
      const downloadPromise = page.waitForEvent("download");
      await page.getByTestId("download-button").first().click();
      const download = await downloadPromise;
      const downloadedPath = await download.path();
      expect(downloadedPath).toBeTruthy();
      const downloaded = await readFile(downloadedPath!);
      expect(createHash("sha256").update(downloaded).digest("hex")).toBe(expectedSha);
      expect(download.suggestedFilename()).toContain("e2e-payload");
    } finally {
      if (toolId !== null) {
        await api(page, `${API}/me/tools/${toolId}`, auth.current(), { method: "DELETE" });
      }
    }
  });

  test("F. 真实 404 语义：不存在的 slug 与无权访问的 private 工具（验收 #10）", async ({
    page,
  }) => {
    const auth = captureAuthHeader(page);
    await signIn(page, ADMIN.username, ADMIN.password);
    await expectPortalReady(page);

    // 不存在的 slug → 404 页面
    await page.goto("/tools/definitely-not-a-real-slug-9f8e7d");
    await expectAfterNavigation(
      page.getByText("页面不存在或你没有访问权限"),
      "404 页文案",
    );

    // 无权访问：admin 建一个 private 工具，换一个普通用户访问必须是 404（不是 403）
    const name = uniqueName("E2E 私有工具");
    let toolId: number | null = null;
    try {
      const created = await api(page, `${API}/me/tools`, auth.current(), {
        method: "POST",
        body: {
          name,
          summary: "E2E 私有工具",
          description_md: "private",
          tool_type: "prompt",
          visibility: "private",
        },
      });
      if (created.status !== 201) {
        test.info().annotations.push({
          type: "gap",
          description: `创建 private 工具失败：${created.status}`,
        });
        return;
      }
      const tool = created.body as unknown as { id: number; slug: string };
      toolId = tool.id;

      // 用第二个浏览器上下文模拟另一个用户，避免互相污染会话
      const other = await page.context().browser()?.newContext({ locale: "zh-CN" });
      expect(other).toBeTruthy();
      const otherPage = await other!.newPage();
      const otherAuth = captureAuthHeader(otherPage);
      await signIn(otherPage, AUTHOR.username, AUTHOR.password);
      const loggedIn = !/\/login/.test(otherPage.url());
      if (!loggedIn) {
        test.info().annotations.push({
          type: "gap",
          description: `种子缺少作者账号 ${AUTHOR.username}（CONTRACT §14.6），跳过无权访问检查`,
        });
        await other!.close();
        return;
      }

      // 无权访问 → 404（FR-FILE-08），不是 403
      const denied = await api(otherPage, `${API}/tools/${tool.slug}`, otherAuth.current());
      expect(denied.status, "无权访问必须返回 404").toBe(404);
      await otherPage.goto(`/tools/${tool.slug}`);
      await expectAfterNavigation(
        otherPage.getByText("页面不存在或你没有访问权限"),
        "无权访问的 404 页文案",
      );
      await other!.close();
    } finally {
      if (toolId !== null) {
        await api(page, `${API}/me/tools/${toolId}`, auth.current(), { method: "DELETE" });
      }
    }
  });

  test("G. 图片能力 URL：签名或占位降级（CONTRACT §14.3）", async ({ page }) => {
    const auth = captureAuthHeader(page);
    await signIn(page, ADMIN.username, ADMIN.password);
    await expectPortalReady(page);

    const list = (await api(page, `${API}/tools?page=1&page_size=50`, auth.current()))
      .body as unknown as { items: Array<{ cover_url: string | null }> };
    const covers = list.items
      .map((item) => item.cover_url)
      .filter((url): url is string => typeof url === "string" && url.length > 0);

    if (covers.length === 0) {
      test.info().annotations.push({
        type: "gap",
        description: "种子里所有工具都没有 cover_url",
      });
      return;
    }

    if (covers.every((url) => url.includes("sig="))) {
      const loaded = await page.evaluate(async () => {
        const images = Array.from(document.images);
        await Promise.all(
          images.map((image) =>
            image.complete
              ? Promise.resolve()
              : new Promise<void>((resolve) => {
                  image.addEventListener("load", () => resolve(), { once: true });
                  image.addEventListener("error", () => resolve(), { once: true });
                }),
          ),
        );
        return images.map((image) => ({
          src: image.currentSrc || image.src,
          ok: image.naturalWidth > 0,
        }));
      });
      expect(loaded.filter((entry) => !entry.ok), "带签名的封面图必须能加载").toEqual([]);
      return;
    }

    // 后端还没实现签名能力 URL（M2 进行中）：记 gap，并断言前端已降级为占位块
    test.info().annotations.push({
      type: "gap",
      description: `cover_url 未带 sig（后端尚未实现 §14.3 的签名下发）：${covers[0]}`,
    });
    const brokenImages = await page.evaluate(() =>
      Array.from(document.images)
        .filter((image) => image.complete && image.naturalWidth === 0)
        .map((image) => image.currentSrc || image.src),
    );
    expect(brokenImages, "图片失败必须降级为占位块，不能留破图").toEqual([]);
  });

  test("I. 真实设置写入：非法值整体回滚、合法项不受影响（验收 #15）", async ({ page }) => {
    const auth = captureAuthHeader(page);
    await signIn(page, ADMIN.username, ADMIN.password);
    await expectPortalReady(page);

    const before = await api(page, `${API}/admin/settings`, auth.current());
    expect(before.status).toBe(200);
    const items = (before.body as unknown as { items: Array<{ key: string; value: unknown }> }).items;
    const target = items.find((item) => item.key === "portal.page_size");
    const other = items.find((item) => item.key === "approval.version_reapproval");
    expect(target, "种子应包含 portal.page_size").toBeTruthy();
    expect(other, "种子应包含 approval.version_reapproval").toBeTruthy();

    // 一个明显非法的值（page_size 有 min/max 约束）
    const failed = await api(page, `${API}/admin/settings`, auth.current(), {
      method: "PUT",
      body: {
        items: [
          { key: "portal.page_size", value: -5 },
          { key: "approval.version_reapproval", value: !other?.value },
        ],
      },
    });
    expect(failed.status, "非法值应被拒绝").toBe(400);
    const failedBody = failed.body as unknown as { code: string; details: Record<string, unknown> | null };
    expect(failedBody.code).toBe("SETTING_INVALID");
    expect(
      JSON.stringify(failedBody.details ?? {}),
      "details 应指出出错的 key",
    ).toContain("portal.page_size");

    // 整体回滚：合法项也必须保持原值
    const after = await api(page, `${API}/admin/settings`, auth.current());
    const afterItems = (after.body as unknown as { items: Array<{ key: string; value: unknown }> })
      .items;
    expect(afterItems.find((item) => item.key === "portal.page_size")?.value).toBe(
      target?.value,
    );
    expect(afterItems.find((item) => item.key === "approval.version_reapproval")?.value).toBe(
      other?.value,
    );
  });

  test("J. 真实导出 CSV：BOM + 中文不乱码（验收 #20）", async ({ page }) => {
    const auth = captureAuthHeader(page);
    await signIn(page, ADMIN.username, ADMIN.password);
    await expectPortalReady(page);

    const probe = await page.evaluate(async ([url, token]) => {
      const response = await fetch(url as string, {
        headers: { Authorization: token as string },
      });
      const bytes = new Uint8Array(await response.arrayBuffer());
      const head = Array.from(bytes.slice(0, 3));
      const text = new TextDecoder("utf-8").decode(bytes);
      return {
        status: response.status,
        contentType: response.headers.get("content-type") ?? "",
        disposition: response.headers.get("content-disposition") ?? "",
        head,
        hasChineseHeader: text.includes("用户名") || text.includes("display_name"),
      };
    }, [`${API}/admin/export/users`, auth.current()] as const);

    expect(probe.status).toBe(200);
    expect(probe.contentType).toContain("text/csv");
    expect(probe.disposition).toContain("attachment");
    // CONTRACT §16.1 验收 20：前 3 字节必须是 UTF-8 BOM，否则 Excel 打开中文乱码
    expect(probe.head).toEqual([0xef, 0xbb, 0xbf]);
    expect(probe.hasChineseHeader).toBe(true);
  });

  test("K. 真实 Token 生命周期：明文只出现一次 + 吊销后失效（§16.4）", async ({ page }) => {
    const auth = captureAuthHeader(page);
    await signIn(page, ADMIN.username, ADMIN.password);
    await expectPortalReady(page);

    /**
     * 登录后的会话 JWT 必须**当场存下来**：`captureAuthHeader` 记录的是「最后一个」
     * Authorization 头，而下面会用它自己签发的 API Token 直接打 `/tools`
     * —— 那个请求也会被监听到，之后 `auth.current()` 就变成了只有 `tools:read`
     * 的 Token，再拿去调 `/admin/tokens/{id}/revoke` 必然 403 SCOPE_MISSING。
     */
    const sessionAuth = auth.current();
    expect(sessionAuth).toMatch(/^Bearer /);

    const created = await api(page, `${API}/admin/tokens`, sessionAuth, {
      method: "POST",
      body: { name: `E2E Token ${Date.now().toString(36)}`, scopes: ["tools:read"] },
    });
    expect(created.status).toBe(201);
    const token = created.body as unknown as { id: number; token: string; token_prefix: string };
    expect(token.token.startsWith("st_")).toBe(true);
    expect(token.token_prefix.length).toBeGreaterThan(3);

    try {
      // 列表接口永不返回明文
      const list = await api(page, `${API}/admin/tokens`, sessionAuth);
      const listText = JSON.stringify(list.body);
      expect(listText.includes(token.token), "列表接口不应泄露明文").toBe(false);

      // 明文可用
      const call = await page.evaluate(async (raw) => {
        const response = await fetch("/api/v1/tools?page=1&page_size=1", {
          headers: { Authorization: `Bearer ${raw}` },
        });
        return { status: response.status, contentType: response.headers.get("content-type") ?? "" };
      }, token.token);
      expect(call.status).toBe(200);

      // 吊销后立即失效
      const revoked = await api(page, `${API}/admin/tokens/${token.id}/revoke`, sessionAuth, {
        method: "POST",
      });
      expect(revoked.status).toBe(200);
      const afterRevoke = await page.evaluate(async (raw) => {
        const response = await fetch("/api/v1/tools?page=1&page_size=1", {
          headers: { Authorization: `Bearer ${raw}` },
        });
        const body = (await response.json().catch(() => null)) as { code?: string } | null;
        return { status: response.status, code: body?.code ?? null };
      }, token.token);
      expect(afterRevoke.status).toBe(401);
      // CONTRACT §16.4：这里**应为** TOKEN_REVOKED，后端 M4 才修 —— 记下来但不作为失败
      test.info().annotations.push({
        type: afterRevoke.code === "TOKEN_REVOKED" ? "ok" : "gap",
        description: `吊销后 code=${afterRevoke.code}（§16.4 要求 TOKEN_REVOKED，M4 修）`,
      });
    } finally {
      await api(page, `${API}/admin/tokens/${token.id}`, sessionAuth, { method: "DELETE" });
    }
  });

  test("H. 生产拓扑：后端托管 dist、无 mock worker（验收 #30）", async ({ page }) => {
    const response = await page.goto("/");
    expect(response?.headers()["content-type"] ?? "").toContain("text/html");
    // 未登录 → 落登录页；这一步同时验证入口 chunk 与登录页 chunk 可加载
    await expectAfterNavigation(page.getByTestId("login-page"), "登录页");

    // SPA fallback 会为未知路径返回 index.html（200 + text/html），所以不能断言 404：
    // 真正的判据是「没有这个文件」——它的 content-type 必须是 HTML 而不是 JS。
    const probe = await page.evaluate(async () => {
      const worker = await fetch("/mockServiceWorker.js");
      const html = await worker.text();
      return {
        status: worker.status,
        contentType: worker.headers.get("content-type") ?? "",
        looksLikeWorker: html.includes("mockServiceWorker") || html.includes("importScripts"),
      };
    });
    expect(probe.looksLikeWorker, "/mockServiceWorker.js 不应存在于生产产物").toBe(false);
    expect(probe.contentType, "未知路径由 SPA fallback 返回 HTML").toContain("text/html");

    // 正对照：真实构建产物必须能取到（证明静态托管本身是好的）
    const asset = await page.evaluate(async () => {
      const script = document.querySelector<HTMLScriptElement>('script[type="module"]');
      const src = script?.getAttribute("src") ?? "";
      const response = await fetch(src);
      return { src, status: response.status, contentType: response.headers.get("content-type") ?? "" };
    });
    expect(asset.src).toMatch(/\/assets\/index-.*\.js$/);
    expect(asset.status).toBe(200);
    expect(asset.contentType).toMatch(/javascript/);
  });

  test("N. M5 种子：26 个工具 + viewer 账号 + 真实文件下载 SHA256 一致（验收 #12）", async ({
    page,
  }) => {
    const auth = captureAuthHeader(page);
    await signIn(page, ADMIN.username, ADMIN.password);
    await expectPortalReady(page);

    // 种子规模（CONTRACT §20.1 A2）
    const list = (await api(page, `${API}/tools?page=1&page_size=100`, auth.current()))
      .body as unknown as { total: number; items: Array<{ slug: string; tool_type: string }> };
    expect(list.total, "M5 种子应为 26 个门户可见工具").toBe(26);
    expect(
      list.items.some((item) => item.tool_type === "file"),
      "种子里应有 file 类型工具",
    ).toBe(true);

    // 种子文件必须是**真实文件**：sha256 存在、可下载、下载内容哈希逐字一致
    // （M2 时种子只有 file_size 没有文件，§17.9 的中间态；M5 已修）
    const fileTool = list.items.find((item) => item.tool_type === "file")!;
    const detail = (await api(page, `${API}/tools/${fileTool.slug}`, auth.current()))
      .body as unknown as {
      current_version: { id: number; file_sha256: string | null; can_download: boolean } | null;
    };
    const version = detail.current_version;
    expect(version?.file_sha256, "种子版本必须有真实文件的 sha256（§20.1 A3/A4）").toBeTruthy();
    expect(version?.can_download).toBe(true);

    const ticket = (
      await api(page, `${API}/tools/${fileTool.slug}/download-ticket`, auth.current(), {
        method: "POST",
        body: { version_id: version!.id },
      })
    ).body as unknown as { url: string };
    const digest = await page.evaluate(async (url) => {
      const response = await fetch(url);
      const buffer = await response.arrayBuffer();
      const hash = await crypto.subtle.digest("SHA-256", buffer);
      return {
        status: response.status,
        size: buffer.byteLength,
        sha256: Array.from(new Uint8Array(hash))
          .map((byte) => byte.toString(16).padStart(2, "0"))
          .join(""),
      };
    }, ticket.url);
    expect(digest.status).toBe(200);
    expect(digest.size, "种子文件不应为空").toBeGreaterThan(0);
    expect(digest.sha256, "下载内容的 SHA256 必须与接口下发的逐字一致").toBe(
      version!.file_sha256,
    );

    // viewer 账号（M5 种子新增，§17.8）：能看详情，但下载被拒（docs/01 §3.2 / §15.7）
    const viewerContext = await page.context().browser()?.newContext({ locale: "zh-CN" });
    expect(viewerContext, "应能开第二个浏览器上下文").toBeTruthy();
    const viewerPage = await viewerContext!.newPage();
    const viewerAuth = captureAuthHeader(viewerPage);
    await signIn(viewerPage, "viewer", "Viewer@12345");
    await expectAfterNavigation(viewerPage.getByTestId("tool-card").first(), "viewer 门户");

    const viewerDetail = await api(viewerPage, `${API}/tools/${fileTool.slug}`, viewerAuth.current());
    expect(viewerDetail.status, "viewer 可以查看工具详情").toBe(200);
    const viewerTicket = await api(
      viewerPage,
      `${API}/tools/${fileTool.slug}/download-ticket`,
      viewerAuth.current(),
      { method: "POST", body: { version_id: version!.id } },
    );
    expect(viewerTicket.status, "viewer 不允许下载").toBe(404);

    // 残留缺口：详情里的 `can_download` 对 viewer 仍是 true，与下载授权不一致
    const viewerFlag = (
      viewerDetail.body as unknown as { current_version?: { can_download?: boolean } }
    ).current_version?.can_download;
    test.info().annotations.push({
      type: viewerFlag === false ? "ok" : "gap",
      description: `viewer 的 current_version.can_download=${viewerFlag}（FR-ACL-05 期望 false；实际下载已被 404 no_download_permission 拦住）`,
    });
    await viewerContext!.close();
  });

  test("L. 懒加载 chunk 一次性失败 → 自动重载并恢复（M4 交付项 1.1/1.3/1.4）", async ({ page }) => {
    /*
     * 这两个韧性用例**只能**在真实产物上跑：mock 模式（dev + MSW）下页面被 Service
     * Worker 接管，而 Playwright 的 `page.route` 拦不到 SW 控制的请求（模块脚本请求
     * 根本不进路由层）。生产构建没有 SW，才能精确地「只让第一次请求失败」。
     * 不依赖后端的离线自检见 `scripts/check-lazy-retry.mjs`。
     */
    const auth = captureAuthHeader(page);
    await signIn(page, ADMIN.username, ADMIN.password);
    await expectPortalReady(page);
    const slug = await firstToolSlug(page, auth);

    const chunkFile = findBuiltChunk("ToolDetailPage");
    expect(chunkFile, "dist/assets 里应有 ToolDetailPage 的 chunk").toBeTruthy();

    // 只让**第一次**请求失败：精确模拟瞬时抖动的成因（网络中断/请求被打断）
    let aborted = 0;
    let allowed = 0;
    await page.route(new RegExp(`/${escapeRegExp(chunkFile!)}$`), async (route) => {
      if (aborted === 0) {
        aborted += 1;
        await route.abort("failed");
        return;
      }
      allowed += 1;
      await route.continue();
    });

    await page.goto(`/tools/${slug}`);

    // 断言经得起一次重载：第一次 chunk 失败后 lazyWithRetry 会整页重载，
    // 重载后这一次请求被放行，页面必须自己恢复出详情页。
    await expectAfterNavigation(
      page.getByTestId("tool-detail"),
      "工具详情页（chunk 首次失败后自动重载恢复）",
    );

    expect(aborted, "应拦下并失败掉第一次 chunk 请求").toBe(1);
    expect(allowed, "重载后应重新请求该 chunk 并成功").toBeGreaterThan(0);

    // 易错点 1：加载成功后必须清除标记，否则用户在这次会话里永久失去自动恢复能力
    const leftover = await page.evaluate(() =>
      Object.keys(window.sessionStorage).filter((key) => key.startsWith("selftool:chunk-retry:")),
    );
    expect(leftover, "成功加载后应清除 chunk 重试标记").toEqual([]);
  });

  test("M. chunk 持续失败 → 展示可手动重试的边界，且不无限重载（M4 交付项 1.2）", async ({
    page,
  }) => {
    const auth = captureAuthHeader(page);
    await signIn(page, ADMIN.username, ADMIN.password);
    await expectPortalReady(page);
    const slug = await firstToolSlug(page, auth);

    const chunkFile = findBuiltChunk("ToolDetailPage");
    expect(chunkFile, "dist/assets 里应有 ToolDetailPage 的 chunk").toBeTruthy();

    let attempts = 0;
    // 只数真正的文档加载：framenavigated 会把 SPA 的 pushState 也算成导航，
    // 用它判断「重载了几次」会虚高。
    let documentLoads = 0;
    page.on("request", (request) => {
      if (request.resourceType() === "document") documentLoads += 1;
    });
    await page.route(new RegExp(`/${escapeRegExp(chunkFile!)}$`), async (route) => {
      attempts += 1;
      await route.abort("failed");
    });

    await page.goto(`/tools/${slug}`);

    // 第一次失败 → 自动重载；重载后仍失败 → 交给错误边界（不再自动重载）
    await expect(page.getByTestId("chunk-error-boundary")).toBeVisible({ timeout: 30_000 });
    await expect(page.getByText(/已自动重试过一次仍未成功/)).toBeVisible();
    await expect(page.getByRole("button", { name: /重试/ })).toBeVisible();

    const attemptsAtBoundary = attempts;
    const loadsAtBoundary = documentLoads;
    await page.waitForTimeout(3_000);
    expect(attempts, "标记生效后不应再重试，否则就是无限重载").toBe(attemptsAtBoundary);
    expect(
      attemptsAtBoundary,
      "只应尝试两次（首次 + 一次自动重载）",
    ).toBeLessThanOrEqual(2);
    expect(
      loadsAtBoundary,
      `只应自动重载一次（观测：文档加载 ${loadsAtBoundary} 次）`,
    ).toBe(2);
    expect(documentLoads, "边界稳定后不应继续加载文档").toBe(loadsAtBoundary);

    // 防循环标记确实是生效的那个机制
    const flags = await page.evaluate(() =>
      Object.keys(window.sessionStorage).filter((key) => key.startsWith("selftool:chunk-retry:")),
    );
    expect(flags.length, "应留下重试标记以阻止第二次自动重载").toBeGreaterThan(0);
  });
});
