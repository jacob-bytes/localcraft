/**
 * Response types mirroring docs/03 (API 接口清单), contracts/CONTRACT.md and —
 * where the docs stop — the backend's Pydantic schemas (`backend/app/schemas/`),
 * which CONTRACT §14.2 ruled are the authority.
 *
 * Rules taken from CONTRACT §5 / docs/03 §1.1:
 *  - snake_case field names, exactly as the backend serialises them
 *  - nullable fields are explicit `null`, never omitted
 *  - timestamps are ISO 8601 UTC strings ending in `Z`
 *  - enums are string-literal unions, mirroring the backend `enum.StrEnum`
 *    (`backend/app/models/enums.py`)
 */

/* -------------------------------------------------------------------------- */
/* Enums (backend/app/models/enums.py — mirrored once, never inlined)          */
/* -------------------------------------------------------------------------- */

export type ToolType = "file" | "webapp" | "skill" | "prompt";

export type Visibility = "public" | "restricted" | "private";

export type ToolStatus =
  | "draft"
  | "pending"
  | "approved"
  | "rejected"
  | "pending_update"
  | "offline";

export type VersionStatus = "pending" | "approved" | "rejected" | "superseded" | "purged";

/** docs/01 §3.1 */
export type Role = "viewer" | "user" | "approver" | "superadmin";

export type UserStatus = "active" | "disabled";

export type AuthProvider = "local" | "oidc";

export type AuthSource = "local" | "oidc";

export type ImageKind = "cover" | "screenshot" | "inline";

export type AclSubjectType = "user" | "group";

export type ApprovalAction =
  | "submit"
  | "withdraw"
  | "resubmit"
  | "approve"
  | "reject"
  | "offline"
  | "relist"
  | "purge_version"
  | "transfer_owner";

export type SettingValueType = "bool" | "int" | "string" | "json" | "list";

/** docs/03 §3.3 `sort` whitelist. */
export type ToolSort = "hot" | "new" | "name" | "-updated_at";

/** docs/03 §4 — every code the frontend is allowed to branch on. */
export type ErrorCode =
  | "VALIDATION_ERROR"
  | "INVALID_SORT"
  | "ACL_REQUIRED"
  | "SUBJECT_NOT_FOUND"
  | "DUPLICATE_ENTRY"
  | "SELF_GRANT"
  | "SETTING_INVALID"
  | "INVALID_CSV"
  | "UNAUTHENTICATED"
  | "INVALID_CREDENTIALS"
  | "TOKEN_EXPIRED"
  | "TOKEN_REVOKED"
  | "FORBIDDEN"
  | "SCOPE_MISSING"
  | "ACCOUNT_DISABLED"
  | "PASSWORD_CHANGE_REQUIRED"
  | "NOT_FOUND"
  | "ACCOUNT_LOCKED"
  | "VERSION_EXISTS"
  | "ALREADY_PROCESSED"
  | "LAST_SUPERADMIN"
  | "CATEGORY_IN_USE"
  | "GROUP_IN_USE"
  | "TOOL_NOT_EDITABLE"
  | "STATE_CONFLICT"
  | "PAYLOAD_TOO_LARGE"
  | "UNSUPPORTED_MEDIA_TYPE"
  | "SKILL_PARSE_FAILED"
  | "SKILL_MD_NOT_FOUND"
  | "ZIP_BOMB_DETECTED"
  | "ZIP_PATH_TRAVERSAL"
  | "ZIP_TOO_MANY_FILES"
  | "ZIP_INVALID"
  | "INSUFFICIENT_STORAGE"
  | "USER_QUOTA_EXCEEDED"
  | "STORAGE_FULL"
  | "DATABASE_UNAVAILABLE"
  | "INTERNAL_ERROR";

/* -------------------------------------------------------------------------- */
/* Envelope (CONTRACT §4, docs/03 §1.5, §1.7)                                  */
/* -------------------------------------------------------------------------- */

export interface Paginated<T> {
  items: T[];
  total: number;
  page: number;
  page_size: number;
  pages: number;
}

/**
 * `GET /tools` adds `facets` to the pagination envelope.
 *
 * The KEY IS ALWAYS PRESENT: an object on `page === 1`, `null` when paging
 * (docs/03 §3.3, M1 checkpoint ruling — aggregation is skipped when paging, but
 * the shape stays stable per §1.1 "可空字段显式返回 null，不省略字段").
 * Deliberately `ToolFacets | null`, NOT `ToolFacets | undefined`.
 */
export interface ToolListResponse extends Paginated<ToolListItem> {
  facets: ToolFacets | null;
}

export interface ToolFacets {
  categories: FacetCategory[];
  types: FacetType[];
}

export interface FacetCategory {
  slug: string;
  name: string;
  count: number;
}

export interface FacetType {
  value: ToolType;
  count: number;
}

/** docs/03 §4 `details.fields` element. */
export interface FieldError {
  field: string;
  message: string;
  value_length?: number;
}

export interface ErrorDetails {
  fields?: FieldError[];
  field?: string;
  value?: unknown;
  retry_after_seconds?: number;
  [key: string]: unknown;
}

export interface ApiErrorBody {
  code: ErrorCode | string;
  message: string;
  details: ErrorDetails | null;
  request_id: string;
}

/* -------------------------------------------------------------------------- */
/* Meta (docs/03 §3.1, backend MetaFeatures)                                   */
/* -------------------------------------------------------------------------- */

export interface MetaFeatures {
  webapp_health_check: boolean;
  skill_preview: boolean;
  anonymous_view: boolean;
  change_password: boolean;
}

