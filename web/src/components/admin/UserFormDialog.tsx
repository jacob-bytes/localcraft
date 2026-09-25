import { zodResolver } from "@hookform/resolvers/zod";
import { useMutation } from "@tanstack/react-query";
import { Info, Sparkles } from "lucide-react";
import * as React from "react";
import { useForm } from "react-hook-form";
import { toast } from "sonner";
import { z } from "zod";

import {
  createAdminUser,
  replaceAdminUserRoles,
  updateAdminUser,
} from "@/api/admin";
import { getErrorMessage, isApiError } from "@/api/client";
import type {
  AdminUserCreateRequest,
  AdminUserCreateResponse,
  AdminUserItem,
  AdminUserUpdateRequest,
  Role,
  RoleOut,
} from "@/api/types";
import { CopyButton } from "@/components/common/CopyButton";
import { Alert, AlertDescription, AlertTitle } from "@/components/ui/alert";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Checkbox } from "@/components/ui/checkbox";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import {
  Form,
  FormControl,
  FormDescription,
  FormField,
  FormItem,
  FormLabel,
  FormMessage,
} from "@/components/ui/form";
import { Input } from "@/components/ui/input";
import { Switch } from "@/components/ui/switch";
import { evaluatePassword } from "@/lib/password";
import { ROLE_LABELS } from "@/lib/permissions";

/**
 * 新建 / 编辑用户（docs/04 §6.13，FR-IAM-01/02/07）。
 *
 * 两处「服务端权威」的落点：
 *  1. 角色勾选项来自 `GET /admin/roles`（`RoleOut.name`/`description`），不写死中文名。
 *  2. 创建失败时把 `ApiError.fieldErrors`（= `details.fields`）逐字段挂到表单；
 *     降级最后一个超管（`LAST_SUPERADMIN`）给出明确文案（FR-IAM-07）。
 *
 * 用户名创建后不可改（FR-IAM-01），编辑模式输入框 `disabled` 并给出说明。
 * 一次性初始密码只在这里展示一次，且**绝不写日志**（`console.log` 会泄漏明文）。
 */

const ROLE_CODES = ["viewer", "user", "approver", "superadmin"] as const;
const roleSchema = z.enum(ROLE_CODES);

export type UserFormMode = "create" | "edit";

const userFormSchema = z
  .object({
    username: z.string().trim().max(64, "用户名最多 64 个字符"),
    display_name: z.string().trim().min(1, "请填写显示名").max(128, "显示名最多 128 个字符"),
    email: z
      .string()
      .trim()
      .max(255, "邮箱最多 255 个字符")
      .email("请输入有效的邮箱地址")
      .or(z.literal("")),
    roles: z.array(roleSchema).min(1, "至少选择一个角色"),
    password: z.string(),
    must_change_password: z.boolean(),
  })
  .superRefine((values, ctx) => {
    // 用户名只在新建时校验：编辑模式它不可改，也不提交。
    if (values.username.length < 3) {
      ctx.addIssue({
        code: z.ZodIssueCode.custom,
        path: ["username"],
        message: "用户名至少 3 个字符",
      });
    } else if (!/^[A-Za-z0-9_-]+$/.test(values.username)) {
      ctx.addIssue({
        code: z.ZodIssueCode.custom,
        path: ["username"],
        message: "只能包含字母、数字、下划线和连字符",
      });
    }
    if (values.password && !evaluatePassword(values.password).meetsPolicy) {
      ctx.addIssue({
        code: z.ZodIssueCode.custom,
        path: ["password"],
        message: "密码至少 10 位，且包含大写字母、小写字母、数字、符号中的至少 3 类",
      });
    }
  });

export type UserFormValues = z.infer<typeof userFormSchema>;

const PASSWORD_ALPHABET = {
  lower: "abcdefghijkmnpqrstuvwxyz",
  upper: "ABCDEFGHJKLMNPQRSTUVWXYZ",
  digit: "23456789",
  symbol: "!@#$%^&*-_=+",
} as const;

/**
 * 本地生成 12 位强密码（至少各含一个小写/大写/数字/符号）。
 * 用 `crypto.getRandomValues` 而不是 `Math.random`，避免可预测的初始口令。
 */
