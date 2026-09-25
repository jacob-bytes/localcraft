import { useMutation, useQueryClient } from "@tanstack/react-query";
import {
  Download,
  FileJson,
  FileSpreadsheet,
  Info,
  ShieldAlert,
  TriangleAlert,
  Upload,
} from "lucide-react";
import * as React from "react";
import { toast } from "sonner";

import {
  exportTools,
  exportUsers,
  importTools,
  importUsers,
  overviewQueryKey,
} from "@/api/admin";
import { getErrorMessage } from "@/api/client";
import type {
  GeneratedPassword,
  ImportOnConflict,
  ImportResultResponse,
  ToolImportItem,
  ToolImportRequest,
} from "@/api/types";
import { CopyButton } from "@/components/common/CopyButton";
import { EmptyState } from "@/components/common/EmptyState";
import { ConfirmDialog } from "@/components/common/ConfirmDialog";
import { Alert, AlertDescription, AlertTitle } from "@/components/ui/alert";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Label } from "@/components/ui/label";import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table";
import { Textarea } from "@/components/ui/textarea";

/**
 * 批量导入导出面板（docs/04 §6.20）。
 *
 * 契约事实（写在这里免得后来者以为漏了功能）：
 *  - `GET /admin/export/users` 与 `/admin/export/tools` **没有查询参数**
 *    （openapi.json 固定冻结），docs/04 §6.20 里的「包含角色 / 状态筛选 / 分类范围」
 *    这一版接口面做不到，页面因此不渲染这些筛选器；
 *  - 导出文件名由 API 层（`src/api/admin.ts`）加日期，响应体自带 CSV 的 BOM；
 *  - 导入接口返回的 `generated_passwords` 是**唯一一次**明文密码（docs/03 §3.14），
 *    只保留在当前组件的 state 里，关闭即消失，绝不落盘。
 */

const CONFLICT_OPTIONS: ReadonlyArray<{ value: ImportOnConflict; label: string; hint: string }> = [
  { value: "skip", label: "跳过已存在", hint: "已存在的记录保持原样，只导入新记录。" },
  { value: "update", label: "更新已存在", hint: "已存在的记录按文件内容覆盖。" },
  { value: "fail", label: "遇冲突即失败", hint: "只要有一条已存在就整体失败，适合严格校验。" },
];

const USERS_TEMPLATE_HEADER = "username,display_name,email,roles,password";
const USERS_TEMPLATE_EXAMPLE = "lisi,李四,lisi@example.com,user,";

function dateStamp(): string {
  const now = new Date();
  return `${now.getFullYear()}${String(now.getMonth() + 1).padStart(2, "0")}${String(
    now.getDate(),
  ).padStart(2, "0")}`;
}

/** 浏览器端下载（docs/04 §6.20 的模板与一次性密码都需要它）。 */
function downloadText(filename: string, content: string, mime: string): void {
  const blob = new Blob([content], { type: mime });
  const url = URL.createObjectURL(blob);
  const anchor = document.createElement("a");
  anchor.href = url;
  anchor.download = filename;
  document.body.appendChild(anchor);
  anchor.click();
  anchor.remove();
  URL.revokeObjectURL(url);
}