export interface Meta {
  site_name: string;
  /*
   * M12 站点定制信息（CONTRACT §27.3）。后端与前端并行开工，字段名以契约冻结的
   * 那一份为准 —— 5 个字段都是**必填的 `string`**，**空串表示未配置**（没有 null）。
   *
   * 渲染端一律按 trim 后的空串判断「未配置」：`footer_*` 四个字段全空时整个页脚
   * 不渲染、`site_subtitle` 为空时连元素都不渲染（§27.2 / §27.4）。
   * 版本号不是设置项，继续取既有的 `app_version`（§27.3）。
   */
  site_subtitle: string;
  footer_org: string;
  footer_contact_email: string;
  footer_contact_phone: string;
  footer_notice: string;
  announcement_md: string;
  auth_provider: AuthProvider;
  allow_anonymous_view: boolean;
  default_sort: ToolSort;
  page_size: number;
  app_version: string;
  api_version: string;
  features: MetaFeatures;
}

/* -------------------------------------------------------------------------- */
/* Auth (docs/03 §3.2, CONTRACT §3, §14.2)                                     */
/* -------------------------------------------------------------------------- */

export interface User {
  id: number;
  username: string;
  display_name: string;
  email: string | null;
  roles: Role[];
  permissions: string[];
  must_change_password: boolean;
  auth_source: AuthSource;
  /** CONTRACT §14.2 — added after dumping the real `/auth/me` response. */
  status: UserStatus;
  last_login_at: string | null;
  created_at: string | null;
}

export interface TokenPair {
  access_token: string;
  token_type: string;
  expires_in: number;
  /** CONTRACT §3.3 — refresh returns the user too, so the client can restore
   *  session state without an extra `/auth/me` round trip. */
  user: User;
}

export interface LoginRequest {
  username: string;
  password: string;
}

export interface ChangePasswordRequest {
  old_password: string;
  new_password: string;
}

/**
 * `{status:"ok"}` body returned by the write-and-done endpoints.
 * CONTRACT §14.2: logout / change-password answer `200` + this body, NOT `204`.
 */
export interface StatusResponse {
  status: string;
}

export interface LoginField {
  name: string;
  label: string;
  type: string;
  required: boolean;
}

/** `GET /auth/provider` (CONTRACT §14.2 — 5 fields, `login_fields` is structured). */
export interface AuthProviderResponse {
  provider: AuthProvider;
  display_name: string;
  login_fields: LoginField[];
  password_change_supported: boolean;
  refresh_supported: boolean;
}

/* -------------------------------------------------------------------------- */
/* Taxonomy (docs/02 §3.6, §3.7; docs/03 §2.3)                                 */
/* -------------------------------------------------------------------------- */

export interface Category {
  id: number;
  slug: string;
  name: string;
  description: string | null;
  /** lucide icon name (docs/02 §3.6) */
  icon: string | null;
  sort_order: number;
  is_active: boolean;
  tool_count: number;
}

export interface Tag {
  id: number;
  /** normalised name, e.g. `python` (docs/02 §3.7) */
  name: string;
  /** original casing for display */
  display_name: string;
  usage_count: number;
}

/* -------------------------------------------------------------------------- */
/* Portal list (docs/03 §3.3)                                                  */
/* -------------------------------------------------------------------------- */

export interface ToolOwner {
  id: number;
  username: string;
  display_name: string;
}

export interface ToolCategoryRef {
  id: number;
  slug: string;
  name: string;
  icon: string | null;
}

export interface ToolListItem {
  id: number;
  slug: string;
  name: string;
  summary: string;
  tool_type: ToolType;
  visibility: Visibility;
  category: ToolCategoryRef | null;
  tags: string[];
  cover_url: string | null;
  owner: ToolOwner;
  current_version: string | null;
  file_size: number | null;
  download_count: number;
  view_count: number;
  /** `true` only for owner / approver / superadmin (docs/03 §3.3) */
  has_pending_version: boolean;
  can_download: boolean;
  published_at: string | null;
  updated_at: string | null;
  /* ---- M8（CONTRACT §23.5）—— 收藏 / 点赞 ---------------------------------- */
  /** 反规范化计数（§23.3）。匿名请求者也会收到。 */
  favorite_count: number;
  like_count: number;
  /** **当前请求者**是否已收藏 / 已点赞；匿名恒为 `false`（§23.5）。 */
  is_favorited: boolean;
  is_liked: boolean;
}

/** Query parameters for `GET /tools` (docs/03 §3.3). Arrays are repeated params. */
export interface ToolListParams {
  q?: string;
  category?: string[];
  tag?: string[];
  type?: ToolType[];
  owner?: string;
  sort?: ToolSort;
  page?: number;
  page_size?: number;
}

/* -------------------------------------------------------------------------- */
/* Tool detail (docs/03 §3.4, backend app/schemas/tool.py + version.py)        */
/* -------------------------------------------------------------------------- */

export interface VersionUploader {
  id: number;
  display_name: string;
}

export interface SkillTreeSummary {
  file_count: number;
  total_size: number;
  max_depth: number;
}

export interface SkillVersionInfo {
  manifest: Record<string, unknown> | null;
  file_tree_summary: SkillTreeSummary | null;
  /**
   * 文件树是否被截断。CONTRACT §15.3：该值**无法从存储的数据推导**
   * （真实总数在截断后即丢失），因此后端在解析时持久化。
   */
  file_tree_truncated: boolean;
  parse_error: string | null;
}

