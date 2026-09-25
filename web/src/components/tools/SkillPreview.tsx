import { useQuery } from "@tanstack/react-query";
import { AlertTriangle, FileText, Folder, Info } from "lucide-react";
import * as React from "react";

import { getErrorMessage } from "@/api/client";
import { fetchSkillPreview, skillPreviewQueryKey } from "@/api/tools";
import type { SkillDetailInfo, SkillFileEntry, SkillVersionInfo } from "@/api/types";
import { ErrorState } from "@/components/common/ErrorState";
import { Markdown } from "@/components/common/Markdown";
import { PageSkeleton } from "@/components/common/PageSkeleton";
import { Alert, AlertDescription, AlertTitle } from "@/components/ui/alert";
import { Badge } from "@/components/ui/badge";
import { formatFileSize } from "@/lib/format";
import { cn } from "@/lib/utils";

/**
 * `skill` 类型专属区块（docs/04 §6.4）：三页签「SKILL.md / 包内文件 / 元信息」。
 *
 * **按需加载是硬要求**（docs/04 §6.4、§9）：skill-preview 的 JSON 可能很大，
 * 因此初始页签是「元信息」（`detail.skill` 已带 manifest/summary），只有切到
 * 「SKILL.md」或「包内文件」时才用 `enabled` 触发请求 —— 首屏不发这个请求。
 *
 * 已知接口缺口：M2 清单（CONTRACT §6.1）**没有**单文件内容接口，`docs/03` §3.5
 * 只返回文件树的 path/size/is_dir/sha256，所以除 SKILL.md 外的条目不可点击。
 */
export type SkillTab = "readme" | "files" | "meta";

const TABS: ReadonlyArray<{ id: SkillTab; label: string; panelTestId: string }> = [
  { id: "readme", label: "SKILL.md", panelTestId: "skill-md-panel" },
  { id: "files", label: "包内文件", panelTestId: "skill-tree-panel" },
  { id: "meta", label: "元信息", panelTestId: "skill-meta-panel" },
];

export interface SkillPreviewProps {
  slug: string;
  /** `detail.current_version.version`；为 null 时无法预览（预览按版本取）。 */
  version: string | null;
  /** `detail.skill` — 无需二次请求即可展示的元信息。 */
  skill: SkillDetailInfo | null;
  /** `detail.current_version.skill` — 更贴近当前版本的 manifest / parse 诊断。 */
  versionSkill: SkillVersionInfo | null;
}

function manifestValueText(value: unknown): string {
  if (value === null || value === undefined) return "—";
  if (typeof value === "string") return value;
  if (typeof value === "number" || typeof value === "boolean") return String(value);
  try {
    const serialised = JSON.stringify(value);
    return serialised ?? String(value);
  } catch {
    return String(value);
  }
}

function isStructured(value: unknown): boolean {
  return value !== null && typeof value === "object";
}

/** frontmatter 键值表；数组值（如 `allowed-tools`）渲染成 `Badge` 列表。 */
function ManifestTable({ manifest }: { manifest: Record<string, unknown> }) {
  const entries = Object.entries(manifest);
  if (entries.length === 0) {
    return <p className="text-sm text-muted-foreground">未解析到 frontmatter 字段。</p>;
  }
  return (
    <dl className="divide-y rounded-lg border" data-testid="skill-manifest">
      {entries.map(([key, value]) => (
        <div key={key} className="grid gap-1 px-3 py-2 sm:grid-cols-[10rem_minmax(0,1fr)]">
          <dt className="break-all font-mono text-xs text-muted-foreground">{key}</dt>
          <dd className="min-w-0 text-sm">
            {Array.isArray(value) ? (
              <span className="flex flex-wrap gap-1">
                {value.length === 0 ? (
                  <span className="text-muted-foreground">—</span>
                ) : (
                  value.map((item, index) => (
                    <Badge key={`${key}-${index}`} variant="secondary" className="font-mono">
                      {manifestValueText(item)}
                    </Badge>
                  ))
                )}
              </span>
            ) : (
              <span className={cn("break-words", isStructured(value) && "font-mono text-xs")}>
                {manifestValueText(value)}
              </span>
            )}
          </dd>
        </div>
      ))}
    </dl>
  );
}

function FileTree({ entries }: { entries: SkillFileEntry[] }) {
  if (entries.length === 0) {
    return <p className="text-sm text-muted-foreground">文件中没有可展示的条目。</p>;
  }
  return (
    <ul className="divide-y rounded-lg border" data-testid="skill-file-tree">
      {entries.map((entry) => {
        const depth = Math.max(0, entry.path.split("/").length - 1);
        const name = entry.path.split("/").at(-1) ?? entry.path;
        return (
          // 无单文件内容接口（CONTRACT §6.1 / docs/03 §3.5）：除 SKILL.md 外一律
          // 只读展示大小，**不加 onClick**，避免给出无法兑现的交互承诺。
          <li
            key={entry.path}
            className="flex items-center gap-2 py-1.5 pr-3 text-sm"
            style={{ paddingLeft: `${12 + depth * 14}px` }}
          >
            {entry.is_dir ? (
              <Folder aria-hidden="true" className="size-3.5 shrink-0 text-muted-foreground" />
            ) : (
              <FileText aria-hidden="true" className="size-3.5 shrink-0 text-muted-foreground" />
            )}
            <span className="min-w-0 flex-1 truncate font-mono text-xs" title={entry.path}>
              {name}
              {entry.is_dir ? "/" : ""}
            </span>
            {!entry.is_dir && entry.path === "SKILL.md" ? (
              <Badge variant="secondary" className="shrink-0">
                见 SKILL.md 页签
              </Badge>
            ) : null}
            {entry.is_dir ? null : (
              <span className="shrink-0 text-xs text-muted-foreground tabular-nums">
                {formatFileSize(entry.size)}
              </span>
            )}
          </li>
        );
      })}
    </ul>
  );
}