/** CSV 单元格转义：含逗号 / 引号 / 换行时加引号并转义引号。 */
function csvCell(value: string): string {
  if (/[",\n\r]/.test(value)) return `"${value.replace(/"/g, '""')}"`;
  return value;
}

export interface ImportExportPanelProps {
  className?: string;
}

export function ImportExportPanel({ className }: ImportExportPanelProps) {
  return (
    <div className={className}>
      <div className="grid grid-cols-1 gap-4 lg:grid-cols-2">
        <UserImportCard />
        <ExportUsersCard />
        <ToolImportCard />
        <ExportToolsCard />
      </div>
    </div>
  );
}

/* -------------------------------------------------------------------------- */
/* 导入用户                                                                    */
/* -------------------------------------------------------------------------- */

function UserImportCard() {
  const queryClient = useQueryClient();
  const [file, setFile] = React.useState<File | null>(null);
  const [onConflict, setOnConflict] = React.useState<ImportOnConflict>("skip");
  const [result, setResult] = React.useState<ImportResultResponse | null>(null);
  const [passwords, setPasswords] = React.useState<GeneratedPassword[]>([]);
  const [dragging, setDragging] = React.useState(false);
  const [formError, setFormError] = React.useState<string | null>(null);
  const [confirmOpen, setConfirmOpen] = React.useState(false);

  const dryRunDone = result !== null && result.dry_run;

  const importMutation = useMutation({
    mutationFn: (dryRun: boolean) => {
      if (!file) throw new Error("请先选择 CSV 文件");
      return importUsers(file, { dryRun, onConflict });
    },
    onSuccess: (response, dryRun) => {
      setResult(response);
      setFormError(null);
      if (dryRun) {
        setPasswords([]);
        toast.success("预演完成，未写入任何数据");
        return;
      }
      setPasswords(response.generated_passwords);
      toast.success(
        `导入完成：成功 ${response.succeeded} 条，失败 ${response.failed} 条，跳过 ${response.skipped} 条`,
      );
      void queryClient.invalidateQueries({ queryKey: ["admin", "users"] });
      void queryClient.invalidateQueries({ queryKey: overviewQueryKey });
    },
    onError: (error) => setFormError(getErrorMessage(error)),
  });

  function pickFile(next: File | null) {
    setFile(next);
    setResult(null);
    setPasswords([]);
    setFormError(null);
  }

  function handleDirect() {
    if (!file) {
      setFormError("请先选择 CSV 文件");
      return;
    }
    setConfirmOpen(true);
  }

  return (
    <Card data-testid="import-users-card">
      <CardHeader className="border-b">
        <CardTitle className="flex items-center gap-2 text-base">
          <Upload aria-hidden="true" className="size-4" />
          导入用户
        </CardTitle>
        <CardDescription>
          CSV 表头：<span className="font-mono text-xs">{USERS_TEMPLATE_HEADER}</span>
          ；密码留空则由系统生成一次性初始密码。建议先预演确认无误再写入。
        </CardDescription>
      </CardHeader>

      <CardContent className="space-y-4">
        <div
          className={
            dragging
              ? "rounded-lg border-2 border-dashed border-primary bg-accent/40 p-6 text-center"
              : "rounded-lg border-2 border-dashed border-input p-6 text-center"
          }
          onDragOver={(event) => {
            event.preventDefault();
            setDragging(true);
          }}
          onDragLeave={() => setDragging(false)}
          onDrop={(event) => {
            event.preventDefault();
            setDragging(false);
            const dropped = event.dataTransfer.files.item(0);
            if (dropped) pickFile(dropped);
          }}
        >
          <input
            id="import-users-file"
            type="file"
            accept=".csv,text/csv"
            className="sr-only"
            onChange={(event) => pickFile(event.target.files?.item(0) ?? null)}
          />
          <Label htmlFor="import-users-file" className="cursor-pointer text-sm font-normal">
            把 CSV 拖到这里，或
            <span className="text-primary underline underline-offset-4">点击选择文件</span>
          </Label>
          <p className="mt-2 text-xs text-muted-foreground">
            {file ? `已选择：${file.name}（${file.size} 字节）` : "仅接受 .csv 文件，UTF-8 编码"}
          </p>
          <button
            type="button"
            data-testid="csv-template-link"
            className="mt-3 text-xs text-primary underline underline-offset-4"
            onClick={() =>
              downloadText(
                "selftool-users-template.csv",
                `\uFEFF${USERS_TEMPLATE_HEADER}\n${USERS_TEMPLATE_EXAMPLE}\n`,
                "text/csv;charset=utf-8",
              )
            }
          >
            下载 CSV 模板
          </button>
        </div>

        <div className="space-y-2">
          <Label htmlFor="import-users-conflict">用户名冲突时</Label>
          <Select
            value={onConflict}
            onValueChange={(value) => {
              setOnConflict(value === "update" || value === "fail" ? value : "skip");
              setResult(null);
              setPasswords([]);
            }}
          >
            <SelectTrigger id="import-users-conflict" className="w-full">
              <SelectValue />
            </SelectTrigger>
            <SelectContent>
              {CONFLICT_OPTIONS.map((option) => (
                <SelectItem key={option.value} value={option.value}>
                  {option.label}
                </SelectItem>
              ))}
            </SelectContent>
          </Select>
          <p className="text-xs text-muted-foreground">
            {CONFLICT_OPTIONS.find((option) => option.value === onConflict)?.hint ?? ""}
          </p>
        </div>

        {formError ? (
          <Alert variant="destructive">
            <TriangleAlert aria-hidden="true" />
            <AlertTitle>导入失败</AlertTitle>
            <AlertDescription>{formError}</AlertDescription>
          </Alert>
        ) : null}

        <div className="flex flex-wrap gap-2">
          <Button
            type="button"
            variant="outline"
            data-testid="import-dry-run-button"
            disabled={importMutation.isPending}
            onClick={() => {
              if (!file) {
                setFormError("请先选择 CSV 文件");
                return;
              }
              importMutation.mutate(true);
            }}
          >
            {importMutation.isPending && importMutation.variables === true ? "预演中…" : "先预演"}
          </Button>
          <Button
            type="button"
            data-testid="import-confirm-button"
            disabled={importMutation.isPending}
            onClick={handleDirect}
          >
            {dryRunDone ? "确认导入" : "直接导入"}
          </Button>
        </div>

        {result === null ? (
          <EmptyState
            icon={FileSpreadsheet}
            title="还没有预演结果"
            description="点「先预演」可以在不写库的情况下看到会成功 / 失败 / 跳过多少条，以及逐行错误。"
            className="py-8"
          />
        ) : (
          <ImportResultTable result={result} testId="import-result-table" />
        )}

        {passwords.length > 0 ? (
          <GeneratedPasswordsPanel
            passwords={passwords}
            onDismiss={() => setPasswords([])}
          />
        ) : null}
      </CardContent>

      <ConfirmDialog
        open={confirmOpen}
        onOpenChange={setConfirmOpen}
        destructive={false}
        title={dryRunDone ? "按预演结果写入数据库？" : "直接导入并写入数据库？"}
        description={
          <div className="space-y-2">
            <p>
              {dryRunDone
                ? `预演结果：成功 ${result?.succeeded ?? 0} 条、失败 ${result?.failed ?? 0} 条、跳过 ${result?.skipped ?? 0} 条。确认后会把成功的记录写入数据库。`
                : "尚未预演。确认后将立即把文件内容写入数据库，冲突策略为「" +
                  (CONFLICT_OPTIONS.find((option) => option.value === onConflict)?.label ?? "") +
                  "」。"}
            </p>
            {(result?.failed ?? 0) > 0 ? (
              <p className="text-destructive">
                预演中有 {result?.failed} 条失败，这些行不会写入。
              </p>
            ) : null}
          </div>
        }
        confirmLabel={dryRunDone ? "确认导入" : "直接导入"}
        pending={importMutation.isPending}
        onConfirm={() => {
          setConfirmOpen(false);
          importMutation.mutate(false);
        }}
      />
    </Card>
  );
}

/* -------------------------------------------------------------------------- */
/* 导入工具                                                                    */
/* -------------------------------------------------------------------------- */

function ToolImportCard() {
  const queryClient = useQueryClient();
  const [raw, setRaw] = React.useState("");
  const [sourceName, setSourceName] = React.useState<string | null>(null);
  const [onConflict, setOnConflict] = React.useState<ImportOnConflict>("skip");
  const [result, setResult] = React.useState<ImportResultResponse | null>(null);
  const [parseError, setParseError] = React.useState<string | null>(null);
  const [requestError, setRequestError] = React.useState<string | null>(null);
  const [confirmOpen, setConfirmOpen] = React.useState(false);

  /** 解析成 `ToolImportRequest.items`。解析失败只提示，**不发请求**。 */
  function parseItems(): ToolImportItem[] | null {
    const text = raw.trim();
    if (!text) {
      setParseError("请先粘贴 JSON 或选择 .json 文件");
      return null;
    }
    let parsed: unknown;
    try {
      parsed = JSON.parse(text);
    } catch {
      setParseError("JSON 解析失败，请检查括号、引号与逗号");
      return null;
    }
    const items = Array.isArray(parsed)
      ? parsed
      : typeof parsed === "object" && parsed !== null && Array.isArray((parsed as { items?: unknown }).items)
        ? ((parsed as { items: unknown[] }).items)
        : null;
    if (items === null) {
      setParseError("JSON 必须是工具数组，或形如 { \"items\": [ ... ] } 的对象");
      return null;
    }
    if (items.length === 0) {
      setParseError("items 不能为空数组");
      return null;
    }
    setParseError(null);
    return items as ToolImportItem[];
  }

  const importMutation = useMutation({
    mutationFn: (payload: ToolImportRequest) => importTools(payload),
    onSuccess: (response, payload) => {
      setResult(response);
      setRequestError(null);
      if (payload.dry_run === true) {
        toast.success("预演完成，未写入任何数据");
        return;
      }
      toast.success(
        `导入完成：成功 ${response.succeeded} 条，失败 ${response.failed} 条，跳过 ${response.skipped} 条`,
      );
      void queryClient.invalidateQueries({ queryKey: ["admin", "tools"] });
      void queryClient.invalidateQueries({ queryKey: overviewQueryKey });
    },
    onError: (error) => setRequestError(getErrorMessage(error)),
  });

  function run(dryRun: boolean, confirmed = false) {
    const items = parseItems();
    if (items === null) return;
    if (!dryRun && !confirmed) {
      setConfirmOpen(true);
      return;
    }
    importMutation.mutate({ items, dry_run: dryRun, on_conflict: onConflict });
  }

  const dryRunDone = result !== null && result.dry_run;

  return (
    <Card data-testid="import-tools-card">
      <CardHeader className="border-b">
        <CardTitle className="flex items-center gap-2 text-base">
          <FileJson aria-hidden="true" className="size-4" />
          导入工具
        </CardTitle>
        <CardDescription>
          兼容两种 JSON：工具对象数组，或 <span className="font-mono text-xs">{'{ "items": [...] }'}</span>
          。每个条目至少要有 <span className="font-mono text-xs">name</span> 与{" "}
          <span className="font-mono text-xs">tool_type</span>。
        </CardDescription>
      </CardHeader>

      <CardContent className="space-y-4">
        <div className="space-y-2">
          <div className="flex items-center justify-between gap-2">
            <Label htmlFor="import-tools-json">JSON 内容</Label>
            <div>
              <input
                id="import-tools-file"
                type="file"
                accept=".json,application/json"
                className="sr-only"
                onChange={(event) => {
                  const picked = event.target.files?.item(0);
                  if (!picked) return;
                  setSourceName(picked.name);
                  void picked.text().then((text) => {
                    setRaw(text);
                    setResult(null);
                    setParseError(null);
                    setRequestError(null);
                  });
                }}
              />
              <Label
                htmlFor="import-tools-file"
                className="cursor-pointer text-xs text-primary underline underline-offset-4"
              >
                {sourceName ? `已选 ${sourceName}，重新选择` : "选择 .json 文件"}
              </Label>
            </div>
          </div>
          <Textarea
            id="import-tools-json"
            rows={6}
            className="font-mono text-xs"
            placeholder={'[{"name": "日志分析器", "tool_type": "file", "summary": "…"}]'}
            value={raw}
            onChange={(event) => {
              setRaw(event.target.value);
              setResult(null);
              setParseError(null);
              setRequestError(null);
            }}
          />
        </div>

        <div className="space-y-2">
          <Label htmlFor="import-tools-conflict">同名冲突时</Label>
          <Select
            value={onConflict}
            onValueChange={(value) => {
              setOnConflict(value === "update" || value === "fail" ? value : "skip");
              setResult(null);
            }}
          >
            <SelectTrigger id="import-tools-conflict" className="w-full">
              <SelectValue />
            </SelectTrigger>
            <SelectContent>
              {CONFLICT_OPTIONS.map((option) => (
                <SelectItem key={option.value} value={option.value}>
                  {option.label}
                </SelectItem>
              ))}
            </SelectContent>
          </Select>
        </div>

        {parseError ? (
          <p role="alert" className="text-sm text-destructive">
            {parseError}
          </p>
        ) : null}

        {requestError ? (
          <Alert variant="destructive">
            <TriangleAlert aria-hidden="true" />
            <AlertTitle>导入失败</AlertTitle>
            <AlertDescription>{requestError}</AlertDescription>
          </Alert>
        ) : null}

        <div className="flex flex-wrap gap-2">
          <Button
            type="button"
            variant="outline"
            data-testid="import-tools-dry-run-button"
            disabled={importMutation.isPending}
            onClick={() => run(true)}
          >
            先预演
          </Button>
          <Button
            type="button"
            data-testid="import-tools-confirm-button"
            disabled={importMutation.isPending}
            onClick={() => run(false)}
          >
            {dryRunDone ? "确认导入" : "直接导入"}
          </Button>
        </div>

        {result === null ? (
          <EmptyState
            icon={FileJson}
            title="还没有预演结果"
            description="粘贴 JSON 后点「先预演」，确认无误再写入。"
            className="py-8"
          />
        ) : (
          <ImportResultTable result={result} testId="import-result-table-tools" />
        )}
      </CardContent>

      <ConfirmDialog
        open={confirmOpen}
        onOpenChange={setConfirmOpen}
        destructive={false}
        title={dryRunDone ? "按预演结果写入数据库？" : "直接导入并写入数据库？"}
        description="确认后将立即把 JSON 中的工具写入数据库。"
        confirmLabel="确认导入"
        pending={importMutation.isPending}
        onConfirm={() => {
          setConfirmOpen(false);
          run(false, true);
        }}
      />
    </Card>
  );
}

/* -------------------------------------------------------------------------- */
/* 导出                                                                        */
/* -------------------------------------------------------------------------- */

function ExportUsersCard() {
  const [pending, setPending] = React.useState(false);

  async function handleExport() {
    setPending(true);
    try {
      await exportUsers();
      toast.success("已导出用户 CSV（文件名带日期）");
    } catch (error) {
      toast.error(getErrorMessage(error));
    } finally {
      setPending(false);
    }
  }

  return (
    <Card data-testid="export-users-card">
      <CardHeader className="border-b">
        <CardTitle className="flex items-center gap-2 text-base">
          <Download aria-hidden="true" className="size-4" />
          导出用户
        </CardTitle>
        <CardDescription>
          导出全站用户为 CSV（带 BOM，Excel 打开不乱码）。不含密码哈希，密码列始终为空。
        </CardDescription>
      </CardHeader>
      <CardContent className="space-y-3">
        <Alert>
          <Info aria-hidden="true" />
          <AlertTitle>接口没有筛选参数</AlertTitle>
          <AlertDescription>
            当前冻结的导出接口不接受查询参数，所以「包含角色 / 状态筛选」这一版无法提供；
            导出内容为全量用户。
          </AlertDescription>
        </Alert>
        <Button type="button" disabled={pending} onClick={() => void handleExport()}>
          {pending ? "导出中…" : "导出 CSV"}
        </Button>
      </CardContent>
    </Card>
  );
}

function ExportToolsCard() {
  const [pending, setPending] = React.useState(false);

  async function handleExport() {
    setPending(true);
    try {
      await exportTools();
      toast.success("已导出工具 JSON（文件名带日期）");
    } catch (error) {
      toast.error(getErrorMessage(error));
    } finally {
      setPending(false);
    }
  }

  return (
    <Card data-testid="export-tools-card">
      <CardHeader className="border-b">
        <CardTitle className="flex items-center gap-2 text-base">
          <Download aria-hidden="true" className="size-4" />
          导出工具
        </CardTitle>
        <CardDescription>
          导出全站工具的元信息为 JSON。格式与「导入工具」兼容，可直接回灌。
        </CardDescription>
      </CardHeader>
      <CardContent className="space-y-3">
        <Alert>
          <Info aria-hidden="true" />
          <AlertTitle>接口没有筛选参数</AlertTitle>
          <AlertDescription>
            当前冻结的导出接口不接受状态 / 分类范围参数，导出内容为全量工具。
          </AlertDescription>
        </Alert>
        <Button type="button" disabled={pending} onClick={() => void handleExport()}>
          {pending ? "导出中…" : "导出 JSON"}
        </Button>
      </CardContent>
    </Card>
  );
}

/* -------------------------------------------------------------------------- */
/* 结果与一次性密码                                                            */
/* -------------------------------------------------------------------------- */

export function ImportResultTable({
  result,
  testId,
}: {
  result: ImportResultResponse;
  testId: string;
}) {
  return (
    <div className="space-y-3" data-testid={testId}>
      <div className="flex flex-wrap items-center gap-2">
        <Badge variant={result.succeeded > 0 ? "success" : "secondary"}>
          成功 {result.succeeded}
        </Badge>
        <Badge variant={result.failed > 0 ? "destructive" : "secondary"}>
          失败 {result.failed}
        </Badge>
        <Badge variant="secondary">跳过 {result.skipped}</Badge>
        {result.dry_run ? <Badge variant="outline">预演（未写库）</Badge> : null}
      </div>

      {result.errors.length === 0 ? (
        <Alert variant="success">
          <ShieldAlert aria-hidden="true" />
          <AlertTitle>没有错误</AlertTitle>
          <AlertDescription>
            {result.dry_run ? "预演通过，可以点「确认导入」写入。" : "全部记录已按冲突策略处理。"}
          </AlertDescription>
        </Alert>
      ) : (
        <div className="overflow-hidden rounded-lg border">
          <Table className="min-w-[640px]">
            <TableHeader className="bg-muted/50">
              <TableRow>
                <TableHead>行号</TableHead>
                <TableHead>字段</TableHead>
                <TableHead>原值</TableHead>
                <TableHead>错误</TableHead>
              </TableRow>
            </TableHeader>
            <TableBody>
              {result.errors.map((error, index) => (
                <TableRow key={`${error.row}-${error.field ?? ""}-${index}`}>
                  <TableCell className="tabular-nums">第 {error.row} 行</TableCell>
                  <TableCell className="font-mono text-xs">{error.field ?? "—"}</TableCell>
                  <TableCell className="max-w-[12rem] truncate font-mono text-xs" title={String(error.value ?? "")}>
                    {error.value === undefined || error.value === null ? "—" : String(error.value)}
                  </TableCell>
                  <TableCell className="text-xs text-destructive">{error.message}</TableCell>
                </TableRow>
              ))}
            </TableBody>
          </Table>
        </div>
      )}
    </div>
  );
}

function buildPasswordsCsv(passwords: readonly GeneratedPassword[]): string {
  const lines = ["username,password"];
  for (const entry of passwords) {
    lines.push(`${csvCell(entry.username)},${csvCell(entry.password)}`);
  }
  return `\uFEFF${lines.join("\n")}\n`;
}

function GeneratedPasswordsPanel({
  passwords,
  onDismiss,
}: {
  passwords: readonly GeneratedPassword[];
  onDismiss: () => void;
}) {
  const csv = React.useMemo(() => buildPasswordsCsv(passwords), [passwords]);

  return (
    <div className="space-y-3" data-testid="import-generated-passwords">
      <Alert variant="destructive">
        <TriangleAlert aria-hidden="true" />
        <AlertTitle>此密码不会再次显示</AlertTitle>
        <AlertDescription>
          下面的初始密码只在这里出现一次。请立刻下载或复制并转交给对应用户，关闭后无法再获取。
        </AlertDescription>
      </Alert>

      <div className="overflow-hidden rounded-lg border">
        <Table>
          <TableHeader className="bg-muted/50">
            <TableRow>
              <TableHead>用户名</TableHead>
              <TableHead>初始密码</TableHead>
              <TableHead className="text-right">复制</TableHead>
            </TableRow>
          </TableHeader>
          <TableBody>
            {passwords.map((entry) => (
              <TableRow key={entry.username}>
                <TableCell className="font-mono text-xs">{entry.username}</TableCell>
                <TableCell className="font-mono text-sm break-all select-all">
                  {entry.password}
                </TableCell>
                <TableCell className="text-right">
                  <CopyButton
                    value={entry.password}
                    label={`复制 ${entry.username} 的初始密码`}
                    className="size-10"
                  />
                </TableCell>
              </TableRow>
            ))}
          </TableBody>
        </Table>
      </div>

      <div className="flex flex-wrap items-center gap-2">
        <Button
          type="button"
          data-testid="import-download-passwords"
          onClick={() =>
            downloadText(
              `selftool-passwords-${dateStamp()}.csv`,
              csv,
              "text/csv;charset=utf-8",
            )
          }
        >
          <Download aria-hidden="true" className="size-4" />
          下载 CSV
        </Button>
        <CopyButton value={csv} label="复制全部用户名与密码" className="size-10" />
        <Button type="button" variant="outline" onClick={onDismiss}>
          我已保存，隐藏密码
        </Button>
      </div>
    </div>
  );
}