/** `list[VersionSummary]` element — `GET /tools/{slug}/versions`. */
export interface VersionSummary {
  id: number;
  tool_id: number;
  version: string;
  changelog_md: string;
  status: VersionStatus;
  is_current: boolean;
  file_name: string | null;
  file_size: number | null;
  /** first 16 chars of the SHA256 (FR-VER-06) */
  file_sha256_short: string | null;
  file_ext: string | null;
  mime_type: string | null;
  uploaded_by: VersionUploader | null;
  approved_at: string | null;
  reject_reason: string | null;
  purged_at: string | null;
  created_at: string | null;
  can_download: boolean;
}

/** Detail-page `current_version` — carries the full hash and skill info. */
export interface VersionDetail extends VersionSummary {
  file_sha256: string | null;
  skill: SkillVersionInfo | null;
  prompt_content: string | null;
}

export interface ToolImage {
  id: number;
  kind: ImageKind;
  /** Capability URL issued by the backend — use verbatim (CONTRACT §14.3). */
  url: string;
  thumb_url: string;
  file_name: string | null;
  mime_type: string | null;
  file_size: number | null;
  width: number | null;
  height: number | null;
  sort_order: number;
  alt_text: string | null;
  sha256: string | null;
  created_at: string | null;
}

/**
 * Server-computed permission booleans (docs/03 §3.4).
 * The frontend reads these instead of re-implementing visibility/role logic.
 */
export interface ToolPermissions {
  can_edit: boolean;
  can_download: boolean;
  can_manage_versions: boolean;
  can_view_acl: boolean;
  can_delete: boolean;
  can_submit: boolean;
  can_approve: boolean;
}

export interface AclEntry {
  id: number | null;
  subject_type: AclSubjectType;
  subject_id: number;
  subject_name: string | null;
  can_download: boolean;
}

export interface SkillDetailInfo {
  manifest: Record<string, unknown> | null;
  readme_md: string | null;
  file_tree_summary: SkillTreeSummary | null;
  parse_error: string | null;
}

export interface PromptDetailInfo {
  content: string;
  char_count: number;
}

export interface CurrentVersionBrief {
  id: number;
  version: string;
}

export interface ToolDetail {
  id: number;
  slug: string;
  name: string;
  summary: string;
  description_md: string;
  /** Server-rendered + sanitised Markdown (kept for parity with docs/03 §3.4). */
  description_html: string;
  tool_type: ToolType;
  visibility: Visibility;
  status: ToolStatus;
  category: ToolCategoryRef | null;
  tags: string[];
  images: ToolImage[];
  webapp_url: string | null;
  current_version: VersionDetail | null;
  pending_version: CurrentVersionBrief | null;
  version_count: number;
  history_version_count: number;
  download_count: number;
  view_count: number;
  owner: ToolOwner;
  published_at: string | null;
  last_version_at: string | null;
  created_at: string | null;
  updated_at: string | null;
  reject_reason: string | null;
  offline_reason: string | null;
  deleted_at: string | null;
  can_download: boolean;
  can_edit: boolean;
  permissions: ToolPermissions;
  acl: AclEntry[] | null;
  skill: SkillDetailInfo | null;
  prompt: PromptDetailInfo | null;
  /* ---- M8（CONTRACT §23.5）—— 收藏 / 点赞 ---------------------------------- */
  favorite_count: number;
  like_count: number;
  is_favorited: boolean;
  is_liked: boolean;
  /**
   * M8（§23.5）：作者自述的**单次使用**预计节省分钟数，`1 ~ 1440`，`null` = 未填写。
   *
   * **契约 §23.5 的字段表里没有列出这一条**（它只写了「`ToolCreateRequest` /
   * `ToolUpdateRequest` 新增 1 个可选字段」），但后端实现把该字段也放进了
   * `ToolDetail` —— 这对前端是好事（编辑页终于能回显当前值），所以这里跟上
   * `backend/openapi.json`（§15.6：openapi 是形状权威）。已上报监控方。
   */
  estimated_saving_minutes: number | null;
}

/** `GET /tools/{slug}/versions/{version}/skill-preview` (docs/03 §3.5). */
export interface SkillFileEntry {
  path: string;
  size: number;
  is_dir: boolean;
  sha256: string | null;
}

export interface SkillPreview {
  version: string;
  manifest: Record<string, unknown> | null;
  readme_md: string | null;
  file_tree: SkillFileEntry[];
  file_tree_truncated: boolean;
  total_size: number;
}

/** `POST /tools/{slug}/download-ticket` (docs/03 §3.15). */
export interface DownloadTicket {
  url: string;
  expires_at: string | null;
  file_name: string | null;
  file_size: number | null;
  file_sha256: string | null;
}

/* -------------------------------------------------------------------------- */
/* My tools (docs/03 §2.4, backend schemas/tool.py MyToolListItem)             */
/* -------------------------------------------------------------------------- */

export interface MyToolListItem {
  id: number;
  slug: string;
  name: string;
  summary: string;
  tool_type: ToolType;
  visibility: Visibility;
  status: ToolStatus;
  category: ToolCategoryRef | null;
  tags: string[];
  cover_url: string | null;
  current_version: string | null;
  pending_version: string | null;
  file_size: number | null;
  download_count: number;
  view_count: number;
  version_seq: number;
  reject_reason: string | null;
  offline_reason: string | null;
  published_at: string | null;
  created_at: string | null;
  updated_at: string | null;
  /** Human-readable status, computed server-side so wording never drifts. */
  status_label: string;
}

