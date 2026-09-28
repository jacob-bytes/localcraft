import { expect, test, type Page } from "@playwright/test";

import { ADMIN, login, setSiteConfig, watchPageErrors } from "./helpers";

/**
 * M12 验收 · 副标题 / 页脚 / 标签页标题（CONTRACT §27）。
 *
 * 三条硬要求都有对应的**可计算**证据，不用「看起来没问题」代替：
 *
 * 1. **未配置时零渲染** —— 断言的是 DOM 里 `footer` / `[data-testid=site-subtitle]`
 *    的**元素个数为 0**（不是「没有文字」），并且先等 `/meta` 真的到达
 *    （用依赖 `site_name` 的 logo aria-label 当探针），否则「还没渲染」会被
 *    误判成「正确地没渲染」。
 * 2. **页脚范围** —— 登录页 / 个人中心 / 管理台三处的 `footer` 个数为 0。
 * 3. **邮箱 / 电话防御性降级** —— 断言节点 `tagName` 不是 `A` 且没有 `href`，
 *    即**根本没有生成链接**（比「点不开」更强的判据）。
 *
 * 主题三态用 canvas 实测 WCAG 对比度（沿用 `m9-viewer-engagement.spec.ts` 的
 * 做法），并比较浅色 / 深色的计算颜色不同 —— 这是「颜色来自语义令牌而不是写死」
 * 的证据。**无法读取图像，所以视觉判断不在本文件的范围内。**
 */

const TOOL_SLUG = "log-analyzer-a3f2";
const DEFAULT_TITLE = "工具与 Skill 平台";
const SITE_NAME = "某事业部工具中心";
const SUBTITLE = "为研发与运维提供的一站式工具入口";

const FULL_CONFIG = {
  footer_org: "某事业部数字化中心",
  footer_contact_email: "ops@example.com",
  footer_contact_phone: "010-8888-6666",
  footer_notice: "京ICP备 00000000 号",
} as const;

/**
 * 等 `/meta` 真的到达。
 *
 * 判据是顶栏 logo 的 `aria-label`（`${siteName(meta)} 首页`）—— 它只在 `/meta`
 * 返回后才可能等于目标站点名，因此「零渲染」的断言不会跑在一次尚未完成的请求前面。
 */
async function expectMetaLoaded(page: Page, name: string = DEFAULT_TITLE): Promise<void> {
  await expect(page.getByRole("link", { name: `${name} 首页` })).toBeVisible();
}

