import { expect, test, type Page } from "@playwright/test";

import { ARTIFACTS, contrastOf, login, watchPageErrors } from "./helpers";

/**
 * 可访问性条款的可执行版：契约 **§34.5①**（对比度）+ **WCAG 2.5.8**（命中区）。
 *
 * ## 为什么单独一个 spec（不挂在某个里程碑下）
 *
 * 它守的是**条款**，不是某次交付。在此之前：
 *   - §34.5① 只体现在几个里程碑 spec 的**定点**断言里（M9 / M12 / M14 各量几个元素，
 *     那几处依然保留）。定点断言能防「已知元素回归」，但**发现不了新加的元素**；
 *   - WCAG 2.5.8 的命中区（`docs/12 §U3`）**一条检查都没有** —— `UserTable` 早就把
 *     命中区从 16×16 撑到 32×32（`M7 · F2`），但 `docs/12` 的状态表还写着"待做"，
 *     而且没有任何东西防它被改回去。
 *
 * ## 三条对比度检查的分工（以及为什么非文本那条只能是策展清单）
 *
 * 1. **通用文本扫描**（自动、无清单）：遍历 `main *`，对每个直接含文本节点的元素量
 *    「`color` vs 祖先链上第一个不透明背景」，按字号/字重取 4.5:1 或 3:1。
 * 2. **非管理页深色「近白大块」**：管理台 13 页的同名检查在 `m3-admin.spec.ts`，这里补非管理页。
 * 3. **非文本图形**（策展清单）：**不能**通用扫 —— 「哪些图标算有意义」无法机械判定，
 *    装饰性图标按 WCAG 明确豁免（本项目里它们都带 `aria-hidden`），
 *    通用扫只会产出大量"合规但不该管"的告警，最终把人练成无视失败。
 *
 * 通用扫描**跳过**两类元素，理由都是"背景无法可靠确定"：
 *   - 祖先链上有 `background-image`（渐变 / 图片）；
 *   - 祖先链上有 `opacity < 1`（叠加出来的实际颜色取决于背后是什么）。
 *
 * ## 踩过的坑（写在这里免得后人重踩）
 *
 * 背景解析**必须从元素自身开始**向上找，不能从 `parentElement` 开始 ——
 * 后者会跳过元素**自己的**背景，于是「主按钮里近白色的文字 vs 页面底色」被算成 1.01:1，
 * 一次误报 28 处。第一版就是这么写的。
 */