/** `GET /me/tools` envelope — pagination only, no facets (backend MyToolListResponse). */
export type MyToolListResponse = Paginated<MyToolListItem>;

export interface MyToolListParams {
  status?: ToolStatus[];
  type?: ToolType[];
  category?: string[];
  q?: string;
  sort?: ToolSort;
  page?: number;
  page_size?: number;
}

export interface ToolCreateRequest {
  name: string;
  summary: string;
  description_md?: string;
  tool_type: ToolType;
  category_id?: number | null;
  tags?: string[];
  visibility?: Visibility;
  webapp_url?: string | null;
  webapp_health_url?: string | null;
  /**
   * M8（CONTRACT §23.5）：作者自述的**单次使用**预计节省分钟数。
   *
   * 取值 **1 ~ 1440**（超出 → `VALIDATION_ERROR`）；**`null` 表示未填写，
   * 不是一个可以当成 0 的值**（§23.3）。留空必须提交 `null`。
   */
  estimated_saving_minutes?: number | null;
}

export interface ToolUpdateRequest {
  name?: string;
  summary?: string;
  description_md?: string;
  category_id?: number | null;
  tags?: string[];
  visibility?: Visibility;
  webapp_url?: string | null;
  webapp_health_url?: string | null;
  /** 同 `ToolCreateRequest.estimated_saving_minutes`；**省略字段 = 不修改**。 */
  estimated_saving_minutes?: number | null;
}

export interface SubmitResponse {
  tool_id: number;
  status: ToolStatus;
  version_seq: number;
  auto_approved: boolean;
  auto_approved_rule: string | null;
  approval_record_id: number | null;
}

export interface WithdrawResponse {
  tool_id: number;
  status: ToolStatus;
  approval_record_id: number | null;
}

export interface AclReplaceRequest {
  visibility: Visibility;
  entries: Array<{
    subject_type: AclSubjectType;
    subject_id: number;
    can_download: boolean;
  }>;
}

export interface AclResponse {
  tool_id: number;
  visibility: Visibility;
  entries: AclEntry[];
  permissions: Record<string, boolean>;
  /** Whether the ACL is currently in effect (only meaningful for `restricted`). */
  effective: boolean;
}

/** `POST /me/tools/{id}/versions` (docs/03 §3.7). */
export interface VersionUploadResponse {
  id: number;
  tool_id: number;
  version: string;
  status: VersionStatus;
  file_name: string | null;
  file_size: number | null;
  file_sha256: string | null;
  prompt_content: string | null;
  skill: SkillVersionInfo | null;
  tool_status: string;
  created_at: string | null;
  /**
   * M8 · 上传去重（CONTRACT §23.2 / 后端 B9）。
   *
   * 去重**在服务端**做：浏览器端算 SHA-256 需要 `crypto.subtle`，而它只在安全
   * 上下文可用，本项目按 D39 走 `http://<ip>:<port>` 直连，该 API 为 `undefined`。
   * 因此命中信息由上传响应回带，前端只负责展示**非阻塞**提示。
   *
   * 字段名与形状以 `backend/openapi.json` 的 `DuplicateVersionMatch` 为准
   * （契约 §23.2 只冻结了「响应回带」，命名留给后端）。
   */
  duplicate_of: DuplicateVersionMatch | null;
}

/**
 * 上传去重命中信息（M8 / `backend/openapi.json` 的 `DuplicateVersionMatch`）。
 *
 * 命中依据是 `tool_versions.file_sha256`（§23.1：该列与索引都已存在，不新增哈希
 * 计算）；命中范围是**其他工具**已有的版本，同一工具的新版本不算重复。
 * 命中**不阻断上传**。
 */
export interface DuplicateVersionMatch {
  tool_id: number;
  slug: string;
  name: string;
  version_id: number;
  version: string;
  file_sha256: string;
  is_current: boolean;
  uploaded_at: string | null;
}

export interface VersionUploadFields {
  version: string;
  changelog_md?: string;
  file?: File | null;
  prompt_content?: string | null;
  webapp_url?: string | null;
  auto_submit?: boolean;
}

export interface VersionPatchRequest {
  changelog_md: string;
}

/* -------------------------------------------------------------------------- */
/* Profile / stats / downloads (backend schemas/me.py)                         */
/* -------------------------------------------------------------------------- */

export interface Usage {
  tool_count: number;
  published_tool_count: number;
  draft_tool_count: number;
  pending_tool_count: number;
  version_count: number;
  download_count: number;
  used_bytes: number;
  quota_bytes: number;
  total_quota_bytes: number;
  platform_used_bytes: number;
  used_percent: number;
}

export interface Profile {
  id: number;
  username: string;
  display_name: string;
  email: string | null;
  roles: Role[];
  permissions: string[];
  status: UserStatus;
  auth_source: AuthSource;
  must_change_password: boolean;
  last_login_at: string | null;
  created_at: string | null;
  usage: Usage | null;
}

export interface ProfileUpdateRequest {
  display_name?: string;
  email?: string | null;
}

export interface DownloadLogItem {
  id: number;
  tool_id: number;
  tool_slug: string | null;
  tool_name: string | null;
  version_id: number | null;
  version: string | null;
  file_name: string | null;
  file_size: number | null;
  via_api_token_id: number | null;
  created_at: string | null;
}

export interface ImagePatchRequest {
  sort_order?: number;
  alt_text?: string;
  set_as_cover?: boolean;
}

export interface ToolStats {
  tool_id: number;
  download_count: number;
  view_count: number;
  daily: Array<{ stat_date: string; views: number; downloads: number }>;
}

