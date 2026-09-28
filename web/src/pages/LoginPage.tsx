import { zodResolver } from "@hookform/resolvers/zod";
import { AlertCircle, Boxes, Eye, EyeOff, Loader2 } from "lucide-react";
import * as React from "react";
import { useForm } from "react-hook-form";
import { Navigate, useNavigate, useSearchParams } from "react-router-dom";
import { z } from "zod";

import { ApiError, getErrorMessage } from "@/api/client";
import { FullScreenLoader } from "@/components/common/PageSkeleton";
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
  FormField,
  FormItem,
  FormLabel,
  FormMessage,
} from "@/components/ui/form";
import { Input } from "@/components/ui/input";
import { useAuth } from "@/hooks/useAuth";
import { siteName, siteSubtitle, useMeta } from "@/hooks/useMeta";
import { formatCountdown } from "@/lib/format";
import { safeRedirectPath } from "@/lib/navigation";

const loginSchema = z.object({
  username: z.string().trim().min(1, "请输入用户名"),
  password: z.string().min(1, "请输入密码"),
});

type LoginValues = z.infer<typeof loginSchema>;

/**
 * `/login` (docs/04 §6.1).
 *
 * Behaviour contracts worth protecting:
 *  - wrong credentials do NOT clear the password box; the field is re-focused
 *    and its content selected, so a typo costs one keystroke to fix
 *  - account lock shows a live countdown and disables submit until it expires
 *  - autoComplete is `username` / `current-password`, never a fake text input
 *  - an already-authenticated visitor is redirected away instead of seeing the form
 */
