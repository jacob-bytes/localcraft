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
  /*
   * M14：门户首页可能同时渲染「继续使用」/「我的收藏」横向区块，同一个 slug 会在
   * 页面上出现两张卡。这里定位的是**卡片墙上**那张（`ToolGrid` 的 `tool-grid`），
   * 与既有语义一致：`openTool` 的入口一直是卡片墙。
   */
  const card = page.locator(`[data-testid="tool-grid"] [data-tool-slug="${slug}"]`);
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

/* -------------------------------------------------------------------------- */
/* M14 · 门户两个区块（CONTRACT §29.8 → M16 §32 改为 tab）+ 探活展示（§29.3）  */
/* -------------------------------------------------------------------------- */

/** 必须与 `src/lib/recentTools.ts` 的 `RECENT_TOOLS_STORAGE_KEY` 一致。 */
export const RECENT_TOOLS_KEY = "localcraft:recent-tools";

export interface RecentToolFixture {
  slug: string;
  name: string;
  /** epoch ms；只用于排序，不含任何身份信息（M7 · F6 的存储形状）。 */
  at: number;
}

/**
 * 预置「最近访问」。**必须在任何 `page.goto` 之前调用**：走 `addInitScript` 在文档
 * 脚本之前写入 `localStorage`，门户挂载时 `readRecentTools()` 才读得到。
 *
 * 写的是与生产**完全相同的键与形状**（`{slug, name, at}[]`，最多 8 条）——
 * 这里不发明任何「测试专用」存储，否则测的就不是真实路径了。
 */
export async function setRecentTools(
  page: Page,
  entries: RecentToolFixture[],
): Promise<void> {
  await page.addInitScript(
    (payload: { key: string; value: string }) => {
      window.localStorage.setItem(payload.key, payload.value);
    },
    { key: RECENT_TOOLS_KEY, value: JSON.stringify(entries) },
  );
}

/**
 * M16：mock 的收藏/点赞状态键（**必须**与 `src/mocks/handlers.ts` 的
 * `ENGAGEMENT_STORAGE_KEY` 逐字一致）。
 *
 * 为什么需要直接种它：`GET /me/favorites` 要验的是「**total > 24** 时只给第一页
 * + 一条『查看全部 N 个 →』」，而种子用户里收藏最多的一个也只有 1 条（zhangsan）。
 * MSW 跑在 **Service Worker** 里，`page.route` 拦不到它（Playwright 文档写得很
 * 清楚：SW 处理的请求对 `page.route` 不可见），所以不能靠「伪造一个响应」。
 * 走**与 mock 自己完全相同的存储键**种数据，测的就还是真实代码路径 ——
 * mock 的 handler 真的去读它、真的分页、真的算 `total`。
 */
export const MOCK_ENGAGEMENT_KEY = "localcraft.msw.engagement";

/** 给某个 mock 用户预置收藏（键是用户 id 的字符串，值是工具 id 列表）。 */
export async function setMockFavorites(
  page: Page,
  userId: number,
  toolIds: number[],
): Promise<void> {
  await page.addInitScript(
    (payload: { key: string; value: string }) => {
      window.localStorage.setItem(payload.key, payload.value);
    },
    {
      key: MOCK_ENGAGEMENT_KEY,
      value: JSON.stringify({ favorites: { [String(userId)]: toolIds }, likes: {} }),
    },
  );
}

/**
 * 种子里**全部** 26 个工具的 id（boundary 1~8 + generated 101~118）。
 * 用它喂 `setMockFavorites` 就能造出 `total > 24` 的收藏夹。
 */
export const MOCK_TOOL_IDS: number[] = [
  ...Array.from({ length: 8 }, (_, index) => index + 1),
  ...Array.from({ length: 18 }, (_, index) => index + 101),
];

export interface ContrastReading {
  dark: boolean;
  contrast: number;
  color: string;
  foreground: string;
  background: string;
}

/**
 * 量一个元素的**实际渲染对比度**（WCAG 2.x 公式）。
 *
 * 与 `m9-viewer-engagement.spec.ts` 里那段是同一套做法，这里抽出来给 M14 用：
 * 令牌是 `oklch(...)`，Chrome 原样返回该语法，所以颜色一律走 **canvas 真实渲染出的
 * 像素**（`getImageData` 给的就是浏览器自己转换后的 sRGB 字节），不手写解析。
 *
 * `subject` 决定量的是哪一对颜色：
 *  - `"text"`（默认）：元素的 `color` vs 它背后第一个不透明的背景 —— WCAG 正文要求 4.5:1；
 *  - `"fill"`：元素**自身的 `background-color`** vs 它**父链**上第一个不透明的背景
 *    —— 用来量「纯图形」标记（状态圆点），WCAG 1.4.11 要求 3:1。
 *    注意不能拿元素的 `color` 去量：那量的是「文字 vs 圆点填充」，是另一回事。
 */
export async function contrastOf(
  page: Page,
  selector: string,
  subject: "text" | "fill" = "text",
): Promise<ContrastReading | null> {
  return page.evaluate(
    ([target, kind]: readonly [string, "text" | "fill"]) => {
      const element = document.querySelector(target);
      if (!element) return null;

      const canvas = document.createElement("canvas");
      canvas.width = 1;
      canvas.height = 1;
      const context = canvas.getContext("2d", { willReadFrequently: true });
      if (!context) return null;

      const toRgb = (cssColor: string): [number, number, number, number] => {
        context.clearRect(0, 0, 1, 1);
        context.fillStyle = "#010203"; // 哨兵：解析失败时不会与目标色混淆
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
      const opaque = (cssColor: string | undefined): [number, number, number, number] | null => {
        if (!cssColor || cssColor === "transparent" || cssColor === "rgba(0, 0, 0, 0)") {
          return null;
        }
        const resolved = toRgb(cssColor);
        return resolved[3] > 0 ? resolved : null;
      };

      const ownBackground = window.getComputedStyle(element).backgroundColor;
      const color = window.getComputedStyle(element).color;
      const foreground =
        kind === "fill" ? (opaque(ownBackground) ?? toRgb("rgba(0,0,0,0)")) : toRgb(color);

      // 背后的底色：文本从元素自身往上找；图形从**父元素**往上找（自身那层就是被测对象）。
      let node: Element | null = kind === "fill" ? element.parentElement : element;
      let background: [number, number, number, number] | null = null;
      while (node) {
        const found = opaque(window.getComputedStyle(node).backgroundColor);
        if (found) {
          background = found;
          break;
        }
        node = node.parentElement;
      }
      const effective: [number, number, number, number] = background ?? [255, 255, 255, 255];
      const foregroundLuminance = luminance(foreground);
      const backgroundLuminance = luminance(effective);
      const lighter = Math.max(foregroundLuminance, backgroundLuminance);
      const darker = Math.min(foregroundLuminance, backgroundLuminance);
      return {
        dark: document.documentElement.classList.contains("dark"),
        contrast: (lighter + 0.05) / (darker + 0.05),
        color: kind === "fill" ? ownBackground : color,
        foreground: `rgb(${foreground[0]}, ${foreground[1]}, ${foreground[2]})`,
        background: `rgb(${effective[0]}, ${effective[1]}, ${effective[2]})`,
      };
    },
    [selector, subject] as const,
  );
}