/* -------------------------------------------------------------------------- */
/* Approvals (docs/03 §3.8–§3.10, backend schemas/approval.py)                 */
/* -------------------------------------------------------------------------- */

export type SubmissionType = "new_tool" | "new_version";

export interface PendingVersionBrief {
  id: number;
  version: string;
  changelog_md: string;
  file_name: string | null;
  file_size: number | null;
  file_sha256: string | null;
}

export interface ApprovalQueueItem {
  tool_id: number;
  tool_slug: string;
  tool_name: string;
  tool_type: ToolType;
  summary: string;
  visibility: Visibility;
  category: ToolCategoryRef | null;
  tags: string[];
  submission_type: SubmissionType;
  status: string;
  pending_version: PendingVersionBrief | null;
  current_version: CurrentVersionBrief | null;
  owner: ToolOwner | null;
  submitted_at: string | null;
  /** Server-computed waiting time in hours (drives the >24h / >72h highlight). */
  waiting_hours: number;
  /**
   * CONTRACT §15.4：审批队列也下发 `tools.version_seq`，批准时回传作为乐观锁
   * （`expected_version_seq`）。CAS 已能独立保证并发正确，这里是让审批人拿得到
   * 文档承诺的值。
   */
  version_seq: number;
}

export interface ApprovalQueueParams {
  status?: string;
  tool_type?: ToolType;
  owner?: string;
  page?: number;
  page_size?: number;
}

export interface ApproveRequest {
  version_id?: number | null;
  note?: string | null;
  expected_version_seq?: number | null;
}

export interface SupersededVersionBrief {
  id: number;
  version: string;
}

export interface ApproveResponse {
  tool_id: number;
  status: string;
  version_seq: number;
  current_version: CurrentVersionBrief | null;
  superseded_version: SupersededVersionBrief | null;
  purged_versions: SupersededVersionBrief[];
  approval_record_id: number | null;
}

export interface RejectRequest {
  reason: string;
  version_id?: number | null;
}

export interface RejectResponse {
  tool_id: number;
  status: string;
  pending_version: PendingVersionBrief | null;
  rejected_version: SupersededVersionBrief | null;
  approval_record_id: number | null;
}

export interface OfflineRequest {
  reason: string;
}

/**
 * `POST /admin/approvals/{id}/offline` 的响应（openapi `OfflineResponse`）。
 *
 * **注意不要用 `ApproveResponse` 接这个响应**：下架/上架不产生新版本，服务端只回
 * `tool_id` / `status` / `approval_record_id`，没有 `version_seq` 等字段。
 * （M4 由 `check:api-types` 的响应覆盖检查发现：守卫此前只看顶层 `$ref` 名。）
 */
export interface OfflineResponse {
  tool_id: number;
  status: string;
  approval_record_id?: number | null;
}

/** `POST /admin/approvals/{id}/relist` 的响应（openapi `RelistResponse`）。 */
export interface RelistResponse {
  tool_id: number;
  status: string;
  approval_record_id?: number | null;
}

export interface RelistRequest {
  reason?: string | null;
}

export interface BatchApproveRequest {
  tool_ids: number[];
  note?: string | null;
}

export interface BatchApproveResultItem {
  tool_id: number;
  ok: boolean;
  status: string | null;
  error_code: string | null;
  message: string | null;
}

export interface BatchApproveResponse {
  succeeded: number;
  failed: number;
  results: BatchApproveResultItem[];
}

export interface ApprovalHistoryParams {
  tool_id?: number;
  actor_id?: number;
  action?: ApprovalAction;
  date_from?: string;
  date_to?: string;
  page?: number;
  page_size?: number;
}

export interface ApprovalRecord {
  id: number;
  tool_id: number;
  tool_name: string | null;
  tool_slug: string | null;
  version_id: number | null;
  version: string | null;
  action: ApprovalAction;
  from_status: string | null;
  to_status: string;
  version_from_status: string | null;
  version_to_status: string | null;
  actor_id: number | null;
  /** Display-name snapshot so history stays accurate after a rename. */
  actor_label: string;
  is_automatic: boolean;
  auto_rule: string | null;
  reason: string | null;
  note: string | null;
  created_at: string | null;
}

/* -------------------------------------------------------------------------- */
/* Whitelist & settings (docs/03 §3.13, backend schemas/setting.py)            */
/* -------------------------------------------------------------------------- */

export interface WhitelistEntry {
  user_id: number;
  username: string | null;
  display_name: string | null;
  reason: string | null;
  added_by_id: number | null;
  added_by_name: string | null;
  expires_at: string | null;
  created_at: string | null;
  is_effective: boolean;
}

export interface WhitelistEntryInput {
  user_id: number;
  reason?: string | null;
  expires_at?: string | null;
}

export interface SettingItem {
  key: string;
  value: unknown;
  value_type: SettingValueType;
  is_public: boolean;
  description: string | null;
  options: string[] | null;
  /** serialisation alias of `minimum` on the backend */
  min: number | null;
  /** serialisation alias of `maximum` on the backend */
  max: number | null;
  updated_at: string | null;
  updated_by: { id: number; display_name: string } | null;
}

export interface SettingWarning {
  code: string;
  message: string;
  pending_count: number | null;
}

export interface SettingListResponse {
  items: SettingItem[];
  warnings: SettingWarning[];
}

export interface SettingUpdateRequest {
  items: Array<{ key: string; value: unknown }>;
}