export default function LoginPage() {
  const { status, user, login } = useAuth();
  const { data: meta } = useMeta();
  const subtitle = siteSubtitle(meta);
  const navigate = useNavigate();
  const [searchParams] = useSearchParams();
  const redirectTo = safeRedirectPath(searchParams.get("redirect"), "/");

  const [formError, setFormError] = React.useState<string | null>(null);
  const [showPassword, setShowPassword] = React.useState(false);
  const [lockedSeconds, setLockedSeconds] = React.useState(0);
  const [submitting, setSubmitting] = React.useState(false);

  const form = useForm<LoginValues>({
    resolver: zodResolver(loginSchema),
    defaultValues: { username: "", password: "" },
    mode: "onSubmit",
  });

  /* Tick the lock countdown down to zero (docs/04 §6.1). */
  React.useEffect(() => {
    if (lockedSeconds <= 0) return;
    const timer = window.setInterval(() => {
      setLockedSeconds((previous) => (previous <= 1 ? 0 : previous - 1));
    }, 1000);
    return () => window.clearInterval(timer);
  }, [lockedSeconds]);

  const locked = lockedSeconds > 0;

  if (status === "unknown") {
    return <FullScreenLoader label="正在检查登录状态…" />;
  }

  if (status === "authenticated") {
    const target = user?.must_change_password ? "/change-password" : redirectTo;
    return <Navigate to={target} replace />;
  }

  async function onSubmit(values: LoginValues) {
    setFormError(null);
    setSubmitting(true);
    try {
      const loggedIn = await login(values.username, values.password);
      if (loggedIn.must_change_password) {
        navigate("/change-password", { replace: true });
        return;
      }
      navigate(redirectTo, { replace: true });
    } catch (error) {
      if (error instanceof ApiError) {
        if (error.is("VALIDATION_ERROR") && Object.keys(error.fieldErrors).length > 0) {
          for (const [field, message] of Object.entries(error.fieldErrors)) {
            if (field === "username" || field === "password") {
              form.setError(field, { type: "server", message });
            }
          }
          setFormError(error.message);
        } else if (error.is("ACCOUNT_LOCKED")) {
          setLockedSeconds(error.retryAfterSeconds ?? 900);
          setFormError(
            `账号已锁定，请稍后重试。${error.retryAfterSeconds ? `（锁定 ${Math.ceil(error.retryAfterSeconds / 60)} 分钟）` : ""}`,
          );
        } else if (error.is("ACCOUNT_DISABLED")) {
          setFormError("账号已禁用，请联系管理员");
        } else if (error.is("INVALID_CREDENTIALS")) {
          setFormError("用户名或密码错误");
          // Wrong password must NOT be wiped: keep the value, select it, so the
          // user can retype immediately.
          form.setFocus("password", { shouldSelect: true });
        } else {
          setFormError(getErrorMessage(error));
        }
      } else {
        setFormError(getErrorMessage(error));
      }
    } finally {
      setSubmitting(false);
    }
  }

  return (
    <div
      data-testid="login-page"
      className="grid min-h-screen place-items-center bg-gradient-to-br from-background to-accent/30 px-4 py-10"
    >
      <Card className="w-full max-w-sm">
        <CardHeader className="items-center text-center">
          <span className="mx-auto grid size-10 place-items-center rounded-xl bg-primary text-primary-foreground">
            <Boxes aria-hidden="true" className="size-5" />
          </span>
          <CardTitle className="text-xl">{siteName(meta)}</CardTitle>
          {/* M12 · F1：副标题在站点名下方一行（CONTRACT §27.4）。为空时整行不渲染。 */}
          {subtitle ? (
            <p data-testid="site-subtitle" className="text-sm text-muted-foreground">
              {subtitle}
            </p>
          ) : null}
          <CardDescription>使用内网账号登录，浏览与下载工具、Skill 和提示词</CardDescription>
        </CardHeader>

        <CardContent>
          {formError ? (
            <Alert variant="destructive" className="mb-4">
              <AlertCircle aria-hidden="true" />
              <AlertTitle>{locked ? "账号已锁定" : "登录失败"}</AlertTitle>
              <AlertDescription>
                {formError}
                {locked ? ` 请 ${formatCountdown(lockedSeconds)} 后重试。` : ""}
              </AlertDescription>
            </Alert>
          ) : null}

          <Form {...form}>
            <form
              onSubmit={(event) => {
                void form.handleSubmit(onSubmit)(event);
              }}
              className="space-y-4"
              noValidate
            >
              <FormField
                control={form.control}
                name="username"
                render={({ field }) => (
                  <FormItem>
                    <FormLabel>用户名</FormLabel>
                    <FormControl>
                      <Input
                        {...field}
                        autoFocus
                        autoComplete="username"
                        autoCapitalize="none"
                        spellCheck={false}
                        disabled={locked || submitting}
                        placeholder="请输入用户名"
                      />
                    </FormControl>
                    <FormMessage />
                  </FormItem>
                )}
              />

              <FormField
                control={form.control}
                name="password"
                render={({ field }) => (
                  <FormItem>
                    <FormLabel>密码</FormLabel>
                    {/* FormControl must wrap the input directly: it injects the
                        id/aria-describedby that link <FormLabel> to the field. */}
                    <div className="relative">
                      <FormControl>
                        <Input
                          {...field}
                          type={showPassword ? "text" : "password"}
                          autoComplete="current-password"
                          disabled={locked || submitting}
                          placeholder="请输入密码"
                          className="pr-10"
                        />
                      </FormControl>
                      <Button
                        type="button"
                        variant="ghost"
                        size="icon"
                        className="absolute right-0 top-0 size-9"
                        aria-label={showPassword ? "隐藏密码" : "显示密码"}
                        aria-pressed={showPassword}
                        onClick={() => setShowPassword((previous) => !previous)}
                      >
                        {showPassword ? (
                          <EyeOff aria-hidden="true" className="size-4" />
                        ) : (
                          <Eye aria-hidden="true" className="size-4" />
                        )}
                      </Button>
                    </div>
                    <FormMessage />
                  </FormItem>
                )}
              />

              <Button type="submit" className="w-full" disabled={submitting || locked}>
                {submitting ? (
                  <>
                    <Loader2 aria-hidden="true" className="size-4 animate-spin" />
                    登录中…
                  </>
                ) : locked ? (
                  `账号锁定中（${formatCountdown(lockedSeconds)}）`
                ) : (
                  "登录"
                )}
              </Button>
            </form>
          </Form>

          <p className="mt-4 text-center text-xs text-muted-foreground">
            使用管理员分配的账号登录，忘记密码请联系管理员
          </p>
        </CardContent>
      </Card>
    </div>
  );
}
