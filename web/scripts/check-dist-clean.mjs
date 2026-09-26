#!/usr/bin/env node
/**
 * 生产产物洁净度守卫（CONTRACT §11 #11）。
 *
 * `web/dist` 里不允许出现任何 `msw` 字样，也不允许残留
 * `mockServiceWorker.js` —— 这是会被 checkpoint 直接拦下的硬伤。
 * 挂在 `npm run verify` 的末尾（构建之后）。
 */

import { readdirSync, readFileSync, statSync, existsSync } from "node:fs";
import { dirname, join, resolve } from "node:path";
import { fileURLToPath } from "node:url";

const webRoot = resolve(dirname(fileURLToPath(import.meta.url)), "..");
const distDir = join(webRoot, "dist");

if (!existsSync(distDir)) {
  console.error("check-dist-clean: 失败 —— dist/ 不存在，请先 npm run build");
  process.exit(1);
}

/** 递归收集所有文件（跳过二进制图片，只扫文本型产物）。 */
function walk(dir) {
  const out = [];
  for (const entry of readdirSync(dir, { withFileTypes: true })) {
    const full = join(dir, entry.name);
    if (entry.isDirectory()) out.push(...walk(full));
    else out.push(full);
  }
  return out;
}

/**
 * 内容里的 `msw` 必须是**独立出现**的（前后不能是字母/数字/下划线）。
 *
 * 为什么加这个边界（M8 实测的误报）：Vite 的 chunk 文件名形如
 * `ConfirmDialog-DMSWScg1.js`，那 8 位 base64 内容哈希**可能**含 `MSW`，而这些
 * 文件名会作为字符串出现在其它 chunk 的内容里（import 图 / 预加载清单）。旧的
 * `/msw/i` 会因此把**完全不含 MSW 代码**的构建判成失败，让 `verify` 随机卡住。
 * （具体命中个数随内容哈希而变，**不可复现**，所以这里不写数字。）
 *
 * 覆盖面由**两条互补的检查**共同保证，缺一不可：
 *
 *  1. **内容正则**（本常量）：负责 MSW 以独立 token 出现在**文件内容**里的形态 ——
 *     `"msw"`、`msw/browser`、`msw@x.y.z`、`require("msw")`。这些形态前后一定不是
 *     `[A-Za-z0-9_]`，所以边界不会放过它们；而 `DMSWScg1` 这类被更长单词包住的
 *     哈希片段则不再误报。
 *  2. **文件名检查**（下面遍历里的 `/mockServiceWorker/i`）：负责 worker 文件
 *     本身。它的依据是**路径**（内容正则只看得到文件内容，看不到名字），所以无论
 *     MSW 生成的 worker 内容长什么样，这个文件名一进产物就会被拦下并明确指出
 *     「MSW service worker 被打进产物」。
 *
 * 注意两者的**互补**方向：`mockServiceWorker` 这个**词**不含 `msw` 子串
 * （m-o-c-k-S-e-r-v-i-c-e…），所以内容正则永远命中不了它 —— 文件名检查管的是
 * 「名字」，内容检查管的是「被打进 bundle 的 MSW 代码与字符串」。实际 MSW 2.x 的
 * `mockServiceWorker.js` 内容里**另有**独立的 `msw` token（`msw/passthrough`、
 * `mswjs/msw`），该文件若落进 `dist/` 两条都会命中；但那是 MSW 生成内容的细节，
 * 不能当作防线，文件名检查才是对 worker 的稳定防线。两条都保留，不要合并。
 */
const MSW_IN_CONTENT = /(?<![A-Za-z0-9_])msw(?![A-Za-z0-9_])/i;

const files = walk(distDir);
const offenders = [];

for (const file of files) {
  const base = file.slice(distDir.length + 1);
  if (/mockServiceWorker/i.test(base)) {
    offenders.push(`${base}（MSW service worker 被打进产物）`);
    continue;
  }
  const size = statSync(file).size;
  if (size > 4 * 1024 * 1024) continue; // 大文件（语言语法包等）跳过，避免无谓读取
  let text;
  try {
    text = readFileSync(file, "utf8");
  } catch {
    continue;
  }
  if (MSW_IN_CONTENT.test(text)) {
    offenders.push(`${base}（内容命中 MSW 标记）`);
  }
}

if (offenders.length > 0) {
  console.error("check-dist-clean: 失败\n");
  for (const item of offenders) console.error(`  ✗ ${item}`);
  process.exit(1);
}

console.log(`check-dist-clean: 通过（dist/ 共 ${files.length} 个文件，无 msw 残留）`);
