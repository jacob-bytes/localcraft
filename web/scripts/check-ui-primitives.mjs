#!/usr/bin/env node
/**
 * 防回归守卫（CONTRACT §14.9）——`npx shadcn@latest add` 会把组件覆盖回
 * **React 19 写法**（ref 作为普通 prop、`cn` 来自 npm 包、sonner 依赖
 * next-themes），而本项目锁定 React 18。M1 已经踩过一次：RHF 的字段 ref 被
 * 静默丢弃、Radix 触发器无法定位。
 *
 * 这个脚本把「记得重做」变成「检查会失败」，挂在 `npm run verify` 里。
 *
 * 断言：
 *  1. 交互型原子组件里，凡是**直接渲染原生 DOM 元素**的导出组件，都必须用
 *     `React.forwardRef` 定义（React 18 下函数组件拿不到 ref）。
 *  2. `src/lib/utils.ts` 的 `cn` 是本地 clsx + tailwind-merge 实现。
 *  3. `package.json` 不存在 `cn` 与 `next-themes` 依赖。
 */

import { readFileSync, existsSync } from "node:fs";
import { dirname, join, resolve } from "node:path";
import { fileURLToPath } from "node:url";

const webRoot = resolve(dirname(fileURLToPath(import.meta.url)), "..");
const failures = [];
const notes = [];

function fail(message) {
  failures.push(message);
}

/* -------------------------------------------------------------------------- */
/* 1. 交互型原子组件必须 forwardRef                                            */
/* -------------------------------------------------------------------------- */

/**
 * 参与检查的文件（CONTRACT §14.9 点名的清单）。
 * 判定方式：把文件按顶层组件声明切段，若某段渲染了原生标签（`<div`、`<input` …）
 * 却没有 `forwardRef`，就判失败。Radix 原语的包装（`<DialogPrimitive.Root>` 等
 * 大写开头）不算原生标签，它们本身已经是 forwardRef。
 */
const PRIMITIVE_FILES = [
  "button",
  "input",
  "select",
  "checkbox",
  "sheet",
  "dialog",
  "alert-dialog",
  "dropdown-menu",
  "popover",
  "tooltip",
  // M3 新增/收编：表格、开关、页签、文本域、单选组
  "table",
  "switch",
  "tabs",
  "textarea",
  "radio-group",
];

const INTRINSIC_TAG =
  /<(a|button|div|form|img|input|label|li|ol|p|pre|section|span|textarea|ul|h[1-6])(\s|>|\/)/;

for (const name of PRIMITIVE_FILES) {
  const file = join(webRoot, "src", "components", "ui", `${name}.tsx`);
  if (!existsSync(file)) {
    fail(`缺少原子组件文件：src/components/ui/${name}.tsx`);
    continue;
  }
  const source = readFileSync(file, "utf8");

  // 以「顶层声明」为单位切片：function X( … } 或 const X = …
  const declarations = source.split(/\n(?=(?:function|const|export function|export const)\s)/);
  let intrinsicWithoutRef = 0;

  for (const block of declarations) {
    const head = block.slice(0, block.indexOf("{") === -1 ? block.length : 200);
    const nameMatch = /(?:export\s+)?(?:function|const)\s+([A-Z][A-Za-z0-9_]*)/.exec(head);
    if (!nameMatch) continue;
    if (/forwardRef/.test(block)) continue;
    const body = block;
    if (!INTRINSIC_TAG.test(body)) continue;
    // 只关心「返回原生元素」的组件：跳过仅把 children 透传给 Radix 的包装
    if (!/return \(/.test(body) && !/=> \(/.test(body)) continue;
    intrinsicWithoutRef += 1;
    fail(
      `src/components/ui/${name}.tsx 的 ${nameMatch[1]} 直接渲染原生元素但没有 forwardRef ` +
        `（React 18 会丢弃 ref —— 见 CONTRACT §14.9）`,
    );
  }

  if (intrinsicWithoutRef === 0 && !/forwardRef/.test(source)) {
    notes.push(`src/components/ui/${name}.tsx 只包装 Radix 原语，无需 forwardRef`);
  }
}

/* -------------------------------------------------------------------------- */
/* 2. cn() 必须是本地实现                                                       */
/* -------------------------------------------------------------------------- */

const utilsPath = join(webRoot, "src", "lib", "utils.ts");
if (!existsSync(utilsPath)) {
  fail("缺少 src/lib/utils.ts");
} else {
  const utils = readFileSync(utilsPath, "utf8");
  if (!/from\s+"clsx"/.test(utils) || !/from\s+"tailwind-merge"/.test(utils)) {
    fail("src/lib/utils.ts 的 cn 必须基于本地 clsx + tailwind-merge 实现");
  }
  if (/from\s+"cn"/.test(utils)) {
    fail('src/lib/utils.ts 不能从 npm 包 "cn" 导入（shadcn 新模板的写法）');
  }
}

/* -------------------------------------------------------------------------- */
/* 3. 依赖里不能出现 cn / next-themes                                          */
/* -------------------------------------------------------------------------- */

const pkg = JSON.parse(readFileSync(join(webRoot, "package.json"), "utf8"));
for (const banned of ["cn", "next-themes"]) {
  if (pkg.dependencies?.[banned] || pkg.devDependencies?.[banned]) {
    fail(`package.json 不应包含依赖 "${banned}"（见 CONTRACT §14.9）`);
  }
}

/* 组件里也不能 import 它们（依赖没了但代码还在同样会挂） */
const uiDir = join(webRoot, "src", "components", "ui");
for (const name of PRIMITIVE_FILES) {
  const file = join(uiDir, `${name}.tsx`);
  if (!existsSync(file)) continue;
  const source = readFileSync(file, "utf8");
  if (/from\s+"cn"/.test(source)) {
    fail(`src/components/ui/${name}.tsx 仍然从 npm 包 "cn" 导入`);
  }
  if (/next-themes/.test(source)) {
    fail(`src/components/ui/${name}.tsx 仍然依赖 next-themes`);
  }
}

/* -------------------------------------------------------------------------- */

if (notes.length > 0 && process.env.VERBOSE_PRIMITIVES === "1") {
  for (const note of notes) console.log(`  · ${note}`);
}

if (failures.length > 0) {
  console.error("check-ui-primitives: 失败\n");
  for (const item of failures) console.error(`  ✗ ${item}`);
  console.error(
    "\n修复方式见 CONTRACT §14.9：把被覆盖的组件改回 forwardRef，" +
      "cn 改为 @/lib/utils，sonner 改为项目 useTheme。",
  );
  process.exit(1);
}

console.log(
  `check-ui-primitives: 通过（${PRIMITIVE_FILES.length} 个交互型原子组件、cn 实现、依赖清单）`,
);
