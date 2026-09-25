/**
 * Response types mirroring docs/03 (API 接口清单) and contracts/CONTRACT.md.
 *
 * Rules taken from CONTRACT §5 / docs/03 §1.1:
 *  - snake_case field names, exactly as the backend serialises them
 *  - nullable fields are explicit `null`, never omitted
 *  - timestamps are ISO 8601 UTC strings ending in `Z`
 *  - enums are string-literal unions, mirroring the backend `enum.StrEnum`
 *
 * Fields marked `docs-undefined` are NOT specified by docs/03; they are called
 * out in the checkpoint report as contract gaps instead of being invented
 * silently. They are only consumed by the MSW mock.
 */

/* -------------------------------------------------------------------------- */
/* Enums (docs/03 §1.4, docs/02 §3.8)                                          */
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

export type VersionStatus = "pending" | "approved" | "rejected" | "purged";

/** docs/01 §3.1 */
export type Role = "viewer" | "user" | "approver" | "superadmin";

export type UserStatus = "active" | "disabled";

export type AuthProvider = "local" | "ldap" | "oidc";

export type AuthSource = "local" | "ldap" | "oidc";

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
/* Meta (docs/03 §3.1)                                                         */
/* -------------------------------------------------------------------------- */

export interface MetaFeatures {
  webapp_health_check: boolean;
  skill_preview: boolean;
}

export interface Meta {
  site_name: string;
  announcement_md: string | null;
  auth_provider: AuthProvider;
  allow_anonymous_view: boolean;
  default_sort: ToolSort;
  page_size: number;
  app_version: string;
  api_version: string;
  features: MetaFeatures;
}

/* -------------------------------------------------------------------------- */
/* Auth (docs/03 §3.2, CONTRACT §3)                                            */
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
}

export interface TokenPair {
  access_token: string;
  token_type: "bearer";
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
 * docs-undefined: docs/03 §2.2 only states the purpose ("当前启用的认证方式与
 * 登录表单字段"), no payload example. This shape is used by the MSW mock only —
 * no UI depends on it (the login page reads `Meta.auth_provider`).
 */
export interface AuthProviderResponse {
  provider: AuthProvider;
  display_name: string;
  login_fields: string[];
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
  /** docs-undefined: field name for the per-category visible tool count is not
   *  given by docs/03 §2.3 ("含各分类可见工具数"). Mirrors `facets.count`. */
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
  category: ToolCategoryRef;
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
  updated_at: string;
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
