import type { Page } from "@playwright/test";
import { expect } from "@playwright/test";

/**
 * Shared helpers for the M2 acceptance suites (mock mode).
 * Mirrors the conventions of `m1-acceptance.spec.ts`.
 */

export const ARTIFACTS = "e2e/artifacts";

export interface Credentials {
  username: string;
  password: string;
}

export const ADMIN: Credentials = { username: "admin", password: "Admin@12345" };
export const AUTHOR: Credentials = { username: "zhangsan", password: "Author@12345" };
/** Seeded for the M2 acceptance walkthrough (viewer role, docs/01 §3.2). */
export const VIEWER: Credentials = { username: "viewer", password: "Viewer@12345" };
/**
 * 兼具 `approver` 的账号（M8 · F11 用它验证「非超管打开管理概览页」的降级路径：
 * `/admin/overview` 放行，`/admin/stats/insights` 403）。
 */
export const APPROVER: Credentials = { username: "wangwu", password: "Author@12345" };

/**
 * Collects genuine errors. Browsers log every non-2xx response as a console
 * error, so expected auth failures are tracked through the response listener
 * with a URL attached instead.
 */
const EXPECTED_FAILING_ENDPOINTS = [
  "/api/v1/auth/refresh",
  "/api/v1/auth/login",
  "/api/v1/auth/change-password",
];

export interface WatchOptions {
  /** Extra expected failures, e.g. the deliberate 404 of an unreadable tool. */
  allow?: (status: number, url: string) => boolean;
}

export function watchPageErrors(page: Page, options: WatchOptions = {}): string[] {
  const errors: string[] = [];
  page.on("pageerror", (error) => errors.push(`pageerror: ${error.message}`));
  page.on("response", (response) => {
    const status = response.status();
    if (status < 400) return;
    const url = response.url();
    if (EXPECTED_FAILING_ENDPOINTS.some((endpoint) => url.includes(endpoint))) return;
    if (options.allow?.(status, url)) return;
    errors.push(`HTTP ${status} ${url}`);
  });
  page.on("console", (message) => {
    if (message.type() !== "error") return;
    const text = message.text();
    if (/Failed to load resource: the server responded with a status of \d+/.test(text)) return;
    // React 18 + React-19-style shadcn components would log this (CONTRACT §14.9).
    if (/Function components cannot be given refs/i.test(text)) {
      errors.push(`react-ref-warning: ${text.slice(0, 160)}`);
      return;
    }
    errors.push(`console.error: ${text}`);
  });
  return errors;
}

export async function login(
  page: Page,
  credentials: Credentials = ADMIN,
  options: { goto?: boolean; expectPortal?: boolean } = {},
): Promise<void> {
  /*
   * 直接去 `/login`，**不要**再用 `goto("/")` 靠重定向。
   * 门户默认允许匿名浏览（`portal.allow_anonymous_view`，迁移 0005），
   * 访问 `/` 会直接渲染门户而不再跳登录页。
   */
  if (options.goto ?? true) await page.goto("/login");
  await expect(page.getByTestId("login-page")).toBeVisible();
  await page.getByLabel("用户名", { exact: true }).fill(credentials.username);
  await page.getByLabel("密码", { exact: true }).fill(credentials.password);
  await page.getByRole("button", { name: "登录", exact: true }).click();
  if (options.expectPortal ?? true) {
    await expect(page).toHaveURL(/\/$/);
  }
}

/** Opens the portal card for a tool slug (the whole card is a `<Link>`). */
export async function openTool(page: Page, slug: string): Promise<void> {
  const card = page.locator(`[data-tool-slug="${slug}"]`);
  await expect(card).toBeVisible();
  await card.click();
  await expect(page).toHaveURL(new RegExp(`/tools/${slug}$`));
  await expect(page.getByTestId("tool-detail")).toBeVisible();
}

/** Captures `navigator.clipboard.writeText` payloads for copy assertions. */
export async function captureClipboard(page: Page): Promise<void> {
  await page.addInitScript(() => {
    const store: string[] = [];
    (window as unknown as { __copied: string[] }).__copied = store;
    const clipboard = navigator.clipboard;
    if (clipboard) {
      const original = clipboard.writeText.bind(clipboard);
      clipboard.writeText = (text: string) => {
        store.push(text);
        return original(text);
      };
    }
  });
}

export async function copiedValues(page: Page): Promise<string[]> {
  return page.evaluate(() => (window as unknown as { __copied?: string[] }).__copied ?? []);
}

/** Records whether a request matching `pattern` happened (lazy-load assertions). */
export function trackRequests(page: Page, pattern: string | RegExp): { count: () => number } {
  let hits = 0;
  page.on("request", (request) => {
    const url = request.url();
    if (typeof pattern === "string" ? url.includes(pattern) : pattern.test(url)) hits += 1;
  });
  return { count: () => hits };
}

/** Two-dot URL comparison: assert a query param without normalising the rest. */
export function urlHasParam(page: Page, key: string, value: string): boolean {
  return new URL(page.url()).searchParams.get(key) === value;
}

/* -------------------------------------------------------------------------- */
/* M12 · 站点定制信息（CONTRACT §27）；M13 按 §28.4 加 footer_tagline           */
/* -------------------------------------------------------------------------- */

/**
 * 可编程的 `/meta` 覆盖项 —— 与 `src/mocks/handlers.ts` 的 `MockSiteConfig`
 * 逐字对应（这里刻意不 import 那个模块：它是 dev-only 的 MSW 代码，不该被
 * Playwright 测试进程加载）。
 */
export interface MockSiteConfig {
  site_name?: string;
  site_subtitle?: string;
  /** M13（§28.4）：第 6 个设置项；mock 的默认值是原写死的标语，不是空串。 */
  footer_tagline?: string;
  footer_org?: string;
  footer_contact_email?: string;
  footer_contact_phone?: string;
  footer_notice?: string;
}

/** 必须与 `src/mocks/handlers.ts` 的 `SITE_CONFIG_STORAGE_KEY` 一致。 */
export const MOCK_SITE_CONFIG_KEY = "localcraft.msw.site-config";

/**
 * 让 mock 的 `/meta` 返回「已配置」的站点信息（M12 的 F1~F3 都靠它构造场景）。
 *
 * **必须在任何 `page.goto` 之前调用**：走 `addInitScript` 在文档脚本之前写入
 * `localStorage`，`/meta` 的 mock 处理器每次响应时读它。多次调用时按注册顺序执行、
 * 后写覆盖先写 —— 同一个用例里想换一组配置，再写一次然后重新导航即可。
 */
export async function setSiteConfig(page: Page, config: MockSiteConfig): Promise<void> {
  await page.addInitScript(
    (payload: { key: string; value: string }) => {
      window.localStorage.setItem(payload.key, payload.value);
    },
    { key: MOCK_SITE_CONFIG_KEY, value: JSON.stringify(config) },
  );
}