/* ========================================================================== */
/* M3 —— 管理控制台（CONTRACT §6.1 / openapi.json 为形状权威，见 §15.6）        */
/* ========================================================================== */

/** 权限点（docs/03 §1.4 的 Scope 列表；后端 `ApiScope` StrEnum 的镜像）。 */
export type ApiScope =
  | "tools:read"
  | "tools:write"
  | "approvals:write"
  | "users:write"
  | "groups:write"
  | "taxonomy:write"
  | "settings:write"
  | "admin:all";

/** `GET /admin/roles`（docs/02 §3.2）。 */
export interface RoleOut {
  id: number;
  code: Role;
  name: string;
  description: string | null;
  permissions: string[];
  is_builtin: boolean;
  user_count: number;
}

/* -------------------------------------------------------------------------- */
/* 用户管理（docs/04 §6.13）                                                    */
/* -------------------------------------------------------------------------- */

export interface AdminUserItem {
  id: number;
  username: string;
  display_name: string;
  email: string | null;
  status: UserStatus;
  auth_source: string;
  roles: string[];
  must_change_password: boolean;
  failed_login_count: number;
  /** 锁定到期时间；非 null 表示账号当前处于锁定中。 */
  locked_until: string | null;
  last_login_at: string | null;
  last_login_ip: string | null;
  tool_count: number;
  used_bytes: number;
  active_token_count: number;
  created_at: string | null;
  updated_at: string | null;
}

export type AdminUserListResponse = Paginated<AdminUserItem>;

export interface AdminUserListParams {
  q?: string;
  role?: Role[];
  status?: UserStatus;
  page?: number;
  page_size?: number;
}

export interface AdminUserCreateRequest {
  username: string;
  display_name: string;
  email?: string | null;
  /** 留空则由服务端生成一次性初始密码。 */
  password?: string | null;
  roles: Role[];
  must_change_password?: boolean;
}

export interface AdminUserCreateResponse {
  user: AdminUserItem;
  /** 一次性初始密码：只在创建响应里出现一次（docs/04 §6.13）。 */
  generated_password: string | null;
}

export interface AdminUserUpdateRequest {
  display_name?: string | null;
  email?: string | null;
  status?: UserStatus | null;
}

export interface AdminRoleReplaceRequest {
  roles: Role[];
}

export interface ResetPasswordRequest {
  /** 留空则由服务端生成。 */
  password?: string | null;
}

export interface ResetPasswordResponse {
  user_id: number;
  generated_password: string | null;
  must_change_password: boolean;
  revoked_sessions: number;
}

export interface RevokeSessionsResponse {
  status: string;
  revoked_sessions: number;
}

/* -------------------------------------------------------------------------- */
/* 用户组（docs/04 §6.14）                                                      */
/* -------------------------------------------------------------------------- */

/* -------------------------------------------------------------------------- */
/* M6 新增（contracts/CONTRACT.md §20.4）                                       */
/* -------------------------------------------------------------------------- */

/** GET /api/v1/directory —— 最小披露的主体目录，供 ACL 授权时搜索。
 *
 * 任何已登录用户可调。**刻意不含邮箱/状态/角色**（§20.4③），前端不要期待这些字段。
 */
export interface DirectoryUser {
  id: number;
  username: string;
  display_name: string;
}

export interface DirectoryGroup {
  id: number;
  name: string;
  member_count: number;
}

export interface DirectoryResponse {
  users: DirectoryUser[];
  groups: DirectoryGroup[];
}

/** DELETE /admin/categories/{id} —— 软删除（FR-TAX-02）。 */
export interface CategoryDeleteResponse {
  status: string;
  soft_deleted: boolean;
}

/** DELETE /admin/groups/{id} —— 附被清理 ACL 条目的工具 id。 */
export interface GroupDeleteResponse {
  status: string;
  cleaned_acl_entries: number;
  affected_tools: number[];
}

/** 只有 {"status":"ok"} 的通用响应（移除组成员、删除 Token 记录）。 */
export interface OkResponse {
  status: string;
}

export interface GroupOut {
  id: number;
  name: string;
  description: string | null;
  is_active: boolean;
  member_count: number;
  /** 被 ACL 引用的工具条数（>0 时删除会返回 409 GROUP_IN_USE）。 */
  acl_reference_count: number;
  created_at: string | null;
  updated_at: string | null;
}

export type GroupListResponse = Paginated<GroupOut>;

export interface GroupCreateRequest {
  name: string;
  description?: string | null;
  is_active?: boolean;
}

export interface GroupUpdateRequest {
  name?: string | null;
  description?: string | null;
  is_active?: boolean | null;
}

export interface GroupMemberOut {
  user_id: number;
  username: string;
  display_name: string;
  email: string | null;
  status: UserStatus;
  added_at: string | null;
  added_by_name: string | null;
}

export type GroupMemberListResponse = Paginated<GroupMemberOut>;

export interface GroupMemberAddRequest {
  user_ids: number[];
}

export interface GroupMemberAddResponse {
  added: number;
  already_members: number;
  not_found: number[];
}

/** `409 GROUP_IN_USE` 的 `details`（后端 `GroupInUseDetail`）。 */
export interface GroupInUseDetail {
  group_id: number;
  group_name: string;
  tool_count: number;
  /** 引用该组的工具（后端是 `list[dict]`，实际给 id/slug/name）。 */
  tools: Array<{ id?: number; slug?: string; name?: string }>;
}

/* -------------------------------------------------------------------------- */
/* 分类与标签管理（docs/04 §6.15 §6.16）                                        */
/* -------------------------------------------------------------------------- */

