import { zodResolver } from "@hookform/resolvers/zod";
import { AlertCircle, ArrowLeft, KeyRound, Loader2 } from "lucide-react";
import * as React from "react";
import { useForm, useWatch } from "react-hook-form";
import { Link, useNavigate } from "react-router-dom";
import { toast } from "sonner";
import { z } from "zod";

import { changePassword } from "@/api/auth";
import { ApiError, getErrorMessage } from "@/api/client";
import { Alert, AlertDescription, AlertTitle } from "@/components/ui/alert";
import { Button } from "@/components/ui/button";
import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from "@/components/ui/card";
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
import { useAuth } from "@/hooks/useAuth";
import { evaluatePassword } from "@/lib/password";
import { cn } from "@/lib/utils";

const STRENGTH_BAR_CLASS: Record<number, string> = {
  1: "bg-destructive",
  2: "bg-warning",
  3: "bg-primary",
  4: "bg-success",
};

const changePasswordSchema = z
  .object({
    old_password: z.string().min(1, "请输入原密码"),
    new_password: z
      .string()
      .min(10, "新密码至少 10 个字符")
      .refine((value) => evaluatePassword(value).classes >= 3, {
        message: "需包含大写字母、小写字母、数字、符号中的至少 3 类",
      }),
    confirm_password: z.string().min(1, "请再次输入新密码"),
  })
  .refine((values) => values.new_password === values.confirm_password, {
    message: "两次输入的新密码不一致",
    path: ["confirm_password"],
  })
  .refine((values) => values.new_password !== values.old_password, {
    message: "新密码不能与原密码相同",
    path: ["new_password"],
  });

type ChangePasswordValues = z.infer<typeof changePasswordSchema>;

function StrengthMeter({ password }: { password: string }) {
  const strength = evaluatePassword(password);
  return (
    <div className="space-y-1.5" aria-live="polite">
      <div className="flex gap-1" role="presentation">
        {[1, 2, 3, 4].map((segment) => (
          <span
            key={segment}
            className={cn(
              "h-1.5 flex-1 rounded-full transition-colors",
              strength.score >= segment
                ? (STRENGTH_BAR_CLASS[strength.score] ?? "bg-muted")
                : "bg-muted",
            )}
          />
        ))}
      </div>
      <p className="text-xs text-muted-foreground">
        {password
          ? `强度：${strength.label}${strength.meetsPolicy ? "" : "（需 ≥10 位且含 3 类字符）"}`
          : "至少 10 位，且包含大写、小写、数字、符号中的 3 类"}
      </p>
    </div>
  );
}

/**
 * `/change-password` (docs/04 §6.2).
 *
 * Forced mode (`must_change_password === true`): no skip button, top-bar
 * navigation hidden (handled by AppShell/TopNav). Success clears local state and
 * sends the user back to /login on purpose — old sessions are revoked, and
 * re-login proves the new password works.
 */
