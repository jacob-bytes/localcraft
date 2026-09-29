#!/usr/bin/env node
/**
 * M18 证据采集（CONTRACT §35.5 / M18 任务书「交付要求」2~4）。
 *
 * 打印的全是**可直接粘贴进 checkpoint 报告的事实**，不发明任何测试专用行为：
 *
 *   A. 无图详情页：图廊元素**不存在**（不是 hidden / display:none / 0 高度），
 *      且标题上方没有留白（`space-y-6` 不为一个已经不存在的兄弟节点留间距）。
 *   B. 全部图片加载失败：图廊同样消失（派发真实的 `error` 事件，与既有
 *      `m1-acceptance.spec.ts` 用例 7 同一套做法）。
 *      —— mock 模式下 MSW 跑在 Service Worker 里，Playwright 的 `page.route`
 *      拦不到它（这是仓库里已经写明的限制，见 `e2e/helpers.ts` 的注释），所以
 *      mock 侧用 `error` 事件模拟「浏览器判定加载失败」；真实的**挂起请求**
 *      证据在真机模式下用 `page.route` 不 fulfill 采集（见 C）。
 *   C. 「加载中 != 加载失败」：真机模式下把图片请求**一直挂起**，图廊必须照常
 *      渲染（主图 `complete === false`），证明不会在加载过程中先收起。
 *   D. 部分失败（mock，两张图的工具）：只让主图失败 -> 图廊**不收起**，主视图
 *      回退成占位块（§34.3 保留的行为）；再让最后一张也失败 -> 才收起。
 *   E. 卡片墙回归：`ToolCard` 的占位照旧 —— 无封面卡片有 `cover-placeholder`，
 *      且把所有 `cover-image` 打失败后，占位数量 == 卡片数量（图廊收起不影响卡片墙）。
 *
 * 用法：
 *   # mock 模式（需先 npm run dev）
 *   node scripts/collect-m18-evidence.mjs
 *   # 真机模式（**推荐**用隔离实例，见 scripts/start-isolated-backend.sh）
 *   bash scripts/start-isolated-backend.sh &          # 127.0.0.1:8010，全新 seed
 *   BASE=http://127.0.0.1:8010 node scripts/collect-m18-evidence.mjs
 *   # （也可以指向共享的 backend 实例：BASE=http://127.0.0.1:8000 …）
 *
 * 依赖 @playwright/test（devDependency）与本机 Chrome。
 */

import { mkdirSync } from "node:fs";
import { dirname, join, resolve } from "node:path";
import { fileURLToPath } from "node:url";

import { chromium } from "@playwright/test";

const webRoot = resolve(dirname(fileURLToPath(import.meta.url)), "..");
const BASE = process.env.BASE ?? "http://127.0.0.1:5173";
/** 5173 是 Vite dev（MSW mock）；任何其它端口都当作「后端托管 dist」的真机模式。 */
const REAL = process.env.MODE ? process.env.MODE === "real" : !BASE.includes(":5173");
const USERNAME = process.env.E2E_USER ?? "admin";
const PASSWORD = process.env.E2E_PASSWORD ?? "Admin@12345";
const ARTIFACTS = join(webRoot, "e2e", "artifacts");

/** 两类后端的种子不同，夹具随之不同（mock 的 id%3===0 会多出一张截图）。 */
const FIXTURES = REAL
  ? {
      noImage: "db-backup-toolkit-5e90",
      oneImage: "log-analyzer-a3f2",
      twoImages: null, // 真机种子里每个工具最多 1 张图（封面）
      noCoverCard: "db-backup-toolkit-5e90", // 真机种子里无封面的 5 个之一
    }
  : {
      noImage: "architecture-review-prompt",
      oneImage: "log-analyzer-a3f2",
      twoImages: "openapi-doc-generator", // 封面 + 截图
      noCoverCard: "code-review-skill", // mock 种子里 cover_url === null
    };

const results = [];
let failures = 0;

function line(title) {
  console.log(`\n=== ${title} ===`);
}

function check(label, ok, detail) {
  if (!ok) failures += 1;
  results.push({ label, ok, detail });
  console.log(`${ok ? "PASS" : "FAIL"}  ${label}`);
  if (detail !== undefined) console.log(`      ${JSON.stringify(detail)}`);
}

