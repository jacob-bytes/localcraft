import { useMeta } from "@/hooks/useMeta";

/**
 * M12 · F2 —— 站点页脚（CONTRACT §27.2 / §27.3 / §27.4）。
 *
 * 三件事是这个组件存在的理由，改动前先读一遍：
 *
 * 1. **范围**：页脚**只出现在门户列表页与工具详情页**（§27.4 用户裁定）。
 *    它因此不挂在某条页面自己的 JSX 里，而是由 `AppShell` 按路由 `handle`
 *    （`siteFooter: true`）判断、并渲染在 `<main>` **之外** —— 这样 `<footer>`
 *    才是真正的 `contentinfo` 地标（ARIA 规定 `footer` 落在 `main` 之内时不映射
 *    地标角色），登录页 / 个人中心 / 管理台则一个 `<footer>` 都没有。
 *
 * 2. **★ 四个配置字段全空 ⇒ 整个页脚不渲染**（§27.4）。判断只看
 *    `footer_org` / `footer_contact_email` / `footer_contact_phone` /
 *    `footer_notice` —— **版本号不算依据**，否则每个既有部署都会凭 `app_version`
 *    多出一块页脚，违背 §27.2「不配置时视觉零变化」。只含空白（`"   "`）的值按
 *    未配置处理：渲染出来只是一块看不见的空隙。
 *
 * 3. **★ 邮箱 / 电话防御性降级**（§27.4）。管理员填错一个字符，不允许让页脚出现
 *    一个点不开的 `mailto:` / `tel:`，也不允许整个页脚崩掉：值不像邮箱 / 电话时
 *    **退化为纯文本**（内容照旧可见，只是不可点）。
 *
 * 取值全部走结构化字段而不是 Markdown —— §27.5 约束 1：Markdown 的
 * `ALLOWED_URI_REGEXP` 只放行 `https?` 与 `mailto:`，站内链接的 href 会被剥掉，
 * 而且排版不受控。**本轮不要为了页脚去放宽 sanitizer。**
 */

/**
 * 邮箱判定：保守到「宁可退化为纯文本」。
 *
 * 明确拒绝：空格、中文（`运维@example.com`）、多个 `@`、缺少点号的域名、以及
 * 任何非 ASCII 字符。本地部分与域名部分都只接受常见字符集，因此通过判定的值
 * 拼进 `mailto:` 不会产生需要转义的字符（也就不存在拼出坏 href 的空间）。
 */
const EMAIL_PATTERN =
  /^[A-Za-z0-9._%+-]+@[A-Za-z0-9](?:[A-Za-z0-9-]*[A-Za-z0-9])?(?:\.[A-Za-z0-9](?:[A-Za-z0-9-]*[A-Za-z0-9])?)+$/;

/**
 * 电话判定：数字与 `-`，可选前导 `+`，总长 3~20 个字符。
 *
 * `-` 是 RFC 3966 允许的视觉分隔符，所以 `tel:010-8888-6666` 是合法 URI。
 * **空格、括号、中文一律判为「不像电话」**（§27.4 明确把含空格 / 中文列为退化
 * 条件）——代价是 `+86 138…` 这类写法会退化成纯文本，但仍然可读，比渲染一个
 * 点不开的链接好。
 */
const PHONE_PATTERN = /^\+?[0-9](?:[0-9-]{1,18}[0-9])?$/;

/** 语义令牌化的链接样式：与站内其它文字链接一致（primary + hover 下划线 + 焦点环）。 */
const LINK_CLASS =
  "rounded text-primary underline-offset-4 hover:underline focus-visible:ring-2 focus-visible:ring-ring focus-visible:outline-none";

/** 空串与「只有空白」都算未配置。 */
function configured(value: string | undefined): string {
  return value?.trim() ?? "";
}

export function SiteFooter() {
  const { data: meta } = useMeta();

  const org = configured(meta?.footer_org);
  const email = configured(meta?.footer_contact_email);
  const phone = configured(meta?.footer_contact_phone);
  const notice = configured(meta?.footer_notice);

  /* ★ §27.4：只看这 4 个配置字段，版本号不参与判断。 */
  if (!org && !email && !phone && !notice) return null;

  const version = configured(meta?.app_version);
  const emailLooksValid = EMAIL_PATTERN.test(email);
  const phoneLooksValid = PHONE_PATTERN.test(phone);

  return (
    <footer aria-label="站点信息" data-testid="site-footer" className="border-t">
      <div className="mx-auto flex w-full max-w-[1400px] flex-col gap-x-8 gap-y-2 px-4 py-6 text-xs text-muted-foreground sm:flex-row sm:items-center sm:justify-between sm:px-6 lg:px-8">
        <div className="flex flex-col gap-1 sm:flex-row sm:items-center sm:gap-4">
          {org ? <span data-testid="site-footer-org">{org}</span> : null}
          {notice ? <span data-testid="site-footer-notice">{notice}</span> : null}
        </div>

        <div className="flex flex-col gap-1 sm:flex-row sm:items-center sm:gap-4">
          {email ? (
            <span>
              支持邮箱{" "}
              {emailLooksValid ? (
                <a data-testid="site-footer-email" className={LINK_CLASS} href={`mailto:${email}`}>
                  {email}
                </a>
              ) : (
                /* 降级：值照旧显示，但**不生成链接**。 */
                <span data-testid="site-footer-email">{email}</span>
              )}
            </span>
          ) : null}

          {phone ? (
            <span>
              内线电话{" "}
              {phoneLooksValid ? (
                <a data-testid="site-footer-phone" className={LINK_CLASS} href={`tel:${phone}`}>
                  {phone}
                </a>
              ) : (
                <span data-testid="site-footer-phone">{phone}</span>
              )}
            </span>
          ) : null}

          {version ? <span data-testid="site-footer-version">v{version}</span> : null}
        </div>
      </div>
    </footer>
  );
}
