# 前端开发 Agent — 任务书（M7 · 体验打磨）

## 你的身份与边界

你是 **localcraft 平台的前端开发 agent**。你独占并只允许修改一个目录：

- `web/`

**你绝对不可以修改**：`docs/`、`contracts/`、`README.md`、`backend/`、`deploy/`、`scripts/`。
后端由另一个 agent 并行开发，你去动 `backend/` 会直接造成冲突。

**不要执行任何 git 命令**（commit / branch / checkout / stash 一个都不要）。版本控制由监控方处理。

工作目录：`/Users/jlthzy/Documents/selftool`

---

## 第一步：先读这些

1. `contracts/CONTRACT.md` —— §3.1「access token 只放内存」是硬要求，本次新增 localStorage 用法时**不要**触碰它。
2. `docs/12-前端UI-UX审查.md` —— §5 修复清单、§6.2 方向表。**本文是你的主要依据**，U3/V1/V2/V3/V8 的原始分析都在里面。
3. `docs/04-前端页面与交互清单.md` —— §3 设计令牌、§5 布局、§8 无障碍与响应式。
4. `docs/00-关键决策.md` —— 看清「明确不做的事」，尤其 D9（不做大屏）与 D10（不做统计图表）。

---

## 本轮的 6 项任务

### F1 · 顶栏搜索框改为居右（**用户直接提出**）

现状：`web/src/components/layout/TopNav.tsx:49` 是

```tsx
<div className="ml-2 flex flex-1 justify-start sm:justify-center">
  <GlobalSearch />
</div>
```

即搜索触发器被 `flex-1` + `sm:justify-center` 顶到了正中间。用户要求**居右**。

**监控方裁定（照做，不要自行发挥）**：

1. 搜索触发器移入右侧集群，排在 `ThemeToggle` **左侧**。视觉顺序为
   `[logo] …(留白)… [搜索] [主题切换] [登录/用户菜单]`
2. **必须处理窄屏 `w-full`**：`GlobalSearch.tsx:72` 现在是
   `h-9 w-full justify-start gap-2 px-3 text-muted-foreground sm:w-64 lg:w-80`。
   `w-full` 在移动端一旦不再居中就会把 logo 和右侧图标挤爆。
   改为窄屏图标按钮（无文字、无 `⌘K` 提示、`aria-label` 保留），`sm` 起恢复带文字的搜索框。
   **可访问名不能丢**，图标态也必须能被读屏软件识别。
3. `minimal` 模式（强制改密）下搜索块被 `Badge` 取代。该分支现在是 `ml-1`，
   移动后要保证 Badge 与右侧集群的间距仍然正确，不要留下一个孤立的左对齐徽标。
4. 改动后 **light / dark / system 三态 + 320 / 375 / 768 / 1280 四档宽度**都要看一眼，
   不允许出现横向滚动条或元素重叠。

### F2 · U3 表格 checkbox 命中区

`docs/12` §U3：`web/src/components/admin/UserTable.tsx`（及同类管理台表格）的行选择
checkbox 真实命中区只有 16×16。用 `p-2 -m-2` 或包 `<label>` 扩到 **≥32×32**，
**视觉尺寸不变**（仍是 16×16）。逐个检查所有带选择框的管理台表格，不要只改一处。

### F3 · V1 无封面卡片占位再设计

现在是「分类色块 + 类型图标」。改为**按分类的柔和渐变 + 大号类型图标**，
整体亮度/饱和度要低，保证白字或深字在两种主题下都过 WCAG AA。

**硬约束**：不得引入新依赖；不得请求远程图片或字体（内网可能完全离线，见 `docs/00` D24）。

### F4 · V2 列表视图与卡片视图的差异化

让两种视图真正不同：

- **列表视图** → 表格式、可扫读：名称 / 分类 / 类型 / 版本 / 下载数 / 更新时间
- **卡片视图** → 保持封面浏览、低信息密度

**硬约束**：**只允许复用列表响应里已有的字段，不得要求后端加接口或加字段。**
若某个字段现在不在列表响应里，就换一个已有的，或不做这一列 —— 在报告里说明取舍。

### F5 · V3 空态插画

给三类空态各配一个**内联 SVG**（不引入图片资源）：无搜索结果 / 无工具 / 无待审。
SVG 加 `aria-hidden="true"`，**文案必须仍然在 DOM 里**（不要用图形替掉文字）。

### F6 · V8 ⌘K 命令面板增强

`web/src/components/layout/GlobalSearch.tsx`：加分组标题与**最近访问**。

**监控方裁定**：

- 最近访问存 `localStorage`，键名 `localcraft:recent-tools`，**最多 8 条**，只存
  `{slug, name, at}`。**严禁**写入 token、邮箱、角色任何凭据或个人信息。
- 该键读取失败（JSON 损坏、被禁用）必须静默降级为「无最近访问」，**不允许白屏或抛错**。
- 有动效就必须 `respect prefers-reduced-motion`（`web/src/index.css` 已有该 media query，复用它）。

---

## 通用硬约束

1. **对比度**：新增/改动的颜色必须过 WCAG AA。**禁止硬编码 `text-white` 这类值**
   —— `docs/12` §U2 就是「用错 token」而不是调色问题。一律用语义令牌
   （`text-destructive-foreground` 等）。改完自行量一遍，把数字写进报告。
2. **主题三态**：light / dark / system 都要验。
3. **`npm run verify` 必须通过**。若 `check:api-types` 因后端阻塞失败
   （见 `contracts/CONTRACT.md` §22.1），要在报告里**明确说明它是否与本次改动有关**，
   不要笼统说"有一个已知失败"。
4. **mock E2E 基线是 79 passed**，不得回归。真机 E2E 16 passed 同理。
5. 不新增运行时依赖；不升级既有依赖版本。
6. 遵守既有代码风格（TS strict，无 `any`，组件与 hooks 分层）。

---

## 交付要求

完成后给一份报告，包含：

1. 6 项任务逐项的**状态**（完成 / 部分 / 未做）与**改动文件清单**。
2. F1 的四档宽度 × 三主题的实测结论，以及窄屏图标态的可访问名是什么。
3. 新增颜色的**对比度实测数字**。
4. `npm run verify`、mock E2E、真机 E2E 的**命令与真实输出**。
5. **任何你没做到或与任务书有出入的地方，明确说出来**，不要静默略过或美化。
   有取巧（如 F4 少做了某列）也要写清楚原因。