/** 通用扫描覆盖的页面。管理台 13 页 + 门户/工作区，都在登录后可达。 */
const PAGES = [
  "/",
  "/me/tools",
  "/me/favorites",
  "/admin",
  "/admin/approvals",
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

/** 非管理页 —— 深色「近白大块」扫描原先只覆盖管理台 13 页，这里补齐。 */
const PUBLIC_AND_ME_PAGES = ["/", "/me/tools", "/me/favorites", "/definitely-not-a-page"] as const;

type Violation = {
  tag: string;
  cls: string;
  text: string;
  color: string;
  bg: string;
  contrast: number;
  required: number;
  px: number;
};

/**
 * 通用文本对比度扫描。返回违规项（空数组 = 通过）。
 *
 * 与 `helpers.ts` 的 `contrastOf` 同一套公式：颜色一律交给 canvas 真实渲染后读像素
 * （令牌是 `oklch(...)`，Chrome 原样返回该语法，不能手写解析）。
 */
const SWEEP_TEXT_CONTRAST = (): Violation[] => {
  const canvas = document.createElement("canvas");
  const context = canvas.getContext("2d", { willReadFrequently: true });
  if (!context) return [];

  const toRgb = (css: string): [number, number, number, number] => {
    context.clearRect(0, 0, 1, 1);
    context.fillStyle = "#010203"; // 哨兵：解析失败时不会与目标色混淆
    context.fillStyle = css;
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
  const opaque = (css: string | undefined): [number, number, number, number] | null => {
    if (!css || css === "transparent" || css === "rgba(0, 0, 0, 0)") return null;
    const resolved = toRgb(css);
    return resolved[3] > 0 ? resolved : null;
  };

  const out: Violation[] = [];
  const seen = new Set<string>();

  for (const element of Array.from(document.querySelectorAll<HTMLElement>("main *"))) {
    // 只量**直接**含文本节点的元素：否则父元素会把子元素的文本算到自己头上，
    // 量出的对比度对应不到真正的渲染对象。
    const text = Array.from(element.childNodes)
      .filter((node) => node.nodeType === Node.TEXT_NODE)
      .map((node) => node.textContent?.trim() ?? "")
      .join("")
      .trim();
    if (!text) continue;
    if (element.closest('[aria-hidden="true"]')) continue;

    let undecidable = false;
    for (let node: HTMLElement | null = element; node; node = node.parentElement) {
      const style = getComputedStyle(node);
      if (style.backgroundImage && style.backgroundImage !== "none") {
        undecidable = true;
        break;
      }
      if (Number(style.opacity) < 1) {
        undecidable = true;
        break;
      }
    }
    if (undecidable) continue;

    const style = getComputedStyle(element);
    if (style.visibility === "hidden" || style.display === "none") continue;
    const rect = element.getBoundingClientRect();
    if (rect.width < 1 || rect.height < 1) continue;

    // ★ 从元素**自身**开始：元素自己就可能有背景（主按钮、徽标）。
    let background: [number, number, number, number] | null = null;
    for (let node: HTMLElement | null = element; node && !background; node = node.parentElement) {
      background = opaque(getComputedStyle(node).backgroundColor);
    }
    const bg = background ?? [255, 255, 255, 255];

    const fgLuminance = luminance(toRgb(style.color));
    const bgLuminance = luminance(bg);
    const contrast =
      (Math.max(fgLuminance, bgLuminance) + 0.05) / (Math.min(fgLuminance, bgLuminance) + 0.05);

    const px = Number.parseFloat(style.fontSize);
    const weight = Number(style.fontWeight) || 400;
    // WCAG：≥24px，或 ≥18.66px 且加粗 → 算大字号，门槛 3:1
    const large = px >= 24 || (px >= 18.66 && weight >= 700);
    const required = large ? 3 : 4.5;

    if (contrast < required) {
      const key = `${element.tagName}|${style.color}|${Math.round(px)}|${text.slice(0, 20)}`;
      if (seen.has(key)) continue;
      seen.add(key);
      out.push({
        tag: element.tagName,
        cls: String(element.className).slice(0, 60),
        text: text.slice(0, 30),
        color: style.color,
        bg: `rgb(${bg.slice(0, 3).join(",")})`,
        contrast: Math.round(contrast * 100) / 100,
        required,
        px: Math.round(px),
      });
    }
  }
  return out;
};

/** 把主题切到指定值并等 `<html>` 真的带上（或去掉）dark 类。 */
async function applyTheme(page: Page, theme: "light" | "dark"): Promise<void> {
  await page.evaluate((value) => window.localStorage.setItem("localcraft-theme", value), theme);
  await page.reload({ waitUntil: "networkidle" });
  if (theme === "dark") {
    await expect(page.locator("html")).toHaveClass(/dark/);
  } else {
    await expect(page.locator("html")).not.toHaveClass(/dark/);
  }
}

test.describe("契约 §34.5①：对比度是可执行的检查", () => {
  test("全站文本在两种主题下都达到 WCAG AA（通用扫描，不看清单）", async ({ page }) => {
    const errors = watchPageErrors(page);
    await login(page);

    const report: string[] = [];

    for (const theme of ["light", "dark"] as const) {
      await applyTheme(page, theme);

      for (const path of PAGES) {
        await page.goto(path, { waitUntil: "networkidle" });
        await page.waitForTimeout(150); // 等懒加载组件把文本渲染出来

        const violations = await page.evaluate(SWEEP_TEXT_CONTRAST);
        for (const v of violations) {
          report.push(
            `[${theme}] ${path} 需要 ${v.required}:1，实测 ${v.contrast}:1` +
              `（${v.px}px <${v.tag}> 前景 ${v.color} / 背景 ${v.bg}）` +
              ` 文本「${v.text}」 class="${v.cls}"`,
          );
        }
      }
    }

    expect(
      report,
      "这些文本的对比度低于契约 §34.5① 的门槛。注意：改法优先用语义令牌或\n" +
        "「浅色档 + dark: 深色档」的成对写法；调色板前景色必须两态分别验证。",
    ).toEqual([]);
    expect(errors).toEqual([]);
  });

  test("非管理页在深色下没有接近纯白的大块背景（管理台那一条的补齐）", async ({ page }) => {
    /*
     * 管理台的同一检查在 `m3-admin.spec.ts`（13 个页面）。这里补**非管理页**：
     * 门户、我的工具、我的收藏、工具详情、404。
     * 判据同前：深色下元素背景 r,g,b 都 > 240 且高度 > 40 → 就是漏了 dark 变体的大块浅色。
     */
    const errors = watchPageErrors(page);
    await login(page);
    await applyTheme(page, "dark");

    // 详情页需要一个真实 slug：直接从门户卡片里取（不硬编码，种子数据变了也不会碎）
    await page.goto("/", { waitUntil: "networkidle" });
    const href = await page
      .locator('a[href^="/tools/"]')
      .first()
      .getAttribute("href")
      .catch(() => null);
    const pages = [...PUBLIC_AND_ME_PAGES, ...(href ? [href] : [])];

    for (const path of pages) {
      await page.goto(path, { waitUntil: "networkidle" });
      await page.waitForTimeout(200);

      const offending = await page.evaluate(() => {
        const found: string[] = [];
        for (const element of Array.from(document.querySelectorAll<HTMLElement>("main *"))) {
          const style = getComputedStyle(element);
          const match = /rgba?\((\d+),\s*(\d+),\s*(\d+)/.exec(style.backgroundColor);
          if (!match) continue;
          const [r, g, b] = [Number(match[1]), Number(match[2]), Number(match[3])];
          if (r > 240 && g > 240 && b > 240 && element.offsetHeight > 40) {
            found.push(`${element.tagName}.${String(element.className).slice(0, 40)}`);
          }
        }
        return found.slice(0, 3);
      });

      expect(offending, `${path} 深色下出现接近纯白的大块背景`).toEqual([]);
      await page.screenshot({
        path: `${ARTIFACTS}/a11y-dark-${path.replaceAll("/", "-").replace(/^-/, "") || "root"}.png`,
        fullPage: true,
      });
    }
    expect(errors).toEqual([]);
  });

  test("权限提示图标 ≥3:1（非文本图形，两态都要过）", async ({ page }) => {
    /*
     * 这个图标原先是 `text-amber-500`：浅色下 **2.1:1**（不合规），
     * 且是全站最后一个不带 `dark:` 的调色板前景色。
     *
     * 它现在放在策展清单里而不是靠通用扫描 —— 通用扫描只量文本节点，
     * 抓不到「用 `currentColor` 填充的 SVG 图标」。
     */
    await login(page);

    for (const theme of ["light", "dark"] as const) {
      await applyTheme(page, theme);
      await page.goto("/admin/tokens", { waitUntil: "networkidle" });

      // 图标在「签发 Token」对话框里，页面上是空状态
      await page.getByRole("button", { name: "签发 Token" }).first().click();
      await expect(page.getByTestId("token-admin-all-hint-icon")).toBeVisible();

      const reading = await contrastOf(page, '[data-testid="token-admin-all-hint-icon"]');
      expect(reading, `${theme}：图标应可量到颜色`).not.toBeNull();
      expect(
        reading?.contrast ?? 0,
        `${theme} 权限提示图标对比度（前景 ${reading?.foreground ?? "?"} / 背景 ${
          reading?.background ?? "?"
        }）—— 非文本图形要求 ≥3:1`,
      ).toBeGreaterThanOrEqual(3);

      await page.keyboard.press("Escape");
    }
  });

  test("管理台行选择 checkbox 的命中区 ≥24×24（WCAG 2.5.8 / docs/12 §U3）", async ({ page }) => {
    /*
     * 背景：视觉上的 checkbox 是 16×16，WCAG 2.5.8「Target Size (Minimum)」要求命中区
     * 至少 24×24。`UserTable` 用外层 `<label className="-m-2 … size-8">` 把真实命中区
     * 撑到 **32×32**（`-m-2` 抵消多出的边距，视觉尺寸与表格布局都不变）。
     *
     * ★ 但 `docs/12 §U3` 的状态表长期写着"待做"，且**没有任何检查**防它被改回去 ——
     * 这个改动只靠一行注释活着。这条测试就是那个缺失的守卫：
     * 量的是 **label 的 bounding box**（真实可点区域），不是 checkbox 的绘制尺寸。
     */
    await login(page);
    await page.goto("/admin/users", { waitUntil: "networkidle" });
    await expect(page.getByTestId("user-row").first()).toBeVisible();

    const targets = [
      ["表头全选", page.locator('label:has([aria-label$="本页用户"])').first()],
      ["行选择", page.locator('label:has([aria-label^="选择用户"])').first()],
    ] as const;

    for (const [label, locator] of targets) {
      const box = await locator.boundingBox();
      expect(box, `${label} 应当能量到命中区`).not.toBeNull();
      // 24 是 WCAG 2.5.8 的底线；本项目实测是 32 —— 留出 24 的底线是为了
      // 让"稍微收紧一点"不会立刻变红，而"退回 16×16"一定会红。
      expect(
        Math.round(box?.width ?? 0),
        `${label} 命中区宽度（当前 ${Math.round(box?.width ?? 0)}px）`,
      ).toBeGreaterThanOrEqual(24);
      expect(
        Math.round(box?.height ?? 0),
        `${label} 命中区高度（当前 ${Math.round(box?.height ?? 0)}px）`,
      ).toBeGreaterThanOrEqual(24);
    }
  });
});
