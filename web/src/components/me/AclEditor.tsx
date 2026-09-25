import { Loader2, Plus, Trash2, Users } from "lucide-react";
import * as React from "react";

import { useQuery } from "@tanstack/react-query";

import { searchDirectory } from "@/api/me";
import type { AclSubjectType } from "@/api/types";
import { useDebounce } from "@/hooks/useDebounce";
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
  const [query, setQuery] = React.useState("");
  const [open, setOpen] = React.useState(false);
  const [error, setError] = React.useState<string | null>(null);
  const debounced = useDebounce(query, 300);

  // M6 J-1：接 GET /api/v1/directory —— 此前普通用户**没有任何可用的检索接口**，
  // 只能手填数字 ID（后端 /admin/users、/admin/groups 都要求超管）。
  //
  // 用 TanStack Query 而不是手写 useEffect：项目的数据获取统一走它，
  // 而且手动 effect 里同步 setState（`setSearching(true)`）会被 oxlint 的
  // `set-state-in-effect` 规则拦下 —— 那条规则是对的，能用查询库就不该自己管 loading。
  const directoryQuery = useQuery({
    queryKey: ["directory", subjectType, debounced.trim()],
    queryFn: ({ signal }) =>
      searchDirectory(
        { q: debounced.trim() || undefined, type: subjectType, limit: 20 },
        signal,
      ),
    enabled: !disabled && open,
    staleTime: 30_000,
    retry: false,
  });

  const searching = directoryQuery.isFetching;

  /** 下拉里的候选项（按当前主体类型取）。 */
  const candidates: Array<{ id: number; label: string; hint: string }> = React.useMemo(() => {
    const data = directoryQuery.data;
    if (!data) return [];
    if (subjectType === "user") {
      return data.users.map((u) => ({ id: u.id, label: u.display_name, hint: u.username }));
    }
    return data.groups.map((g) => ({
      id: g.id,
      label: g.name,
      hint: `${g.member_count} 名成员`,
    }));
  }, [directoryQuery.data, subjectType]);

  function addEntry(subject: { id: number; label: string }) {
    if (
      entries.some(
        (entry) => entry.subject_type === subjectType && entry.subject_id === subject.id,
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
        subject_id: subject.id,
        can_download: true,
        // 名称直接回填，不必等保存后由服务端补 —— 列表里立刻可读
        subject_name: subject.label,
      },
    ]);
    setQuery("");
    setOpen(false);
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

        <div className="relative space-y-1.5">
          <Label htmlFor="acl-subject-search">
            {subjectType === "user" ? "搜索用户" : "搜索用户组"}
          </Label>
          <Input
            id="acl-subject-search"
            className="w-56"
            value={query}
            disabled={disabled}
            placeholder={subjectType === "user" ? "输入姓名或用户名" : "输入用户组名称"}
            autoComplete="off"
            role="combobox"
            aria-expanded={open}
            aria-controls="acl-subject-options"
            onFocus={() => setOpen(true)}
            onChange={(event) => {
              setQuery(event.target.value);
              setOpen(true);
            }}
            onKeyDown={(event) => {
              const first = candidates[0];
              if (event.key === "Enter" && first) {
                event.preventDefault();
                addEntry(first);
              }
              if (event.key === "Escape") setOpen(false);
            }}
          />

          {open && !disabled ? (
            <div
              id="acl-subject-options"
              role="listbox"
              className="absolute top-full left-0 z-50 mt-1 max-h-64 w-72 overflow-auto rounded-md border bg-popover p-1 shadow-md"
            >
              {searching && candidates.length === 0 ? (
                <p className="flex items-center gap-2 px-2 py-3 text-sm text-muted-foreground">
                  <Loader2 aria-hidden="true" className="size-4 animate-spin" />
                  搜索中…
                </p>
              ) : candidates.length === 0 ? (
                <p className="px-2 py-3 text-sm text-muted-foreground">
                  没有匹配的{subjectType === "user" ? "用户" : "用户组"}
                </p>
              ) : (
                candidates.map((c) => {
                  const already = entries.some(
                    (e) => e.subject_type === subjectType && e.subject_id === c.id,
                  );
                  return (
                    <button
                      key={c.id}
                      type="button"
                      role="option"
                      aria-selected={already}
                      disabled={already}
                      onClick={() => addEntry(c)}
                      className="flex w-full items-center justify-between gap-2 rounded-sm px-2 py-1.5 text-left text-sm hover:bg-accent disabled:opacity-50"
                    >
                      <span className="truncate">{c.label}</span>
                      <span className="shrink-0 text-xs text-muted-foreground">
                        {already ? "已添加" : c.hint}
                      </span>
                    </button>
                  );
                })
              )}
            </div>
          ) : null}
        </div>

        <Button
          type="button"
          variant="outline"
          size="sm"
          disabled={disabled || candidates.length === 0}
          onClick={() => {
            const first = candidates[0];
            if (first) addEntry(first);
          }}
        >
          <Plus aria-hidden="true" className="size-4" />
          添加
        </Button>

        <p className="w-full text-xs text-muted-foreground">
          按姓名 / 用户名搜索后选中即可添加；名称会自动回填，保存后立即在名单中可读。
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