export function SkillPreview({ slug, version, skill, versionSkill }: SkillPreviewProps) {
  // 初始页签不触发请求（docs/04 §6.4「按需加载」）—— 元信息来自详情接口。
  const [tab, setTab] = React.useState<SkillTab>("meta");
  const tabRefs = React.useRef<Array<HTMLButtonElement | null>>([]);

  const needsPreview = tab === "readme" || tab === "files";
  const previewQuery = useQuery({
    queryKey: skillPreviewQueryKey(slug, version ?? ""),
    queryFn: ({ signal }) => fetchSkillPreview(slug, version ?? "", signal),
    enabled: needsPreview && version !== null,
    staleTime: 5 * 60_000,
  });

  const manifest = versionSkill?.manifest ?? skill?.manifest ?? null;
  const summary = versionSkill?.file_tree_summary ?? skill?.file_tree_summary ?? null;
  const parseError = versionSkill?.parse_error ?? skill?.parse_error ?? null;
  const preview = previewQuery.data ?? null;
  const hasSkillInfo = skill !== null || versionSkill !== null;

  const handleTabKeys = (event: React.KeyboardEvent<HTMLButtonElement>, index: number) => {
    const last = TABS.length - 1;
    let next: number | null = null;
    if (event.key === "ArrowRight") next = index === last ? 0 : index + 1;
    else if (event.key === "ArrowLeft") next = index === 0 ? last : index - 1;
    else if (event.key === "Home") next = 0;
    else if (event.key === "End") next = last;
    if (next === null) return;
    event.preventDefault();
    const target = TABS[next];
    if (!target) return;
    setTab(target.id);
    tabRefs.current[next]?.focus();
  };

  /** 元信息来自详情接口；其余两页依赖 skill-preview。 */
  function renderPanel() {
    if (tab === "meta") {
      return (
        <div className="space-y-3">
          {summary ? (
            <p className="text-xs text-muted-foreground">
              共 {summary.file_count} 个文件 · 总大小 {formatFileSize(summary.total_size)} ·
              最大深度 {summary.max_depth} 层
            </p>
          ) : null}
          {manifest ? (
            <ManifestTable manifest={manifest} />
          ) : (
            <p className="text-sm text-muted-foreground">
              {hasSkillInfo ? "未解析到 frontmatter 字段。" : "该 Skill 未提供元信息。"}
            </p>
          )}
        </div>
      );
    }

    if (version === null) {
      return <p className="text-sm text-muted-foreground">该工具暂无可用版本，无法预览。</p>;
    }

    if (previewQuery.isPending) return <PageSkeleton variant="list" count={3} />;

    if (previewQuery.isError) {
      return (
        <ErrorState
          message={getErrorMessage(previewQuery.error)}
          onRetry={() => {
            void previewQuery.refetch();
          }}
        />
      );
    }

    if (tab === "readme") {
      if (!preview?.readme_md || preview.readme_md.trim() === "") {
        return <p className="text-sm text-muted-foreground">该 Skill 未提供 SKILL.md。</p>;
      }
      return <Markdown source={preview.readme_md} />;
    }

    return (
      <div className="space-y-2">
        <FileTree entries={preview?.file_tree ?? []} />
        {preview?.file_tree_truncated ? (
          <p className="text-xs text-amber-700 dark:text-amber-300">
            包内文件过多，仅显示前 {preview.file_tree.length} 项。
          </p>
        ) : null}
        <p className="flex items-center gap-1 text-xs text-muted-foreground">
          <Info aria-hidden="true" className="size-3" />
          仅 SKILL.md 支持预览
        </p>
      </div>
    );
  }

  const activeMeta = TABS.find((item) => item.id === tab);

  return (
    <section
      aria-labelledby="skill-preview-title"
      className="space-y-3 rounded-xl border bg-card p-4"
      data-testid="skill-tabs"
    >
      <h2 id="skill-preview-title" className="text-lg font-semibold">
        Skill 预览
      </h2>

      {parseError ? (
        <Alert variant="warning" data-testid="skill-parse-error">
          <AlertTriangle aria-hidden="true" />
          <AlertTitle>Skill 包解析存在问题</AlertTitle>
          <AlertDescription>{parseError}</AlertDescription>
        </Alert>
      ) : null}

      <div role="tablist" aria-label="Skill 预览页签" className="flex flex-wrap gap-1 border-b">
        {TABS.map((item, index) => {
          const active = item.id === tab;
          return (
            <button
              key={item.id}
              ref={(node) => {
                tabRefs.current[index] = node;
              }}
              type="button"
              role="tab"
              id={`skill-tab-${item.id}`}
              aria-selected={active}
              aria-controls={`skill-panel-${item.id}`}
              tabIndex={active ? 0 : -1}
              data-testid={`skill-tab-${item.id}`}
              onClick={() => setTab(item.id)}
              onKeyDown={(event) => handleTabKeys(event, index)}
              className={cn(
                "-mb-px border-b-2 px-3 py-2 text-sm font-medium transition-colors",
                active
                  ? "border-primary text-foreground"
                  : "border-transparent text-muted-foreground hover:text-foreground",
              )}
            >
              {item.label}
            </button>
          );
        })}
      </div>

      <div
        role="tabpanel"
        id={`skill-panel-${tab}`}
        aria-labelledby={`skill-tab-${tab}`}
        data-testid={activeMeta?.panelTestId}
      >
        {renderPanel()}
      </div>
    </section>
  );
}