async function login(page) {
  await page.goto(`${BASE}/login`);
  await page.getByLabel("用户名", { exact: true }).fill(USERNAME);
  await page.getByLabel("密码", { exact: true }).fill(PASSWORD);
  await page.getByRole("button", { name: "登录", exact: true }).click();
  await page.waitForURL(`${BASE}/`);
}

/**
 * 量详情页主内容列与图廊的几何关系。
 * `gapAboveHeader` 是主列顶部到 `<header>` 顶部的像素距离：图廊存在时 ≈ 图廊高度 +
 * `space-y-6` 的 24px；图廊不渲染时必须是 **0**（否则就是留了空白）。
 */
async function measureDetail(page) {
  return page.evaluate(() => {
    const detail = document.querySelector('[data-testid="tool-detail"]');
    const header = detail ? detail.querySelector("header") : null;
    const main = header ? header.parentElement : null;
    const mainTop = main ? main.getBoundingClientRect().top : null;
    const headerTop = header ? header.getBoundingClientRect().top : null;
    const gallery = document.querySelector('[data-testid="tool-image-gallery"]');
    const thumbList = document.querySelector('ul[aria-label="图片缩略图"]');
    const mainImage = document.querySelector('[data-testid="gallery-main-image"]');
    return {
      galleryElements: document.querySelectorAll('[data-testid="tool-image-gallery"]').length,
      mainImageElements: document.querySelectorAll('[data-testid="gallery-main-image"]').length,
      galleryPlaceholders: document.querySelectorAll('[data-testid="gallery-placeholder"]').length,
      thumbnailLists: thumbList ? 1 : 0,
      mainColumnChildren: main
        ? Array.from(main.children).map((child) => child.tagName.toLowerCase())
        : [],
      firstChildIsHeader: main && header ? main.firstElementChild === header : null,
      gapAboveHeaderPx:
        mainTop !== null && headerTop !== null ? Math.round(headerTop - mainTop) : null,
      galleryDisplay: gallery ? getComputedStyle(gallery).display : null,
      galleryRectHeight: gallery ? Math.round(gallery.getBoundingClientRect().height) : null,
      mainImageComplete: mainImage ? mainImage.complete : null,
      mainImageNaturalWidth: mainImage ? mainImage.naturalWidth : null,
    };
  });
}

/** 右侧信息栏（§35.3⑤「布局不能塌」）：下载按钮 / 信息表 / 分享必须照旧。 */
async function measureAside(page) {
  return page.evaluate(() => {
    const aside = document.querySelector('[data-testid="tool-detail"] aside');
    if (!aside) return null;
    const rect = aside.getBoundingClientRect();
    return {
      x: Math.round(rect.x),
      width: Math.round(rect.width),
      top: Math.round(rect.top),
      downloadButtons: aside.querySelectorAll('[data-testid="download-button"]').length,
      infoRows: aside.querySelectorAll("dt").length,
      hasShareHeading: Array.from(aside.querySelectorAll("h2")).some(
        (heading) => heading.textContent?.trim() === "分享",
      ),
    };
  });
}

/** 把页面上所有详情页图廊的 `<img>` 派发一次真实的 `error` 事件。 */
async function failGalleryImages(page, { only = "all" } = {}) {
  return page.evaluate((mode) => {
    const images = Array.from(document.querySelectorAll('[data-testid="tool-image-gallery"] img'));
    const targets = mode === "first" ? images.slice(0, 1) : images;
    for (const image of targets) image.dispatchEvent(new Event("error", { bubbles: true }));
    return targets.length;
  }, only);
}

async function waitForGalleryCount(page, expected) {
  try {
    await page.waitForFunction(
      (count) => document.querySelectorAll('[data-testid="tool-image-gallery"]').length === count,
      expected,
      { timeout: 10_000 },
    );
    return true;
  } catch {
    return false;
  }
}

