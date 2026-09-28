import { useMeta } from "@/hooks/useMeta";

/**
 * M12 · F2 建立、**M13 按 §28.6 重构** —— 站点页脚（CONTRACT §27.4 / §28.6）。
 *
 * M13 改了什么、为什么（改之前先读这一段）：
 *
 * M12 时 §27.4 裁定「页脚**只**出现在门户列表页与工具详情页、四个字段全空则整个页脚
 * 不渲染」，于是 `AppShell` 里原本的**全局页脚**（`localcraft v… · API …` + 写死的
 * 「内网工具与 Skill 共享平台」）被删掉了。§28.1 记录了根因：监控方写 §27 时漏看了
 * 这个既有页脚，「不配置时视觉零变化」与「页脚只在门户相关页」因此自相矛盾。
 *
 * §28.6 冻结的最终形态是**一个 `<footer>`，内容分两组**：
 *
 * | 内容 | 范围 | 条件 |
 * | --- | --- | --- |
 * | `localcraft v{app_version}` · `API {api_version}` | **所有 AppShell 页** | 始终 |
 * | `portal.footer_tagline`（第 6 个设置项） | **所有 AppShell 页** | 非空时 |
 * | 4 个 `footer_*` 配置字段 | **仅门户列表页与工具详情页** | 各自非空时 |
 *
 * 因此：
 *
 * 1. **本组件不再有「整体不渲染」的分支** —— 版本行始终在，页脚元素就始终渲染。
 *    §27.4 那条「字段全空则整个页脚不渲染」**已废止**。
 * 2. 4 个配置字段的范围由调用方决定（`AppShell` 读路由 `handle.portalFooterFields`，
 *    见 `routes.tsx`），所以它们与版本行**同处一个 `<footer>`**，不是第二个页脚元素。
 * 3. `<footer>` 渲染在 `<main>` **之外**（`AppShell` 负责）：ARIA 规定 `footer`
 *    落在 `main` 之内时不映射 `contentinfo` 地标。
 * 4. **邮箱 / 电话防御性降级**（§27.4 仍有效，§28.6 重申）：管理员填错一个字符，
 *    不允许页脚出现点不开的 `mailto:` / `tel:`，也不允许整个页脚崩掉 ——
 *    值不像邮箱 / 电话时**退化为纯文本**（内容照旧可见，只是不可点）。
 *
 * 取值全部走结构化字段而不是 Markdown —— §27.5 约束 1：Markdown 的
 * `ALLOWED_URI_REGEXP` 只放行 `https?` 与 `mailto:`，站内链接的 href 会被剥掉，
 * 而且排版不受控。**不要为了页脚去放宽 sanitizer。**
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

export interface SiteFooterProps {
  /**
   * `true` 时额外渲染 4 个 `footer_*` 配置字段（§28.6：仅门户列表页与工具详情页）。
   *
   * 版本行与标语**不受这个开关影响** —— 它们在所有 AppShell 页都渲染。
   */
  portalFields?: boolean;
}

export function SiteFooter({ portalFields = false }: SiteFooterProps) {
  const { data: meta } = useMeta();

  /* 系统标识（全站）：§28.6 —— 版本行与标语不看 `portalFields`。 */
  const appVersion = configured(meta?.app_version);
  const apiVersion = configured(meta?.api_version);
  const tagline = configured(meta?.footer_tagline);

  /* 门户信息（仅门户相关页）：§28.6。 */
  const org = configured(meta?.footer_org);
  const email = configured(meta?.footer_contact_email);
  const phone = configured(meta?.footer_contact_phone);
  const notice = configured(meta?.footer_notice);

  /*
   * 版本行的两段各自判定：`/meta` 尚未返回（或后端某个版本字段为空）时宁可少一段，
   * 也不渲染出 `localcraft v · API` 这种残句。`/meta` 返回后两段都在（它们是
   * `MetaResponse` 的既有必填字段），所以「所有 AppShell 页都有版本行」成立。
   */
  const versionLine = [
    appVersion ? `localcraft v${appVersion}` : "",
    apiVersion ? `API ${apiVersion}` : "",
  ]
    .filter(Boolean)
    .join(" · ");

  const showOrgGroup = portalFields && (org !== "" || notice !== "");
  const showContactGroup = portalFields && (email !== "" || phone !== "");
  const emailLooksValid = EMAIL_PATTERN.test(email);
  const phoneLooksValid = PHONE_PATTERN.test(phone);

  return (
    <footer aria-label="站点信息" data-testid="site-footer" className="border-t">
      <div className="mx-auto flex w-full max-w-[1400px] flex-col gap-x-8 gap-y-2 px-4 py-6 text-xs text-muted-foreground sm:flex-row sm:flex-wrap sm:items-center sm:justify-between sm:px-6 lg:px-8">
        <div className="flex flex-col gap-1 sm:flex-row sm:items-center sm:gap-4">
          {versionLine ? (
            <span data-testid="site-footer-version">{versionLine}</span>
          ) : null}
          {tagline ? <span data-testid="site-footer-tagline">{tagline}</span> : null}
        </div>

        {showOrgGroup ? (
          <div className="flex flex-col gap-1 sm:flex-row sm:items-center sm:gap-4">
            {org ? <span data-testid="site-footer-org">{org}</span> : null}
            {notice ? <span data-testid="site-footer-notice">{notice}</span> : null}
          </div>
        ) : null}

        {showContactGroup ? (
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
          </div>
        ) : null}
      </div>
    </footer>
  );
}