export function generateStrongPassword(length = 12): string {
  const classes = [
    PASSWORD_ALPHABET.lower,
    PASSWORD_ALPHABET.upper,
    PASSWORD_ALPHABET.digit,
    PASSWORD_ALPHABET.symbol,
  ];
  const all = classes.join("");
  const picks: string[] = [];

  const randomIndex = (max: number): number => {
    const buffer = new Uint32Array(1);
    crypto.getRandomValues(buffer);
    return (buffer[0] ?? 0) % max;
  };

  for (const alphabet of classes) {
    picks.push(alphabet.charAt(randomIndex(alphabet.length)));
  }
  while (picks.length < Math.max(length, classes.length)) {
    picks.push(all.charAt(randomIndex(all.length)));
  }
  // Fisher–Yates 洗牌，避免「前 4 位必然是各类字符」的固定结构。
  for (let index = picks.length - 1; index > 0; index -= 1) {
    const swap = randomIndex(index + 1);
    const current = picks[index] ?? "";
    picks[index] = picks[swap] ?? "";
    picks[swap] = current;
  }
  return picks.join("");
}

/** 编辑时「资料已保存、角色未保存」需要单独提示，不能笼统报失败。 */
class PartialSaveError extends Error {
  readonly roleError: unknown;

  constructor(roleError: unknown) {
    super("角色更新失败");
    this.name = "PartialSaveError";
    this.roleError = roleError;
  }
}

const MAPPABLE_FIELDS = ["username", "display_name", "email", "password", "roles"] as const;
type MappableField = (typeof MAPPABLE_FIELDS)[number];

function isMappableField(field: string): field is MappableField {
  return (MAPPABLE_FIELDS as readonly string[]).includes(field);
}

function sameRoles(left: readonly string[], right: readonly string[]): boolean {
  if (left.length !== right.length) return false;
  const sortedLeft = [...left].sort();
  const sortedRight = [...right].sort();
  return sortedLeft.every((value, index) => value === sortedRight[index]);
}

/** 保存结果：新建（可能带一次性密码）与编辑（是否有实际改动）分开处理。 */
type SaveOutcome =
  | { kind: "created"; response: AdminUserCreateResponse }
  | { kind: "updated"; changed: boolean };

export interface UserFormDialogProps {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  mode: UserFormMode;
  /** 编辑目标；`create` 模式为 `null`。 */
  user: AdminUserItem | null;
  /** `GET /admin/roles` 的结果（默认角色来自服务端，前端不写死）。 */
  roles: readonly RoleOut[];
  rolesPending: boolean;
  /** 保存成功后刷新列表（创建成功的一次性密码由本组件展示）。 */
  onSaved: () => void;
}