export interface AdminCategoryOut {
  id: number;
  slug: string;
  name: string;
  description: string | null;
  icon: string | null;
  sort_order: number;
  is_active: boolean;
  tool_count: number;
  created_at: string | null;
  updated_at: string | null;
}

export interface AdminCategoryCreateRequest {
  name: string;
  slug?: string | null;
  description?: string | null;
  icon?: string | null;
  sort_order?: number;
  is_active?: boolean;
}

export interface AdminCategoryUpdateRequest {
  name?: string | null;
  slug?: string | null;
  description?: string | null;
  icon?: string | null;
  sort_order?: number | null;
  is_active?: boolean | null;
}

export interface CategoryOrderItem {
  id: number;
  sort_order: number;
}

export interface CategoryOrderRequest {
  items: CategoryOrderItem[];
}

/** `409 CATEGORY_IN_USE` 的 `details`。 */
export interface CategoryInUseDetail {
  category_id?: number;
  tool_count: number;
}

export interface AdminTagOut {
  id: number;
  /** 归一化名（小写、全角转半角）。 */
  name: string;
  display_name: string;
  usage_count: number;
  created_at: string | null;
}

export type AdminTagListResponse = Paginated<AdminTagOut>;

export interface TagRenameRequest {
  display_name: string;
}

export interface TagMergeRequest {
  source_ids: number[];
  target_id: number;
}

export interface TagMergeResponse {
  target_id: number;
  target_name: string;
  merged_tags: number;
  moved_references: number;
  /** 同一工具原本同时带源标签与目标标签，合并后去重掉的引用数。 */
  deduplicated_references: number;
  deleted_tags: string[];
}

export interface TagCleanupResponse {
  deleted: number;
  tags: string[];
}

/* -------------------------------------------------------------------------- */
/* API Token（docs/04 §6.18）                                                   */
/* -------------------------------------------------------------------------- */

export interface ApiTokenOut {
  id: number;
  name: string;
  /** 形如 `st_9xK2mN`；**列表接口永不返回明文**（FR-ADMIN-10）。 */
  token_prefix: string;
  scopes: string[];
  note: string | null;
  created_by_id: number | null;
  created_by_name: string | null;
  created_at: string | null;
  expires_at: string | null;
  revoked_at: string | null;
  last_used_at: string | null;
  last_used_ip: string | null;
  is_active: boolean;
}

export type ApiTokenListResponse = Paginated<ApiTokenOut>;

export interface ApiTokenCreateRequest {
  name: string;
  scopes: ApiScope[];
  expires_at?: string | null;
  note?: string | null;
}

export interface ApiTokenCreateResponse extends ApiTokenOut {
  /** **明文只在此处出现一次**（docs/04 §6.18）。 */
  token: string;
  warning: string;
}

/* -------------------------------------------------------------------------- */
/* 概览与统计（docs/04 §6.9）                                                   */
/* -------------------------------------------------------------------------- */

export interface StatusCount {
  status: string;
  count: number;
}

export interface AdminOverviewResponse {
  tool_count: number;
  pending_count: number;
  tools_by_status: StatusCount[];
  user_count: number;
  active_user_count: number;
  disabled_user_count: number;
  category_count: number;
  tag_count: number;
  group_count: number;
  active_token_count: number;
  recycle_bin_count: number;
  download_count: number;
  view_count: number;
  downloads_last_7_days: number;
  used_bytes: number;
  quota_bytes: number;
  used_percent: number;
  /** 是否应就存储水位告警（**判断口径只在后端** —— `settings_service.storage_warning()`）。 */
  storage_warning: boolean;
  /** 告警阈值（百分比），来自设置项 `quota.warn_threshold_pct`，仅用于文案。 */
  storage_warning_threshold_pct: number;
}

export interface ToolRankItem {
  tool_id: number;
  slug: string;
  name: string;
  tool_type: ToolType;
  status: ToolStatus;
  owner_name: string | null;
  download_count: number;
  view_count: number;
}

export interface ToolRankResponse {
  items: ToolRankItem[];
  total: number;
}

export interface StorageOwnerItem {
  owner_id: number;
  username: string;
  display_name: string;
  used_bytes: number;
  quota_bytes: number;
  used_percent: number;
  tool_count: number;
  version_count: number;
}

export interface StorageStatsResponse {
  items: StorageOwnerItem[];
  total: number;
  page: number;
  page_size: number;
  total_used_bytes: number;
  total_quota_bytes: number;
  used_percent: number;
  file_count: number;
  orphan_file_count: number;
}

/* -------------------------------------------------------------------------- */
/* 全站工具管理（docs/04 §6.12）                                                */
/* -------------------------------------------------------------------------- */

export interface AdminToolItem {
  id: number;
  slug: string;
  name: string;
  summary: string;
  tool_type: ToolType;
  visibility: Visibility;
  status: ToolStatus;
  category: ToolCategoryRef | null;
  tags: string[];
  cover_url: string | null;
  owner: ToolOwner | null;
  current_version: string | null;
  download_count: number;
  view_count: number;
  version_seq: number;
  reject_reason: string | null;
  offline_reason: string | null;
  published_at: string | null;
  created_at: string | null;
  updated_at: string | null;
  /** 非 null 表示该工具在回收站里。 */
  deleted_at: string | null;
}

export type AdminToolListResponse = Paginated<AdminToolItem>;

