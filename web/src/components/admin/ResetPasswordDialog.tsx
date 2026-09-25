import { zodResolver } from "@hookform/resolvers/zod";
import { useMutation } from "@tanstack/react-query";
import { Info, Sparkles } from "lucide-react";
import * as React from "react";
import { useForm } from "react-hook-form";
import { z } from "zod";

import { resetAdminUserPassword } from "@/api/admin";
import { getErrorMessage } from "@/api/client";
import type { AdminUserItem, ResetPasswordResponse } from "@/api/types";
import { generateStrongPassword } from "@/components/admin/UserFormDialog";
import { CopyButton } from "@/components/common/CopyButton";
import { Alert, AlertDescription, AlertTitle } from "@/components/ui/alert";
import { Button } from "@/components/ui/button";
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
import { evaluatePassword } from "@/lib/password";

/**
 * 重置用户密码（docs/04 §6.13，FR-IAM-03）。
 *
 * 可手填新密码或点「生成」；服务端留空时会自己生成一个并回显一次。
 * 重置会**顺带吊销该用户全部会话**，所以成功后必须把 `revoked_sessions`
 * 明确告诉管理员（否则「他怎么还能操作」会变成排查负担）。
 */

const resetSchema = z
  .object({ password: z.string() })
  .superRefine((values, ctx) => {
    if (values.password && !evaluatePassword(values.password).meetsPolicy) {
      ctx.addIssue({
        code: z.ZodIssueCode.custom,
        path: ["password"],
        message: "密码至少 10 位，且包含大写字母、小写字母、数字、符号中的至少 3 类",
      });
    }
  });

type ResetFormValues = z.infer<typeof resetSchema>;

export interface ResetPasswordDialogProps {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  user: AdminUserItem | null;
  /** 成功后刷新列表（`must_change_password` 会变化）。 */
  onDone: () => void;
}

export function ResetPasswordDialog({
  open,
  onOpenChange,
  user,
  onDone,
}: ResetPasswordDialogProps) {
  const [result, setResult] = React.useState<ResetPasswordResponse | null>(null);
  /** 手填的密码也要「展示一次」，便于复制给用户（服务端此时不回显明文）。 */
  const [submittedPassword, setSubmittedPassword] = React.useState<string | null>(null);
  const [errorMessage, setErrorMessage] = React.useState<string | null>(null);

  const form = useForm<ResetFormValues>({
    resolver: zodResolver(resetSchema),
    defaultValues: { password: "" },
  });

  // 父组件用 `key` 在每次打开时重新挂载本组件，`result` / `errorMessage` 因此
  // 天然是全新的，不需要在 effect 里重置（一次性密码也不会重复显示）。

  const resetMutation = useMutation({
    mutationFn: (values: ResetFormValues) => {
      if (!user) throw new Error("缺少重置目标用户");
      return resetAdminUserPassword(user.id, {
        password: values.password ? values.password : null,
      });
    },
    onSuccess: (response, variables) => {
      setResult(response);
      setSubmittedPassword(variables.password);
      onDone();
    },
    onError: (error) => setErrorMessage(getErrorMessage(error)),
  });

  const displayName = user?.display_name ?? user?.username ?? "";
  const plainText = result?.generated_password ?? (submittedPassword || null);

  return (
    <Dialog
      open={open}
      onOpenChange={(next) => {
        onOpenChange(next);
        if (!next) setResult(null);
      }}
    >
      <DialogContent
        data-testid="reset-password-dialog"
        className="max-h-[calc(100vh-2rem)] overflow-y-auto sm:max-w-md"
      >
        <DialogHeader>
          <DialogTitle>重置「{displayName}」的密码</DialogTitle>
          <DialogDescription>
            重置后该用户的全部登录会话会立即失效，并按策略要求其下次登录修改密码。
          </DialogDescription>
        </DialogHeader>

        {result ? (
          <Alert>
            <Info aria-hidden="true" />
            <AlertTitle>密码已重置</AlertTitle>
            <AlertDescription>
              <div className="mt-2 space-y-2">
                <div className="flex items-center gap-2 text-sm">
                  <span className="text-muted-foreground">用户名</span>
                  <code className="rounded bg-muted px-1.5 py-0.5 font-mono text-xs">
                    {user?.username ?? ""}
                  </code>
                </div>
                {plainText ? (
                  <div className="flex items-center gap-2 text-sm">
                    <span className="text-muted-foreground">新密码</span>
                    <code
                      data-testid="generated-password"
                      className="rounded bg-muted px-1.5 py-0.5 font-mono text-xs"
                    >
                      {plainText}
                    </code>
                    <CopyButton
                      value={plainText}
                      label="复制新密码"
                      testId="copy-generated-password"
                    />
                  </div>
                ) : null}
                {plainText ? (
                  <p className="text-xs text-muted-foreground">
                    请复制并告知用户，此密码不会再次显示。
                  </p>
                ) : null}
                <p className="text-xs text-muted-foreground">
                  已下线会话数：{result.revoked_sessions}
                  {result.must_change_password ? " · 该用户下次登录需修改密码" : ""}
                </p>
              </div>
            </AlertDescription>
          </Alert>
        ) : (
          <Form {...form}>
            <form
              className="grid gap-4"
              onSubmit={form.handleSubmit((values) => {
                setErrorMessage(null);
                resetMutation.mutate(values);
              })}
            >
              <FormField
                control={form.control}
                name="password"
                render={({ field }) => (
                  <FormItem>
                    <FormLabel>新密码</FormLabel>
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
                      至少 10 位，且包含大写、小写、数字、符号中的 3 类。
                    </FormDescription>
                    <FormMessage />
                  </FormItem>
                )}
              />

              {errorMessage ? (
                <Alert variant="destructive" data-testid="reset-password-error">
                  <AlertTitle>重置失败</AlertTitle>
                  <AlertDescription>{errorMessage}</AlertDescription>
                </Alert>
              ) : null}

              <DialogFooter>
                <Button type="button" variant="outline" onClick={() => onOpenChange(false)}>
                  取消
                </Button>
                <Button type="submit" disabled={resetMutation.isPending}>
                  {resetMutation.isPending ? "重置中…" : "重置密码"}
                </Button>
              </DialogFooter>
            </form>
          </Form>
        )}

        {result ? (
          <DialogFooter>
            <Button type="button" onClick={() => onOpenChange(false)}>
              我已保存密码，关闭
            </Button>
          </DialogFooter>
        ) : null}
      </DialogContent>
    </Dialog>
  );
}

export default ResetPasswordDialog;