export function UserFormDialog({
  open,
  onOpenChange,
  mode,
  user,
  roles,
  rolesPending,
  onSaved,
}: UserFormDialogProps) {
  const [created, setCreated] = React.useState<{
    username: string;
    password: string;
  } | null>(null);
  const [submitError, setSubmitError] = React.useState<string | null>(null);

  const defaults = React.useMemo<UserFormValues>(
    () => ({
      username: user?.username ?? "",
      display_name: user?.display_name ?? "",
      email: user?.email ?? "",
      roles: (user?.roles ?? []).filter((code): code is Role =>
        (ROLE_CODES as readonly string[]).includes(code),
      ),
      password: "",
      must_change_password: user?.must_change_password ?? true,
    }),
    [user],
  );

  const form = useForm<UserFormValues>({
    resolver: zodResolver(userFormSchema),
    defaultValues: defaults,
  });

  // 打开时由父组件换 `key` 重新挂载本组件，所以这里不需要 effect 重置表单
  // （也避免在 effect 里同步 setState 造成额外渲染回合）。

  const saveMutation = useMutation({
    mutationFn: async (values: UserFormValues): Promise<SaveOutcome> => {
      if (mode === "create") {
        const payload: AdminUserCreateRequest = {
          username: values.username.trim(),
          display_name: values.display_name.trim(),
          email: values.email.trim() ? values.email.trim() : null,
          password: values.password ? values.password : null,
          roles: values.roles,
          must_change_password: values.must_change_password,
        };
        return { kind: "created", response: await createAdminUser(payload) };
      }

      if (!user) throw new Error("缺少编辑目标用户");

      const patch: AdminUserUpdateRequest = {};
      if (values.display_name.trim() !== user.display_name) {
        patch.display_name = values.display_name.trim();
      }
      const nextEmail = values.email.trim() ? values.email.trim() : null;
      if (nextEmail !== user.email) patch.email = nextEmail;

      const profileChanged = Object.keys(patch).length > 0;
      const rolesChanged = !sameRoles(values.roles, user.roles);

      // 先改资料再换角色：角色失败时前端能明确说「资料已保存」，
      // 而不是让管理员以为整次保存都没生效。
      if (profileChanged) await updateAdminUser(user.id, patch);
      if (rolesChanged) {
        try {
          await replaceAdminUserRoles(user.id, { roles: values.roles });
        } catch (error) {
          if (profileChanged) throw new PartialSaveError(error);
          throw error;
        }
      }
      return { kind: "updated", changed: profileChanged || rolesChanged };
    },
    onSuccess: (outcome, values) => {
      onSaved();
      if (outcome.kind === "created") {
        if (outcome.response.generated_password) {
          // 只在界面上展示一次；不写 console（明文密码不进日志）。
          setCreated({
            username: values.username.trim(),
            password: outcome.response.generated_password,
          });
          return;
        }
        toast.success("用户已创建");
        onOpenChange(false);
        return;
      }
      if (!outcome.changed) {
        toast.message("没有需要保存的修改");
      } else {
        toast.success("已保存用户修改");
      }
      onOpenChange(false);
    },
    onError: (error) => {
      if (error instanceof PartialSaveError) {
        toast.warning("资料已保存，但角色更新失败，请重试角色修改", {
          description: getErrorMessage(error.roleError),
        });
        onSaved();
        return;
      }
      if (isApiError(error)) {
        if (error.is("LAST_SUPERADMIN")) {
          form.setError("roles", { message: "不能禁用或降级最后一个超级管理员" });
          return;
        }
        // VALIDATION_ERROR 的 `details.fields` → 逐字段挂到表单（client.ts 已解析好）。
        let mapped = false;
        for (const [field, message] of Object.entries(error.fieldErrors)) {
          if (!isMappableField(field)) continue;
          form.setError(field, { message });
          mapped = true;
        }
        if (!mapped) setSubmitError(getErrorMessage(error));
        return;
      }
      setSubmitError(getErrorMessage(error));
    },
  });

  const roleOptions = roles.length > 0 ? roles : [];

  return (
    <Dialog
      open={open}
      onOpenChange={(next) => {
        onOpenChange(next);
        if (!next) setCreated(null);
      }}
    >
      {/* 1280×720（docs/04 §8.2 管理台基准）下这个表单比视口高，
          必须限高 + 内部滚动，否则底部「创建/保存」按钮点不到。 */}
      <DialogContent
        data-testid="user-form-dialog"
        className="max-h-[calc(100vh-2rem)] overflow-y-auto sm:max-w-lg"
      >
        <DialogHeader>
          <DialogTitle>{mode === "create" ? "新建用户" : `编辑用户「${user?.username ?? ""}」`}</DialogTitle>
          <DialogDescription>
            {mode === "create"
              ? "创建后用户名不可修改；账号只支持禁用，平台会保留其历史记录以便审计。"
              : "可修改显示名与邮箱；用户名创建后不可修改。"}
          </DialogDescription>
        </DialogHeader>

        {created ? (
          /* 一次性初始密码：只读展示 + 复制，关闭后不可再次获取。 */
          <Alert>
            <Info aria-hidden="true" />
            <AlertTitle>用户已创建</AlertTitle>
            <AlertDescription>
              <div className="mt-2 space-y-2">
                <div className="flex items-center gap-2 text-sm">
                  <span className="text-muted-foreground">用户名</span>
                  <code className="rounded bg-muted px-1.5 py-0.5 font-mono text-xs">
                    {created.username}
                  </code>
                </div>
                <div className="flex items-center gap-2 text-sm">
                  <span className="text-muted-foreground">初始密码</span>
                  <code
                    data-testid="generated-password"
                    className="rounded bg-muted px-1.5 py-0.5 font-mono text-xs"
                  >
                    {created.password}
                  </code>
                  <CopyButton
                    value={created.password}
                    label="复制初始密码"
                    testId="copy-generated-password"
                  />
                </div>
                <p className="text-xs text-muted-foreground">
                  请复制并告知用户，此密码不会再次显示。
                </p>
              </div>
            </AlertDescription>
          </Alert>
        ) : (
          <Form {...form}>
            <form
              className="grid gap-4"
              onSubmit={form.handleSubmit((values) => {
                setSubmitError(null);
                saveMutation.mutate(values);
              })}
            >
              <FormField
                control={form.control}
                name="username"
                render={({ field }) => (
                  <FormItem>
                    <FormLabel>用户名</FormLabel>
                    <FormControl>
                      <Input
                        autoComplete="off"
                        placeholder="如 zhang-san"
                        disabled={mode === "edit"}
                        {...field}
                      />
                    </FormControl>
                    <FormDescription data-testid="username-readonly-hint">
                      {mode === "edit"
                        ? "用户名创建后不可修改"
                        : "仅字母、数字、下划线与连字符，长度 3~64。"}
                    </FormDescription>
                    <FormMessage />
                  </FormItem>
                )}
              />

              <FormField
                control={form.control}
                name="display_name"
                render={({ field }) => (
                  <FormItem>
                    <FormLabel>显示名</FormLabel>
                    <FormControl>
                      <Input placeholder="如 张三" {...field} />
                    </FormControl>
                    <FormMessage />
                  </FormItem>
                )}
              />

              <FormField
                control={form.control}
                name="email"
                render={({ field }) => (
                  <FormItem>
                    <FormLabel>邮箱（选填）</FormLabel>
                    <FormControl>
                      <Input type="email" placeholder="如 zhangsan@example.com" {...field} />
                    </FormControl>
                    <FormMessage />
                  </FormItem>
                )}
              />

              <FormField
                control={form.control}
                name="roles"
                render={({ field }) => (
                  <FormItem>
                    <FormLabel>角色</FormLabel>
                    <div className="grid gap-2">
                      {rolesPending && roleOptions.length === 0 ? (
                        <p className="text-sm text-muted-foreground">角色加载中…</p>
                      ) : (
                        roleOptions.map((role) => {
                          const checkboxId = `user-role-${role.code}`;
                          const checked = field.value.includes(role.code);
                          return (
                            <div key={role.code} className="flex items-start gap-2">
                              <Checkbox
                                id={checkboxId}
                                checked={checked}
                                onCheckedChange={(value) => {
                                  const next =
                                    value === true
                                      ? [...field.value, role.code]
                                      : field.value.filter((code) => code !== role.code);
                                  field.onChange(next);
                                }}
                              />
                              <div className="grid gap-0.5">
                                <label
                                  htmlFor={checkboxId}
                                  className="flex items-center gap-2 text-sm font-medium"
                                >
                                  {role.name}
                                  {role.is_builtin ? (
                                    <Badge variant="outline">内置</Badge>
                                  ) : null}
                                </label>
                                {role.description ? (
                                  <span className="text-xs text-muted-foreground">
                                    {role.description}
                                  </span>
                                ) : (
                                  <span className="text-xs text-muted-foreground">
                                    {ROLE_LABELS[role.code]}
                                  </span>
                                )}
                              </div>
                            </div>
                          );
                        })
                      )}
                    </div>
                    <FormMessage />
                  </FormItem>
                )}
              />

              {mode === "create" ? (
                <>
                  <FormField
                    control={form.control}
                    name="password"
                    render={({ field }) => (
                      <FormItem>
                        <FormLabel>初始密码</FormLabel>
                        <div className="flex gap-2">
                          <FormControl>
                            <Input
                              type="text"
                              autoComplete="new-password"
                              placeholder="留空则由服务端生成"
                              className="font-mono"
                              {...field}
                            />
                          </FormControl>
                          <Button
                            type="button"
                            variant="outline"
                            onClick={() => {
                              field.onChange(generateStrongPassword());
                              void form.trigger("password");
                            }}
                          >
                            <Sparkles aria-hidden="true" className="size-4" />
                            生成
                          </Button>
                        </div>
                        <FormDescription>
                          至少 10 位，且包含大写、小写、数字、符号中的 3 类；留空则由服务端生成。
                        </FormDescription>
                        <FormMessage />
                      </FormItem>
                    )}
                  />

                  <FormField
                    control={form.control}
                    name="must_change_password"
                    render={({ field }) => (
                      <FormItem className="flex flex-row items-center justify-between rounded-lg border p-3">
                        <div className="space-y-0.5">
                          <FormLabel>要求用户首次登录时修改密码</FormLabel>
                          <FormDescription>建议保持开启。</FormDescription>
                        </div>
                        <FormControl>
                          <Switch
                            checked={field.value}
                            onCheckedChange={field.onChange}
                            aria-label="要求用户首次登录时修改密码"
                          />
                        </FormControl>
                      </FormItem>
                    )}
                  />
                </>
              ) : null}

              {submitError ? (
                <Alert variant="destructive" data-testid="user-form-error">
                  <AlertTitle>保存失败</AlertTitle>
                  <AlertDescription>{submitError}</AlertDescription>
                </Alert>
              ) : null}

              <DialogFooter>
                <Button type="button" variant="outline" onClick={() => onOpenChange(false)}>
                  取消
                </Button>
                <Button type="submit" disabled={saveMutation.isPending}>
                  {saveMutation.isPending ? "保存中…" : mode === "create" ? "创建" : "保存"}
                </Button>
              </DialogFooter>
            </form>
          </Form>
        )}

        {created ? (
          <DialogFooter>
            <Button
              type="button"
              onClick={() => {
                onOpenChange(false);
                setCreated(null);
              }}
            >
              我已保存密码，关闭
            </Button>
          </DialogFooter>
        ) : null}
      </DialogContent>
    </Dialog>
  );
}

export default UserFormDialog;