export interface AdminToolListParams {
  q?: string;
  status?: ToolStatus[];
  type?: ToolType[];
  category?: string[];
  visibility?: Visibility[];
  owner?: string;
  include_deleted?: boolean;
  date_from?: string;
  date_to?: string;
  page?: number;
  page_size?: number;
}

export interface AdminToolCreateRequest {
  name: string;
  summary: string;
  owner_id: number;
  tool_type: ToolType;
  description_md?: string;
  category_id?: number | null;
  tags?: string[];
  visibility?: Visibility;
  webapp_url?: string | null;
  /** `true` 时创建后直接发布（跳过草稿）。 */
  publish?: boolean;
}

export interface TransferOwnerRequest {
  new_owner_id: number;
  reason?: string | null;
}

export interface TransferOwnerResponse {
  tool_id: number;
  previous_owner: ToolOwner | null;
  new_owner: ToolOwner | null;
  previous_owner_used_bytes: number;
  new_owner_used_bytes: number;
  approval_record_id: number | null;
}

export interface PurgeToolResponse {
  tool_id: number;
  purged_versions: number;
  purged_files: number;
}

/** 回收站条目与全站工具同形（`AdminToolListResponse`），`deleted_at` 非 null。 */
export type RecycleBinItem = AdminToolItem;

/* -------------------------------------------------------------------------- */
/* 批量导入导出（docs/04 §6.20）                                                */
/* -------------------------------------------------------------------------- */

export interface ImportErrorItem {
  row: number;
  field: string | null;
  value?: unknown;
  message: string;
}

export interface GeneratedPassword {
  username: string;
  password: string;
}

export interface ImportResultResponse {
  dry_run: boolean;
  on_conflict: string;
  succeeded: number;
  failed: number;
  skipped: number;
  generated_passwords: GeneratedPassword[];
  errors: ImportErrorItem[];
}

export type ImportOnConflict = "skip" | "update" | "fail";

export interface ToolImportItem {
  name: string;
  summary?: string;
  tool_type: ToolType;
  description_md?: string;
  category_slug?: string | null;
  tags?: string[];
  visibility?: Visibility;
  owner_username?: string | null;
  webapp_url?: string | null;
  slug?: string | null;
  publish?: boolean;
}

export interface ToolImportRequest {
  items: ToolImportItem[];
  dry_run?: boolean;
  on_conflict?: ImportOnConflict;
}

export interface ExportScopeParams {
  include_roles?: boolean;
  status?: UserStatus;
}

/* ========================================================================== */
/* M8 —— 收藏 / 点赞 / 数字概览（CONTRACT §23，任务书 F7~F11）                 */
/* ========================================================================== */

/* -------------------------------------------------------------------------- */
/* 收藏与点赞（§23.4）                                                          */
/* -------------------------------------------------------------------------- */

/**
 * `PUT` / `DELETE /api/v1/tools/{slug}/favorite` **与** 点赞的两个端点，
 * 共用同一个响应模型（`backend/openapi.json` 的 `ToolEngagementResponse`）。
 *
 * 契约 §23.4 只冻结了方法、路径与「幂等」语义，响应形状由后端定。后端刻意回
 * **操作后的完整状态**（两个计数 + 两个当前请求者布尔），前端因此点一次按钮就拿到
 * 最新计数，不必再拉一次详情。
 *
 * `tool_id` / `slug` 是必填，其余字段后端都带默认值，所以运行时一定存在。
 */
export interface ToolEngagementResponse {
  tool_id: number;
  slug: string;
  is_favorited: boolean;
  favorite_count: number;
  is_liked: boolean;
  like_count: number;
}

/** `GET /api/v1/me/favorites` —— 与门户列表**同一个**响应模型（`ToolListResponse`）。 */
export type FavoriteListResponse = ToolListResponse;

/* -------------------------------------------------------------------------- */
/* 管理数字概览（§23.6 冻结形状）                                               */
/* -------------------------------------------------------------------------- */

/** `downloads_daily` 的一项：恰好 30 条，缺口补 0（§23.6）。 */
export interface DailyDownloadPoint {
  /** ISO 日期（`YYYY-MM-DD`）。 */
  date: string;
  downloads: number;
}

/** `tools_by_category` 的一项（§23.6）。 */
export interface CategoryInsightItem {
  category_id: number | null;
  name: string;
  tool_count: number;
  download_count: number;
}

/**
 * 效率估算（§23.6）。
 *
 * `basis` **不是装饰**：它让接口自己声明「这是估算而不是实测」，前端因此**无法**
 * 在脱离口径的情况下单独渲染 `total_minutes`。展示时必须同时给出 `basis` 文案、
 * `covered_tool_count / total_tool_count` 覆盖率与未填工具数（任务书 F11.2）。
 *
 * `total_minutes = Σ(estimated_saving_minutes × COUNT(DISTINCT download_logs.user_id))`，
 * 仅计入 `estimated_saving_minutes IS NOT NULL` 且 `user_id IS NOT NULL` 的工具。
 */
export interface SavingsInsight {
  total_minutes: number;
  covered_tool_count: number;
  /** 全部工具数（含未填 `estimated_saving_minutes` 的）。 */
  total_tool_count: number;
  /** 常量，声明口径。 */
  basis: "author_estimate";
}

/** `GET /api/v1/admin/stats/insights`（§23.6，形状冻结）。 */
export interface AdminInsightsResponse {
  downloads_last_30_days: number;
  downloads_daily: DailyDownloadPoint[];
  /** 30 天内上传/更新过工具的去重用户数。 */
  active_contributors_30d: number;
  tools_by_category: CategoryInsightItem[];
  savings: SavingsInsight;
}
