import { Alert, AlertDescription, AlertTitle } from "@/components/ui/alert";
import { ImportExportPanel } from "@/components/admin/ImportExportPanel";
import { Info } from "lucide-react";

/**
 * `/admin/import-export` —— 批量导入导出（docs/04 §6.20）。
 *
 * 页面只负责标题与顶部说明，四张卡片与一次性密码的展示逻辑都在
 * `ImportExportPanel` 里（密码明文只存活在该组件的 state 中）。
 */
export default function ImportExportPage() {
  return (
    <div data-testid="import-export-page" className="flex flex-col gap-4">
      <div className="flex flex-wrap items-baseline justify-between gap-2">
        <h1 className="text-xl font-semibold tracking-tight">批量导入导出</h1>
        <p className="text-xs text-muted-foreground">
          导入先预演、再确认；导出为浏览器端下载（文件名带日期）
        </p>
      </div>

      <Alert>
        <Info aria-hidden="true" />
        <AlertTitle>导入是原子性操作</AlertTitle>
        <AlertDescription>
          每行都会独立校验，错误精确到行与字段；预演不写库，也不会生成密码。系统生成的一次性初始密码
          只在导入响应里出现一次，请立即下载或复制。
        </AlertDescription>
      </Alert>

      <ImportExportPanel />
    </div>
  );
}