test.describe("M12 · 站点定制信息", () => {
  test("F1/F2 · 未配置（5 个字段都是空串）时副标题与页脚在 DOM 上完全不存在", async ({
    page,
  }) => {
    const errors = watchPageErrors(page);

    await page.goto("/");
    await expectMetaLoaded(page);
    await expect(page.getByRole("heading", { name: "发现内网工具与 Skill" })).toBeVisible();
    await expect(page.getByTestId("site-subtitle")).toHaveCount(0);
    await expect(page.locator("footer")).toHaveCount(0);
    await expect(page).toHaveTitle(DEFAULT_TITLE);

    await page.goto(`/tools/${TOOL_SLUG}`);
    await expectMetaLoaded(page);
    await expect(page.getByTestId("tool-detail")).toBeVisible();
    await expect(page.getByTestId("site-subtitle")).toHaveCount(0);
    await expect(page.locator("footer")).toHaveCount(0);

    expect(errors).toEqual([]);
  });

  test("F2 · 四个页脚字段只有空白字符时，页脚仍然整个不渲染（空白不算配置）", async ({
    page,
  }) => {
    const errors = watchPageErrors(page);
    await setSiteConfig(page, {
      site_subtitle: "   ",
      footer_org: " ",
      footer_contact_email: "\t",
      footer_contact_phone: "  ",
      footer_notice: "\n",
    });

    await page.goto("/");
    await expectMetaLoaded(page);
    await expect(page.getByTestId("site-subtitle")).toHaveCount(0);
    await expect(page.locator("footer")).toHaveCount(0);

    expect(errors).toEqual([]);
  });

  test("F1 · 配置后副标题出现在门户 H1 下方一行与登录页站点名下方一行", async ({ page }) => {
    const errors = watchPageErrors(page);
    await setSiteConfig(page, { site_name: SITE_NAME, site_subtitle: SUBTITLE });

    await page.goto("/");
    await expectMetaLoaded(page, SITE_NAME);
    const subtitle = page.getByTestId("site-subtitle");
    await expect(subtitle).toBeVisible();
    await expect(subtitle).toHaveText(SUBTITLE);

    // DOM 位置：副标题的前一个兄弟节点就是 H1（「H1 下方一行」，不是塞在别处）。
    expect(await subtitle.evaluate((node) => node.previousElementSibling?.tagName ?? "")).toBe(
      "H1",
    );
    // 几何位置：确实落在 H1 的下方。
    const headingBox = await page
      .getByRole("heading", { name: "发现内网工具与 Skill" })
      .boundingBox();
    const subtitleBox = await subtitle.boundingBox();
    expect(headingBox !== null && subtitleBox !== null && subtitleBox.y > headingBox.y).toBe(true);

    // 登录页：站点名（卡片标题）下方一行。
    await page.goto("/login");
    await expect(page.getByTestId("login-page")).toBeVisible();
    const loginSubtitle = page.getByTestId("site-subtitle");
    await expect(loginSubtitle).toBeVisible();
    await expect(loginSubtitle).toHaveText(SUBTITLE);
    expect(
      await loginSubtitle.evaluate(
        (node) => node.previousElementSibling?.getAttribute("data-slot") ?? "",
      ),
    ).toBe("card-title");
    // 顶栏不塞副标题：登录页之外也没有把副标题塞进 header 的情况。
    await expect(page.locator('[data-testid="top-nav"] [data-testid="site-subtitle"]')).toHaveCount(
      0,
    );

    expect(errors).toEqual([]);
  });

  test("F2 · 配置后页脚出现在门户与工具详情页（contentinfo 地标 + 结构化字段 + 版本号）", async ({
    page,
  }) => {
    const errors = watchPageErrors(page);
    await setSiteConfig(page, FULL_CONFIG);

    for (const path of ["/", `/tools/${TOOL_SLUG}`]) {
      await page.goto(path);
      if (path !== "/") await expect(page.getByTestId("tool-detail")).toBeVisible();
      await expect(page.getByTestId("site-footer")).toBeVisible();

      /*
       * `<footer>` 只有落在 `<main>` **之外**时才映射 `contentinfo` 地标 ——
       * 这条断言同时守住了「语义用 <footer> + aria-label」与「渲染位置在 main 之外」。
       */
      await expect(page.getByRole("contentinfo", { name: "站点信息" })).toBeVisible();

      await expect(page.getByTestId("site-footer-org")).toHaveText(FULL_CONFIG.footer_org);
      await expect(page.getByTestId("site-footer-notice")).toHaveText(FULL_CONFIG.footer_notice);
      await expect(page.getByTestId("site-footer-version")).toHaveText("v1.0.0");
      await expect(page.getByTestId("site-footer-email")).toHaveAttribute(
        "href",
        `mailto:${FULL_CONFIG.footer_contact_email}`,
      );
      await expect(page.getByTestId("site-footer-phone")).toHaveAttribute(
        "href",
        `tel:${FULL_CONFIG.footer_contact_phone}`,
      );
    }

    expect(errors).toEqual([]);
  });

  test("F2 · 页脚不出现在登录页、个人中心与管理台（§27.4 范围裁定）", async ({ page }) => {
    const errors = watchPageErrors(page);
    await setSiteConfig(page, FULL_CONFIG);

    await page.goto("/login");
    await expect(page.getByTestId("login-page")).toBeVisible();
    await expect(page.locator("footer")).toHaveCount(0);

    // 登录后依次看门户（应有）、个人中心与管理台（都不应有）。
    await login(page, ADMIN);
    await expect(page.getByTestId("site-footer")).toBeVisible();

    await page.goto("/me");
    await expect(page.getByTestId("profile-page")).toBeVisible();
    await expect(page.locator("footer")).toHaveCount(0);

    await page.goto("/admin");
    await expect(page.getByTestId("admin-overview")).toBeVisible();
    await expect(page.locator("footer")).toHaveCount(0);

    expect(errors).toEqual([]);
  });

  test("F2 · 邮箱/电话像样时渲染 mailto:/tel: 链接", async ({ page }) => {
    const errors = watchPageErrors(page);
    await setSiteConfig(page, FULL_CONFIG);

    await page.goto("/");
    await expectMetaLoaded(page);

    const email = page.getByTestId("site-footer-email");
    const phone = page.getByTestId("site-footer-phone");
    expect(await email.evaluate((node) => node.tagName)).toBe("A");
    expect(await phone.evaluate((node) => node.tagName)).toBe("A");
    // 链接文本就是地址本身 —— 可辨识，不存在「点击这里」这类空泛文本。
    await expect(email).toHaveText(FULL_CONFIG.footer_contact_email);
    await expect(email).toHaveAccessibleName(FULL_CONFIG.footer_contact_email);
    await expect(phone).toHaveText(FULL_CONFIG.footer_contact_phone);
    await expect(phone).toHaveAccessibleName(FULL_CONFIG.footer_contact_phone);

    expect(errors).toEqual([]);
  });

  test("F2 · 窄屏（375px）下页脚不横向溢出", async ({ page }) => {
    const errors = watchPageErrors(page);
    await setSiteConfig(page, {
      footer_org: "某事业部数字化中心 · 内网工具与 Skill 共享平台运营团队",
      footer_contact_email: "tooling-support@example.com",
      footer_contact_phone: "010-8888-6666",
      footer_notice: "京ICP备 00000000 号 · 京公网安备 11000000000000 号",
    });
    await page.setViewportSize({ width: 375, height: 800 });

    await page.goto("/");
    await expect(page.getByTestId("site-footer")).toBeVisible();
    // 无法读图 ⇒ 用可计算的「有没有横向溢出」代替「看起来挤不挤」。
    const scrollWidth = await page.evaluate(() => document.documentElement.scrollWidth);
    expect(scrollWidth).toBeLessThanOrEqual(375);

    expect(errors).toEqual([]);
  });

  test("F2 · 邮箱/电话填错时退化为纯文本：不生成链接、页脚不崩", async ({ page }) => {
    const errors = watchPageErrors(page);

    /*
     * 每组都覆盖 §27.4 点名的几类「不像」：多个 `@`、**值中间的空格**、中文、
     * 缺域名点号、逗号 / 括号（`tel:` / `mailto:` 里非法）。凡是这些值，节点必须是
     * 纯文本 `SPAN` 且没有 `href`。
     *
     * 注意「空格」的边界：**首尾**空白会被 trim 后照常判为合法（见下一条用例），
     * 这里判退化的是**值内部**的空格 —— 那才是会拼出坏链接的形态。
     */
    const cases = [
      { email: "ops@@example.com", phone: "内线 8000" },
      { email: "运维@example.com", phone: "123 456" },
      { email: "not an email", phone: "电话：8000" },
      { email: "ops@example", phone: "(010)88886666" },
      { email: "ops@example.com,ops2@example.com", phone: "abc-def" },
    ] as const;

    for (const item of cases) {
      await setSiteConfig(page, {
        footer_contact_email: item.email,
        footer_contact_phone: item.phone,
      });
      await page.goto("/");
      await expectMetaLoaded(page);

      // 页脚整体照常渲染（这两个字段非空），只是链接降级了。
      await expect(page.getByTestId("site-footer")).toBeVisible();

      const email = page.getByTestId("site-footer-email");
      await expect(email).toHaveText(item.email);
      expect(await email.evaluate((node) => node.tagName), `邮箱 ${item.email}`).not.toBe("A");
      expect(await email.evaluate((node) => node.getAttribute("href"))).toBeNull();

      const phone = page.getByTestId("site-footer-phone");
      await expect(phone).toHaveText(item.phone);
      expect(await phone.evaluate((node) => node.tagName), `电话 ${item.phone}`).not.toBe("A");
      expect(await phone.evaluate((node) => node.getAttribute("href"))).toBeNull();
    }

    expect(errors).toEqual([]);
  });

  test("F2 · 邮箱/电话首尾空白被 trim（粘错一个空格不会退化成纯文本，也不会拼出坏 href）", async ({
    page,
  }) => {
    const errors = watchPageErrors(page);
    /* 这是本轮的**判断**，不是契约明文：首尾空白按「没填干净」处理，trim 后照常判定；
       值内部含空格仍然退化（上一条用例）。这里断言的是 trim 之后 href 里没有空格。 */
    await setSiteConfig(page, {
      footer_contact_email: "  ops@example.com  ",
      footer_contact_phone: "\t010-8888-6666 ",
    });

    await page.goto("/");
    await expectMetaLoaded(page);

    const email = page.getByTestId("site-footer-email");
    await expect(email).toHaveText("ops@example.com");
    await expect(email).toHaveAttribute("href", "mailto:ops@example.com");

    const phone = page.getByTestId("site-footer-phone");
    await expect(phone).toHaveText("010-8888-6666");
    await expect(phone).toHaveAttribute("href", "tel:010-8888-6666");

    expect(errors).toEqual([]);
  });

  test("F3 · 标签页标题跟随 portal.site_name；未配置（含只有空白）时用既有默认值", async ({
    page,
  }) => {
    const errors = watchPageErrors(page);
    await setSiteConfig(page, { site_name: SITE_NAME });

    for (const path of ["/", `/tools/${TOOL_SLUG}`, "/login"]) {
      await page.goto(path);
      await expect(page).toHaveTitle(SITE_NAME);
    }

    // 空白站点名 → 与 `siteName()` 一致地回落到默认值；JS 跑起来后覆盖 index.html 的静态标题。
    await setSiteConfig(page, { site_name: "   " });
    await page.goto("/");
    await expectMetaLoaded(page, DEFAULT_TITLE);
    await expect(page).toHaveTitle(DEFAULT_TITLE);

    expect(errors).toEqual([]);
  });

  test("F2 · 主题三态下页脚可读（对比度 ≥ 4.5），且颜色来自语义令牌", async ({ page }) => {
    const errors = watchPageErrors(page);
    await setSiteConfig(page, FULL_CONFIG);

    const cases = [
      { mode: "light", scheme: "light", expectDarkClass: false },
      { mode: "dark", scheme: "light", expectDarkClass: true },
      { mode: "system", scheme: "dark", expectDarkClass: true },
      { mode: "system", scheme: "light", expectDarkClass: false },
    ] as const;

    const measured: { mode: string; dark: boolean; color: string; contrast: number }[] = [];

    for (const item of cases) {
      await page.emulateMedia({ colorScheme: item.scheme });
      // 主题由 index.html 的阻塞内联脚本在首帧前应用：先在同源页面写入取值，再整页导航。
      await page.goto("/");
      await page.evaluate((mode: string) => {
        window.localStorage.setItem("localcraft-theme", mode);
      }, item.mode);
      await page.goto("/");
      await expect(page.getByTestId("site-footer")).toBeVisible();

      const observed = await page.evaluate(() => {
        const canvas = document.createElement("canvas");
        canvas.width = 1;
        canvas.height = 1;
        const context = canvas.getContext("2d", { willReadFrequently: true });
        if (!context) return null;

        /* 颜色取值用 canvas 真实渲染的像素 —— 令牌是 oklch()，手写解析容易悄悄算错。 */
        const toRgb = (cssColor: string): [number, number, number, number] => {
          context.clearRect(0, 0, 1, 1);
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

        const sample = (selector: string) => {
          const node = document.querySelector(selector);
          if (!node) return null;
          const color = window.getComputedStyle(node).color;
          const foreground = toRgb(color);
          let background: [number, number, number, number] | null = null;
          let ancestor: Element | null = node;
          while (ancestor) {
            const candidate = window.getComputedStyle(ancestor).backgroundColor;
            if (candidate && candidate !== "rgba(0, 0, 0, 0)" && candidate !== "transparent") {
              const resolved = toRgb(candidate);
              if (resolved[3] > 0) {
                background = resolved;
                break;
              }
            }
            ancestor = ancestor.parentElement;
          }
          const effective: [number, number, number, number] = background ?? [255, 255, 255, 255];
          const foregroundLuminance = luminance(foreground);
          const backgroundLuminance = luminance(effective);
          const lighter = Math.max(foregroundLuminance, backgroundLuminance);
          const darker = Math.min(foregroundLuminance, backgroundLuminance);
          return { color, contrast: (lighter + 0.05) / (darker + 0.05) };
        };

        return {
          dark: document.documentElement.classList.contains("dark"),
          org: sample('[data-testid="site-footer-org"]'),
          email: sample('[data-testid="site-footer-email"]'),
          notice: sample('[data-testid="site-footer-notice"]'),
        };
      });

      expect(observed, `${item.mode}：页脚应存在`).not.toBeNull();
      expect(observed?.dark, `${item.mode} 下 <html> 的 dark 类`).toBe(item.expectDarkClass);
      for (const [label, value] of [
        ["运营方", observed?.org],
        ["邮箱链接", observed?.email],
        ["备案号", observed?.notice],
      ] as const) {
        expect(value, `${item.mode}：${label} 元素`).not.toBeNull();
        expect(
          value?.contrast ?? 0,
          `${item.mode} 下${label}与背景的对比度（${value?.color ?? "?"}）`,
        ).toBeGreaterThanOrEqual(4.5);
      }
      measured.push({
        mode: item.mode,
        dark: observed?.dark ?? false,
        color: observed?.org?.color ?? "",
        contrast: observed?.org?.contrast ?? 0,
      });
    }

    /*
     * 令牌化的证据：浅色与深色下同一元素的计算颜色**必须不同**。
     * 只要有人把颜色写死（`text-gray-500` / 十六进制），这条就会失败。
     */
    const light = measured.find((item) => item.mode === "light");
    const dark = measured.find((item) => item.mode === "dark");
    expect(light?.color).not.toBe(dark?.color);

    expect(errors).toEqual([]);
  });
});