export default function ChangePasswordPage() {
  const { user, clearLocalSession } = useAuth();
  const navigate = useNavigate();
  const [formError, setFormError] = React.useState<string | null>(null);
  const [submitting, setSubmitting] = React.useState(false);
  const forced = user?.must_change_password === true;

  const form = useForm<ChangePasswordValues>({
    resolver: zodResolver(changePasswordSchema),
    defaultValues: { old_password: "", new_password: "", confirm_password: "" },
    mode: "onSubmit",
  });

  // `useWatch` instead of `form.watch()`: same value, but it is a real hook so
  // React Compiler can memoise this component safely.
  const newPassword = useWatch({ control: form.control, name: "new_password" });

  async function onSubmit(values: ChangePasswordValues) {
    setFormError(null);
    setSubmitting(true);
    try {
      await changePassword({
        old_password: values.old_password,
        new_password: values.new_password,
      });
      toast.success("密码已更新，请重新登录");
      clearLocalSession();
      navigate("/login", { replace: true });
    } catch (error) {
      if (error instanceof ApiError && Object.keys(error.fieldErrors).length > 0) {
        for (const [field, message] of Object.entries(error.fieldErrors)) {
          if (
            field === "old_password" ||
            field === "new_password" ||
            field === "confirm_password"
          ) {
            form.setError(field, { type: "server", message });
          } else {
            setFormError(message);
          }
        }
      } else {
        setFormError(getErrorMessage(error));
      }
    } finally {
      setSubmitting(false);
    }
  }

  return (
    <div className="mx-auto w-full max-w-lg px-4 py-10 sm:px-6 lg:px-8">
      {!forced ? (
        <Button asChild variant="ghost" size="sm" className="mb-3 -ml-2">
          <Link to="/">
            <ArrowLeft aria-hidden="true" className="size-4" />
            返回首页
          </Link>
        </Button>
      ) : null}

      <Card>
        <CardHeader>
          <span className="grid size-9 place-items-center rounded-lg bg-primary/10 text-primary">
            <KeyRound aria-hidden="true" className="size-4" />
          </span>
          <CardTitle>
            {forced ? "请先修改初始密码" : "修改密码"}
            <span className="sr-only">（{forced ? "强制模式" : "主动模式"}）</span>
          </CardTitle>
          <CardDescription>
            {forced
              ? "为了账号安全，首次登录必须修改初始密码后才能使用平台功能。"
              : "修改后当前会话将失效，需要使用新密码重新登录。"}
          </CardDescription>
        </CardHeader>

        <CardContent>
          {formError ? (
            <Alert variant="destructive" className="mb-4">
              <AlertCircle aria-hidden="true" />
              <AlertTitle>修改失败</AlertTitle>
              <AlertDescription>{formError}</AlertDescription>
            </Alert>
          ) : null}

          <Form {...form}>
            <form
              onSubmit={(event) => {
                void form.handleSubmit(onSubmit)(event);
              }}
              className="space-y-5"
              noValidate
            >
              <FormField
                control={form.control}
                name="old_password"
                render={({ field }) => (
                  <FormItem>
                    <FormLabel>{forced ? "初始密码" : "原密码"}</FormLabel>
                    <FormControl>
                      <Input
                        {...field}
                        type="password"
                        autoComplete="current-password"
                        autoFocus
                        disabled={submitting}
                      />
                    </FormControl>
                    <FormMessage />
                  </FormItem>
                )}
              />

              <FormField
                control={form.control}
                name="new_password"
                render={({ field }) => (
                  <FormItem>
                    <FormLabel>新密码</FormLabel>
                    <FormControl>
                      <Input
                        {...field}
                        type="password"
                        autoComplete="new-password"
                        disabled={submitting}
                      />
                    </FormControl>
                    <StrengthMeter password={newPassword} />
                    <FormMessage />
                  </FormItem>
                )}
              />

              <FormField
                control={form.control}
                name="confirm_password"
                render={({ field }) => (
                  <FormItem>
                    <FormLabel>确认新密码</FormLabel>
                    <FormControl>
                      <Input
                        {...field}
                        type="password"
                        autoComplete="new-password"
                        disabled={submitting}
                      />
                    </FormControl>
                    <FormDescription>两次输入需完全一致</FormDescription>
                    <FormMessage />
                  </FormItem>
                )}
              />

              <div className="flex items-center gap-2">
                <Button type="submit" disabled={submitting}>
                  {submitting ? (
                    <>
                      <Loader2 aria-hidden="true" className="size-4 animate-spin" />
                      提交中…
                    </>
                  ) : (
                    "确认修改"
                  )}
                </Button>
                {!forced ? (
                  <Button asChild type="button" variant="outline" disabled={submitting}>
                    <Link to="/">取消</Link>
                  </Button>
                ) : null}
              </div>
            </form>
          </Form>
        </CardContent>
      </Card>
    </div>
  );
}