/**
 * 在文档脚本之前埋一个观察器：记录图廊元素**是否曾经出现在 DOM 里**。
 * abort 是瞬时的，`waitForSelector` 解析时图廊可能已经被收起 —— 只检查「现在没有」
 * 分不清「收起成功」和「页面根本没渲染」。这个探针给出「存在过 -> 消失」的实证
 * （与 `m1-acceptance.spec.ts` 的 `installFlashProbe` 同一套 MutationObserver 做法）。
 */
async function installGallerySeenProbe(page) {
  await page.addInitScript(() => {
    window.__gallerySeen = false;
    const check = () => {
      if (document.querySelector('[data-testid="tool-image-gallery"]')) {
        window.__gallerySeen = true;
      }
    };
    new MutationObserver(check).observe(document, {
      childList: true,
      subtree: true,
    });
    check();
  });
}

async function readGallerySeen(page) {
  return page.evaluate(() => window.__gallerySeen === true);
}

async function main() {
  mkdirSync(ARTIFACTS, { recursive: true });
  console.log(`M18 证据采集 —— BASE=${BASE} 模式=${REAL ? "真实后端" : "MSW mock"}`);
  console.log(`夹具：无图=${FIXTURES.noImage} 有图=${FIXTURES.oneImage} 两图=${FIXTURES.twoImages}`);

  const browser = await chromium.launch({ channel: "chrome" });
  const context = await browser.newContext({
    baseURL: BASE,
    viewport: { width: 1440, height: 900 },
    locale: "zh-CN",
  });
  const page = await context.newPage();
  await login(page);

  /* ------------------------------------------------------------------ A */
  line("A. 无图详情页：图廊「真收起」");
  await page.goto(`${BASE}/tools/${FIXTURES.noImage}`);
  await page.waitForSelector('[data-testid="tool-detail"]');
  const noImage = await measureDetail(page);
  const noImageAside = await measureAside(page);
  check("A1 图廊根元素不存在（DOM count == 0）", noImage.galleryElements === 0, noImage);
  check("A2 主图 / 占位块 / 缩略图列表都不存在", noImage.mainImageElements === 0 && noImage.galleryPlaceholders === 0 && noImage.thumbnailLists === 0);
  check("A3 标题上方没有留白（gapAboveHeaderPx == 0）", noImage.gapAboveHeaderPx === 0);
  check("A4 主列第一个孩子就是标题（没有空盒子占位）", noImage.firstChildIsHeader === true);
  await page.screenshot({ path: join(ARTIFACTS, "m18-a-no-image.png"), fullPage: true });

  /* 对照组：有图时图廊确实在，且标题确实在图廊之后。 */
  await page.goto(`${BASE}/tools/${FIXTURES.oneImage}`);
  await page.waitForSelector('[data-testid="tool-detail"]');
  await page.waitForSelector('[data-testid="gallery-main-image"]');
  const withImage = await measureDetail(page);
  const withImageAside = await measureAside(page);
  check(
    "A5 对照组（有图）：图廊存在、标题不在第一位、上方留白 > 0",
    withImage.galleryElements === 1 && withImage.firstChildIsHeader === false && withImage.gapAboveHeaderPx > 0,
    withImage,
  );
  check(
    "A6 右侧信息栏照旧：下载按钮 / 信息表 / 分享都在（§35.3⑤）",
    noImageAside?.downloadButtons === 1 &&
      noImageAside.infoRows >= 5 &&
      noImageAside.hasShareHeading === true,
    { noImage: noImageAside, withImage: withImageAside },
  );
  check(
    "A7 图廊收起不影响信息栏位置（同一栅格：x / width 与有图时一致）",
    noImageAside !== null &&
      withImageAside !== null &&
      noImageAside.x === withImageAside.x &&
      noImageAside.width === withImageAside.width,
    { noImage: noImageAside, withImage: withImageAside },
  );

  /* ------------------------------------------------------------------ B */
  line("B. 全部图片加载失败 -> 同样收起");
  const failedCount = await failGalleryImages(page);
  const collapsed = await waitForGalleryCount(page, 0);
  const afterAllFailed = await measureDetail(page);
  check(
    "B1 每张图都失败后图廊消失（count == 0）",
    collapsed && afterAllFailed.galleryElements === 0,
    { dispatchedErrorEvents: failedCount, ...afterAllFailed },
  );
  check("B2 收起后标题回到主列第一位、上方留白 0", afterAllFailed.firstChildIsHeader === true && afterAllFailed.gapAboveHeaderPx === 0);
  await page.screenshot({ path: join(ARTIFACTS, "m18-b-all-failed.png"), fullPage: true });

  /* ------------------------------------------------------------------ D */
  if (FIXTURES.twoImages) {
    line("D. 部分失败不收起（mock：两张图的工具）");
    await page.goto(`${BASE}/tools/${FIXTURES.twoImages}`);
    await page.waitForSelector('[data-testid="gallery-main-image"]');
    const beforePartial = await measureDetail(page);
    const firstFailed = await failGalleryImages(page, { only: "first" });
    await page.waitForTimeout(300);
    const afterPartial = await measureDetail(page);
    check(
      "D1 只有主图失败时图廊仍在（不提前收起），主视图回退成占位块",
      afterPartial.galleryElements === 1 &&
        afterPartial.galleryPlaceholders === 1 &&
        afterPartial.thumbnailLists === 1,
      { before: beforePartial, dispatched: firstFailed, after: afterPartial },
    );
    const restFailed = await failGalleryImages(page);
    const collapsedPartial = await waitForGalleryCount(page, 0);
    check(
      "D2 最后一张也失败后才收起",
      collapsedPartial && restFailed > 0,
      await measureDetail(page),
    );
  }

  /* ------------------------------------------------------------------ E */
  line("E. 卡片墙占位不动（回归）");
  await page.goto(`${BASE}/`);
  await page.waitForSelector('[data-testid="tool-grid"] [data-testid="tool-card"]');
  const wallBefore = await page.evaluate((expectedSlug) => {
    const grid = document.querySelector('[data-testid="tool-grid"]');
    const cardOf = (slug) => {
      const card = grid.querySelector(`[data-tool-slug="${slug}"]`);
      if (!card) return null;
      return {
        slug,
        coverImages: card.querySelectorAll('[data-testid="cover-image"]').length,
        coverPlaceholders: card.querySelectorAll('[data-testid="cover-placeholder"]').length,
      };
    };
    const firstPlaceholderCard = grid
      .querySelector('[data-testid="cover-placeholder"]')
      ?.closest('[data-testid="tool-card"]');
    return {
      cards: grid.querySelectorAll('[data-testid="tool-card"]').length,
      coverImages: grid.querySelectorAll('[data-testid="cover-image"]').length,
      coverPlaceholders: grid.querySelectorAll('[data-testid="cover-placeholder"]').length,
      expectedNoCoverCard: cardOf(expectedSlug),
      sampleNoCoverCard: firstPlaceholderCard
        ? {
            slug: firstPlaceholderCard.getAttribute("data-tool-slug"),
            coverImages: firstPlaceholderCard.querySelectorAll('[data-testid="cover-image"]').length,
            coverPlaceholders: firstPlaceholderCard.querySelectorAll(
              '[data-testid="cover-placeholder"]',
            ).length,
          }
        : null,
    };
  }, FIXTURES.noCoverCard);
  check(
    "E1 每张卡片恰好一个盒子：cover-image + cover-placeholder == 卡片数",
    wallBefore.coverImages + wallBefore.coverPlaceholders === wallBefore.cards,
    {
      cards: wallBefore.cards,
      coverImages: wallBefore.coverImages,
      coverPlaceholders: wallBefore.coverPlaceholders,
    },
  );
  check(
    "E2 无封面卡片仍在渲染占位块（没有 cover-image）",
    wallBefore.expectedNoCoverCard?.coverPlaceholders === 1 &&
      wallBefore.expectedNoCoverCard?.coverImages === 0 &&
      wallBefore.sampleNoCoverCard?.coverPlaceholders === 1 &&
      wallBefore.sampleNoCoverCard?.coverImages === 0,
    {
      expected: wallBefore.expectedNoCoverCard,
      sampleFromDom: wallBefore.sampleNoCoverCard,
    },
  );
  await page.evaluate(() => {
    for (const image of document.querySelectorAll('[data-testid="cover-image"]')) {
      image.dispatchEvent(new Event("error", { bubbles: true }));
    }
  });
  await page.waitForTimeout(300);
  const wallAfter = await page.evaluate(() => {
    const grid = document.querySelector('[data-testid="tool-grid"]');
    return {
      cards: grid.querySelectorAll('[data-testid="tool-card"]').length,
      coverImages: grid.querySelectorAll('[data-testid="cover-image"]').length,
      coverPlaceholders: grid.querySelectorAll('[data-testid="cover-placeholder"]').length,
    };
  });
  check(
    "E3 卡片墙封面全部失败 -> 全部回退占位（占位数 == 卡片数，图廊的收起逻辑不外溢）",
    wallAfter.coverImages === 0 && wallAfter.coverPlaceholders === wallAfter.cards,
    wallAfter,
  );

  /* ------------------------------------------------------------------ C */
  if (REAL) {
    line("C. 加载中 != 加载失败（真机：把图片请求一直挂起）");
    const pendingPage = await context.newPage();
    await installGallerySeenProbe(pendingPage);
    await pendingPage.route("**/api/v1/images/**", () => {
      /* 故意不 continue / fulfill / abort：请求永远处于 pending。 */
    });
    await pendingPage.goto(`${BASE}/tools/${FIXTURES.oneImage}`);
    await pendingPage.waitForSelector('[data-testid="gallery-main-image"]');
    await pendingPage.waitForTimeout(2000);
    const pending = await measureDetail(pendingPage);
    const pendingGallerySeen = await readGallerySeen(pendingPage);
    check(
      "C1 请求挂起（加载中）时图廊照常渲染，主图 complete === false",
      pendingGallerySeen &&
        pending.galleryElements === 1 &&
        pending.mainImageElements === 1 &&
        pending.mainImageComplete === false &&
        pending.mainImageNaturalWidth === 0 &&
        pending.galleryPlaceholders === 0,
      { gallerySeenEver: pendingGallerySeen, ...pending },
    );
    await pendingPage.close();

    const failedPage = await context.newPage();
    await installGallerySeenProbe(failedPage);
    let abortedImageRequests = 0;
    failedPage.on("requestfailed", (request) => {
      if (request.url().includes("/api/v1/images/")) abortedImageRequests += 1;
    });
    await failedPage.route("**/api/v1/images/**", (route) => route.abort("failed"));
    await failedPage.goto(`${BASE}/tools/${FIXTURES.oneImage}`);
    const failedCollapsed = await waitForGalleryCount(failedPage, 0);
    await failedPage.waitForTimeout(500);
    const failedAfter = await measureDetail(failedPage);
    const failedGallerySeen = await readGallerySeen(failedPage);
    check(
      "C2 同样的页面 + 同样的夹具，只有网络结果不同：请求 abort（真失败）后图廊消失",
      failedGallerySeen &&
        failedCollapsed &&
        failedAfter.galleryElements === 0 &&
        failedAfter.firstChildIsHeader === true &&
        failedAfter.gapAboveHeaderPx === 0 &&
        abortedImageRequests > 0,
      {
        gallerySeenEver: failedGallerySeen,
        afterFailure: failedAfter,
        abortedImageRequests,
      },
    );
    await failedPage.close();
  } else {
    line("C. 加载中 != 加载失败（mock 说明）");
    console.log(
      "   mock 的图片由 MSW Service Worker 应答，`page.route` 拦不到（仓库既有结论），\n" +
        "   因此「请求挂起时照常渲染」这条只在真机模式采集：\n" +
        "   BASE=http://127.0.0.1:8010 node scripts/collect-m18-evidence.mjs（隔离实例）\n" +
        "   mock 侧由 D1（只有主图失败时图廊不收起）间接证明「未全部失败就不收起」。",
    );
  }

  await browser.close();

  line("汇总");
  console.log(JSON.stringify({ base: BASE, mode: REAL ? "real" : "mock", failures, checks: results }, null, 2));
  console.log(`\n结论：${failures === 0 ? "全部证据成立" : `${failures} 条证据不成立`}`);
  process.exitCode = failures === 0 ? 0 : 1;
}

await main();
