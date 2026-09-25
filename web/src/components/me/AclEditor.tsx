import { Plus, Trash2, Users } from "lucide-react";
import * as React from "react";

import type { AclSubjectType } from "@/api/types";
import { Button } from "@/components/ui/button";
import { Checkbox } from "@/components/ui/checkbox";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";

/** Payload shape of `PUT /me/tools/{id}/acl` (full replacement, FR-ACL-03). */
export interface AclDraftEntry {
  subject_type: AclSubjectType;
  subject_id: number;
  can_download: boolean;
  /**
   * Display name as returned by the server. There is no "search users/groups"
   * endpoint in CONTRACT §6.1, and GET /admin/users is superadmin-only, so the
   * client cannot resolve a bare id — entries added in this session show the id
   * until a save round-trips the server-provided `subject_name` back.
   */
  subject_name: string | null;
}

export interface AclEditorProps {
  entries: AclDraftEntry[];
  onChange: (entries: AclDraftEntry[]) => void;
  disabled?: boolean;
}

const SUBJECT_LABELS: Record<AclSubjectType, string> = {
  user: "用户",
  group: "用户组",
};

/**
 * 授权名单编辑器（docs/04 §6.7「指定可见 → 展开授权区」，FR-ACL-02/03/04）。
 *
 * Backend caps the list at 100 entries (`AclReplaceRequest`). Subjects are
 * entered by numeric id because no lookup endpoint exists for a normal user —
 * see the report's contract-gap list.
 */
export function AclEditor({ entries, onChange, disabled = false }: AclEditorProps) {
  const [subjectType, setSubjectType] = React.useState<AclSubjectType>("user");
  const [subjectId, setSubjectId] = React.useState("");
  const [error, setError] = React.useState<string | null>(null);

  function addEntry() {
    const parsed = Number.parseInt(subjectId, 10);
    if (!Number.isFinite(parsed) || parsed <= 0) {
      setError("请输入有效的用户 / 用户组 ID");
      return;
    }
    if (
      entries.some(
        (entry) => entry.subject_type === subjectType && entry.subject_id === parsed,
      )
    ) {
      setError("该主体已在授权名单中");
      return;
    }
    if (entries.length >= 100) {
      setError("授权条目不能超过 100 条");
      return;
    }
    setError(null);
    onChange([
      ...entries,
      {
        subject_type: subjectType,
        subject_id: parsed,
        can_download: true,
        subject_name: null,
      },
    ]);
    setSubjectId("");
  }

  return (
    <div className="space-y-3 rounded-lg border bg-muted/30 p-3" data-testid="acl-editor">
      <div className="flex flex-wrap items-end gap-2">
        <div className="space-y-1.5">
          <Label htmlFor="acl-subject-type">主体类型</Label>
          <Select
            value={subjectType}
            onValueChange={(value) => setSubjectType(value as AclSubjectType)}
            disabled={disabled}
          >
            <SelectTrigger id="acl-subject-type" className="w-28" aria-label="主体类型">
              <SelectValue />
            </SelectTrigger>
            <SelectContent>
              <SelectItem value="user">用户</SelectItem>
              <SelectItem value="group">用户组</SelectItem>
            </SelectContent>
          </Select>
        </div>

        <div className="space-y-1.5">
          <Label htmlFor="acl-subject-id">主体 ID</Label>
          <Input
            id="acl-subject-id"
            className="w-32"
            inputMode="numeric"
            value={subjectId}
            disabled={disabled}
            placeholder="如 3"
            onChange={(event) => setSubjectId(event.target.value)}
            onKeyDown={(event) => {
              if (event.key === "Enter") {
                event.preventDefault();
                addEntry();
              }
            }}
          />
        </div>

        <Button type="button" variant="outline" size="sm" disabled={disabled} onClick={addEntry}>
          <Plus aria-hidden="true" className="size-4" />
          添加
        </Button>

        <p className="w-full text-xs text-muted-foreground">
          暂无可用的成员检索接口，请填写用户 / 用户组 ID；保存后由服务端回填名称。
        </p>
      </div>

      {error ? (
        <p role="alert" className="text-xs text-destructive">
          {error}
        </p>
      ) : null}

      {entries.length === 0 ? (
        <p className="flex items-center gap-1.5 text-sm text-muted-foreground">
          <Users aria-hidden="true" className="size-4" />
          还没有授权对象，至少添加一条才能保存为「指定可见」。
        </p>
      ) : (
        <ul className="divide-y rounded-md border bg-card">
          {entries.map((entry) => (
            <li
              key={`${entry.subject_type}-${entry.subject_id}`}
              className="flex flex-wrap items-center gap-2 px-3 py-2 text-sm"
            >
              <span className="rounded bg-muted px-1.5 py-0.5 text-xs">
                {SUBJECT_LABELS[entry.subject_type]}
              </span>
              <span className="font-medium">
                {entry.subject_name ?? `#${entry.subject_id}`}
              </span>
              {entry.subject_name ? (
                <span className="font-mono text-xs text-muted-foreground">
                  #{entry.subject_id}
                </span>
              ) : null}

              <label className="ml-auto flex items-center gap-1.5 text-xs">
                <Checkbox
                  checked={entry.can_download}
                  disabled={disabled}
                  aria-label={`允许 ${entry.subject_name ?? entry.subject_id} 下载`}
                  onCheckedChange={(checked) =>
                    onChange(
                      entries.map((item) =>
                        item === entry ? { ...item, can_download: checked === true } : item,
                      ),
                    )
                  }
                />
                可下载
              </label>

              <Button
                type="button"
                variant="ghost"
                size="icon-sm"
                aria-label={`移除 ${entry.subject_name ?? entry.subject_id}`}
                disabled={disabled}
                onClick={() =>
                  onChange(
                    entries.filter(
                      (item) =>
                        !(
                          item.subject_type === entry.subject_type &&
                          item.subject_id === entry.subject_id
                        ),
                    ),
                  )
                }
              >
                <Trash2 aria-hidden="true" className="size-4 text-destructive" />
              </Button>
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}
