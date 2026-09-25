import { defineConfig, devices } from "@playwright/test";

/**
 * **真实后端** E2E（CONTRACT §14.1 / M2 裁定 6）。
 *
 * 与 `playwright.config.ts`（MSW mock）的区别，正是这个套件存在的理由 ——
 * mock 永远测不到的东西：
 *   - refresh cookie 的真实 `Path=/api/v1/auth`
 *   - 登出 / 改密成功的真实响应形状（`200` + `{"status":"ok"}`，不是 204）
 *   - `facets` 翻页时的真实 `null` 序列化
 *   - 真实 404 语义（无权访问返回 404 而不是 403）
 *   - 真实 SQL 分页/筛选/排序行为
 *   - 图片签名 URL、下载票据与真实落盘文件的 SHA256
 *
 * 拓扑与生产一致：后端（8000）托管 `web/dist`，前端不经过 Vite。
 * 因此**必须先构建**：`VITE_ENABLE_MOCKS=false npm run build`（`.env.production` 已保证）。
 *
 * 默认不启动 webServer：后端由外部启动（见 `scripts/start-isolated-backend.sh`）。
 * 需要指向别的实例时用环境变量：
 *   REAL_BASE=http://127.0.0.1:8010 npx playwright test --config playwright.real.config.ts
 */
const baseURL = process.env.REAL_BASE ?? "http://127.0.0.1:8000";

export default defineConfig({
  testDir: "./e2e-real",
  timeout: 90_000,
  fullyParallel: false,
  workers: 1,
  retries: 0,
  reporter: [["list"]],
  outputDir: "e2e-real/artifacts/test-output",
  // M4（CONTRACT §19.9）：路由级懒加载失败时 `lazyWithRetry` 会整页重载一次，
  // 断言必须容得下这次重载 + 重新渲染，所以 expect 窗口从 15s 提到 30s。
  // 这不是放宽断言 —— 「重载后仍未渲染出目标元素」依然会失败。
  expect: { timeout: 30_000 },
  use: {
    baseURL,
    trace: "off",
    screenshot: "only-on-failure",
    viewport: { width: 1440, height: 900 },
    locale: "zh-CN",
    acceptDownloads: true,
  },
  projects: [
    {
      name: "real-backend",
      // 本机 Chrome：沙箱无法下载 Playwright 自带浏览器（与 mock 套件一致）。
      use: { ...devices["Desktop Chrome"], channel: "chrome" },
    },
  ],
  metadata: { baseURL, mode: "real-backend" },
});
