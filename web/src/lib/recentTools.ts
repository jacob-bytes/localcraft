/**
 * M7 · F6「最近访问」的持久化层。
 *
 * 硬约束（任务书 F6）：
 *  - 键名固定 `localcraft:recent-tools`，**最多 8 条**；
 *  - 每条**只存** `{slug, name, at}` —— 严禁写入 token / 邮箱 / 角色
 *    任何凭据或个人信息（CONTRACT §3.1 只约束 access token 的存放位置，
 *    这里是不写入凭据的额外自律）；
 *  - 读取失败（JSON 损坏、localStorage 被禁用 / 配额满）必须**静默降级**
 *    为「无最近访问」，不允许抛错或白屏；
 *  - 写入同理只做 best-effort，失败就放弃。
 *
 * 因此这里的每个入口都包在 try/catch 里，且对从 localStorage 读回来的
 * 内容做形状校验（外部输入不可信：用户可以在 devtools 里改坏它）。
 */

export const RECENT_TOOLS_STORAGE_KEY = "localcraft:recent-tools";

/** 任务书：最多 8 条。 */
export const RECENT_TOOLS_LIMIT = 8;

export interface RecentToolEntry {
  slug: string;
  name: string;
  /** 最近访问时间（epoch ms），仅用于排序与展示，不含任何身份信息。 */
  at: number;
}

function isRecentToolEntry(value: unknown): value is RecentToolEntry {
  if (typeof value !== "object" || value === null) return false;
  const candidate = value as Record<string, unknown>;
  return (
    typeof candidate.slug === "string" &&
    candidate.slug.length > 0 &&
    typeof candidate.name === "string" &&
    typeof candidate.at === "number" &&
    Number.isFinite(candidate.at)
  );
}

/**
 * 读取最近访问。任何异常（键不存在、JSON 损坏、权限被拒）都返回 `[]`，
 * 调用方不需要再包 try/catch。
 */
export function readRecentTools(): RecentToolEntry[] {
  try {
    const raw = window.localStorage.getItem(RECENT_TOOLS_STORAGE_KEY);
    if (!raw) return [];
    const parsed: unknown = JSON.parse(raw);
    if (!Array.isArray(parsed)) return [];
    return parsed.filter(isRecentToolEntry).slice(0, RECENT_TOOLS_LIMIT);
  } catch {
    return [];
  }
}

/**
 * 记录一次访问：同一 slug 去重（保留最新时间）并截断到 8 条。
 * 返回写入后的列表，便于调用方直接更新 UI；写入失败时返回内存中的结果。
 */
export function recordRecentTool(
  entry: { slug: string; name: string },
  now: number = Date.now(),
): RecentToolEntry[] {
  const next: RecentToolEntry[] = [
    { slug: entry.slug, name: entry.name, at: now },
    ...readRecentTools().filter((item) => item.slug !== entry.slug),
  ].slice(0, RECENT_TOOLS_LIMIT);

  try {
    window.localStorage.setItem(RECENT_TOOLS_STORAGE_KEY, JSON.stringify(next));
  } catch {
    /* 配额满 / 隐私模式：静默降级，本次不持久化 */
  }
  return next;
}
