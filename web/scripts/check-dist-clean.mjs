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
  if (/msw/i.test(text)) {
    offenders.push(`${base}（内容命中 /msw/i）`);
  }
}

if (offenders.length > 0) {
  console.error("check-dist-clean: 失败\n");
  for (const item of offenders) console.error(`  ✗ ${item}`);
  process.exit(1);
}

console.log(`check-dist-clean: 通过（dist/ 共 ${files.length} 个文件，无 msw 残留）`);
