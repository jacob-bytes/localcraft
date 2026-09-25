#!/usr/bin/env node
/**
 * 三方类型比对（CONTRACT §15.6：**`backend/openapi.json` 是接口形状的权威**）。
 *
 * 比对 `backend/openapi.json` 的响应模型与 `src/api/types.ts` 的 interface：
 *   - 字段名：缺字段 / 多字段 → 失败
 *   - 字段类型：数字 vs 字符串、布尔 vs 字符串、数组 vs 标量、可空性差异 → 失败
 *   - 其余（枚举别名、`object` 内联结构）按宽松规则放过，只在 `--verbose` 下提示
 *
 * 挂在 `npm run verify` 里。后端新增字段而前端未跟进时**这里会失败**，这正是目的
 * （M2 的 `version_seq`、M3 的 `missing_scopes` 都是这类漂移）。
 *
 * 用法：
 *   node scripts/check-api-types.mjs            # 比对，失败即非零退出
 *   VERBOSE_API_TYPES=1 node scripts/check-api-types.mjs
 */

import { existsSync, readFileSync } from "node:fs";
import { dirname, join, resolve } from "node:path";
import { fileURLToPath } from "node:url";

const webRoot = resolve(dirname(fileURLToPath(import.meta.url)), "..");
// 允许指向另一份 openapi（用于「J2 落地后会通过吗」的预演，不改 backend/）
const openapiPath = process.env.OPENAPI_PATH
  ? resolve(process.env.OPENAPI_PATH)
  : resolve(webRoot, "..", "backend", "openapi.json");
const typesPath = join(webRoot, "src", "api", "types.ts");

if (!existsSync(openapiPath)) {
  console.error(`check-api-types: 找不到 ${openapiPath}（后端产物未生成？）`);
  process.exit(1);
}

const openapi = JSON.parse(readFileSync(openapiPath, "utf8"));
const schemas = openapi.components?.schemas ?? {};
const tsSource = readFileSync(typesPath, "utf8");

/* -------------------------------------------------------------------------- */
/* 解析 types.ts 的 interface                                                   */
/* -------------------------------------------------------------------------- */

/** 去掉注释，避免 `/** … *\/` 把字段行吞掉。 */
const tsClean = tsSource
  .replace(/\/\*[\s\S]*?\*\//g, "")
  .replace(/^\s*\/\/.*$/gm, "");

/** name → Map<field, rawType> */
function parseInterfaceBody(body) {
  const fields = new Map();
  let depth = 0;
  let current = "";
  for (const char of body) {
    if (char === "{" || char === "(" || char === "[") depth += 1;
    if (char === "}" || char === ")" || char === "]") depth -= 1;
    if (char === ";" && depth <= 0) {
      const fieldMatch = /^\s*(\w+)\??\s*:\s*([\s\S]+)$/.exec(current);
      if (fieldMatch) fields.set(fieldMatch[1], fieldMatch[2].replace(/\s+/g, " ").trim());
      current = "";
      continue;
    }
    current += char;
  }
  return fields;
}

/** 抓所有 interface（含 extends 链）与 type 别名。 */
const rawInterfaces = []; // { name, extendsName, body }
const typeAliases = new Map();

for (const match of tsClean.matchAll(/export interface (\w+)(?:\s+extends\s+([\w<>, ]+))?\s*\{/g)) {
  const name = match[1];
  const extendsName = match[2]?.trim() ?? null;
  const start = match.index + match[0].length;
  let depth = 1;
  let end = start;
  while (end < tsClean.length && depth > 0) {
    const char = tsClean[end];
    if (char === "{") depth += 1;
    else if (char === "}") depth -= 1;
    end += 1;
  }
  rawInterfaces.push({ name, extendsName, body: tsClean.slice(start, end - 1) });
}

for (const match of tsClean.matchAll(/export type (\w+)\s*=\s*([^;]+);/g)) {
  typeAliases.set(match[1], match[2].replace(/\s+/g, " ").trim());
}

const tsInterfaces = new Map();
function resolveInterface(name, seen = new Set()) {
  if (tsInterfaces.has(name)) return tsInterfaces.get(name);
  if (seen.has(name)) return new Map();
  seen.add(name);

  const declared = rawInterfaces.find((item) => item.name === name);
  let fields = new Map();
  if (declared) {
    if (declared.extendsName) {
      for (const parent of declared.extendsName.split(",").map((part) => part.trim())) {
        const base = parent.replace(/<.*$/, "");
        const inherited = resolveInterface(base, seen);
        for (const [key, value] of inherited) fields.set(key, value);
      }
    }
    for (const [key, value] of parseInterfaceBody(declared.body)) fields.set(key, value);
  } else if (typeAliases.has(name)) {
    // Paginated<T> 之类的别名：合成分页信封字段
    const alias = typeAliases.get(name);
    if (/^Paginated</.test(alias)) {
      for (const key of ["items", "total", "page", "page_size", "pages"]) {
        fields.set(key, key === "items" ? "unknown[]" : "number");
      }
    }
  }
  tsInterfaces.set(name, fields);
  return fields;
}

for (const item of rawInterfaces) resolveInterface(item.name);
for (const name of typeAliases.keys()) resolveInterface(name);

/* -------------------------------------------------------------------------- */
/* openapi schema → 字段表                                                      */
/* -------------------------------------------------------------------------- */

function openapiFields(name) {
  const schema = schemas[name];
  if (!schema?.properties) return null;
  const fields = new Map();
  for (const [field, spec] of Object.entries(schema.properties)) {
    fields.set(field, spec);
  }
  return fields;
}

function refName(spec) {
  if (!spec) return "any";
  if (spec.$ref) return spec.$ref.split("/").pop();
  if (spec.anyOf) {
    const parts = spec.anyOf.map(refName).filter((part) => part !== "any");
    return parts.length ? [...new Set(parts)].join(" | ") : "any";
  }
  if (spec.type === "array") return `list[${refName(spec.items)}]`;
  if (spec.type === "object") return "object";
  if (spec.enum) return "enum";
  return spec.type ?? "any";
}

/** 归一化成比较用的类别：number / string / boolean / list / object / any */
function category(spec) {
  const name = refName(spec);
  if (name === "any" || name === "object" || name === "enum") return "any";
  if (name.includes(" | ")) {
    const parts = name.split(" | ").filter((part) => part !== "null");
    if (parts.length === 0) return "any";
    if (parts.length === 1) return classifyNamed(parts[0]);
    return parts.every((part) => classifyNamed(part) === classifyNamed(parts[0]))
      ? classifyNamed(parts[0])
      : "any";
  }
  return classifyNamed(name);
}

function classifyNamed(name) {
  if (name === "integer" || name === "number") return "number";
  if (name === "string") return "string";
  if (name === "boolean") return "boolean";
  if (name.startsWith("list[")) return "list";
  if (name === "any" || name === "object") return "any";
  return "ref";
}

/** TS 侧类型归一化（`string | null` → string，`unknown` → any）。 */
function tsCategory(raw) {
  const text = raw.replace(/\s+/g, " ").trim();
  if (text === "unknown" || text === "any") return "any";
  const parts = text
    .split("|")
    .map((part) => part.trim())
    .filter((part) => part !== "null" && part !== "undefined");
  if (parts.length === 0) return "any";
  const first = parts[0];
  if (parts.every((part) => part === first)) {
    if (first === "number") return "number";
    if (first === "string") return "string";
    if (first === "boolean") return "boolean";
    if (first.endsWith("[]") || first.startsWith("Array<")) return "list";
    if (first.startsWith("{") || first === "object") return "any";
    return "ref";
  }
  const categories = new Set(parts.map((part) => tsCategory(part)));
  if (categories.size === 1) return [...categories][0];
  return "any";
}

function isNullableTs(raw) {
  return raw.includes("null");
}

function isNullableOpenapi(spec) {
  return refName(spec)
    .split(" | ")
    .some((part) => part === "null");
}

/* -------------------------------------------------------------------------- */
/* 比对表：openapi 模型 ←→ types.ts interface                                   */
/* -------------------------------------------------------------------------- */

/** 后端模型名 → 前端 interface 名（已知改名集中在这里，改错会立刻失败）。 */
const ALIASES = {
  UserBrief: "ToolOwner",
  CategoryBrief: "ToolCategoryRef",
  RoleCode: "Role",
  ToolVisibility: "Visibility",
  UserMe: "User",
  TokenResponse: "TokenPair",
  AclEntryOut: "AclEntry",
  UsageOut: "Usage",
  ProfileOut: "Profile",
  MyToolListItem: "MyToolListItem",
  AdminToolItem: "AdminToolItem",
};

const PAIRS = [
  // M1
  "MetaResponse→Meta",
  "UserMe→User",
  "TokenResponse→TokenPair",
  "AuthProviderResponse→AuthProviderResponse",
  "CategoryOut→Category",
  "TagOut→Tag",
  "ToolListItem→ToolListItem",
  "ToolFacets→ToolFacets",
  "CategoryFacet→FacetCategory",
  "TypeFacet→FacetType",
  "FieldError→FieldError",
  // M2
  "ToolDetail→ToolDetail",
  "VersionSummary→VersionSummary",
  "VersionDetail→VersionDetail",
  "SkillPreviewResponse→SkillPreview",
  "DownloadTicketResponse→DownloadTicket",
  "MyToolListItem→MyToolListItem",
  "MyToolListResponse→MyToolListResponse",
  "ProfileOut→Profile",
  "UsageOut→Usage",
  "DownloadLogOut→DownloadLogItem",
  "SkillVersionInfo→SkillVersionInfo",
  "SkillTreeSummary→SkillTreeSummary",
  "UploadedFilePlaceholder→__skip__",
  "ApprovalQueueItem→ApprovalQueueItem",
  "ApproveResponse→ApproveResponse",
  "OfflineResponse→OfflineResponse",
  "RelistResponse→RelistResponse",
  "RejectResponse→RejectResponse",
  "ApprovalRecordOut→ApprovalRecord",
  "WhitelistEntryOut→WhitelistEntry",
  "SettingItem→SettingItem",
  "SettingListResponse→SettingListResponse",
  "SettingWarning→SettingWarning",
  "VersionUploadResponse→VersionUploadResponse",
  "VersionUploader→VersionUploader",
  "ImageOut→ToolImage",
  "ToolStatsResponse→ToolStats",
  "DailyStatPoint→__inline__",
  // M3
  "RoleOut→RoleOut",
  "AdminUserItem→AdminUserItem",
  "AdminUserListResponse→AdminUserListResponse",
  "AdminUserCreateResponse→AdminUserCreateResponse",
  "AdminUserCreateRequest→AdminUserCreateRequest",
  "AdminUserUpdateRequest→AdminUserUpdateRequest",
  "AdminRoleReplaceRequest→AdminRoleReplaceRequest",
  "ResetPasswordResponse→ResetPasswordResponse",
  "RevokeSessionsResponse→RevokeSessionsResponse",
  "GroupOut→GroupOut",
  "GroupListResponse→GroupListResponse",
  "GroupMemberListResponse→GroupMemberListResponse",
  "GroupMemberOut→GroupMemberOut",
  "GroupMemberAddResponse→GroupMemberAddResponse",
  "GroupCreateRequest→GroupCreateRequest",
  "GroupUpdateRequest→GroupUpdateRequest",
  "AdminCategoryOut→AdminCategoryOut",
  "AdminCategoryCreateRequest→AdminCategoryCreateRequest",
  "AdminCategoryUpdateRequest→AdminCategoryUpdateRequest",
  "CategoryOrderRequest→CategoryOrderRequest",
  "AdminTagOut→AdminTagOut",
  "AdminTagListResponse→AdminTagListResponse",
  "TagRenameRequest→TagRenameRequest",
  "TagMergeRequest→TagMergeRequest",
  "TagMergeResponse→TagMergeResponse",
  "TagCleanupResponse→TagCleanupResponse",
  "ApiTokenOut→ApiTokenOut",
  "ApiTokenListResponse→ApiTokenListResponse",
  "ApiTokenCreateResponse→ApiTokenCreateResponse",
  "ApiTokenCreateRequest→ApiTokenCreateRequest",
  "StatusCount→StatusCount",
  "AdminOverviewResponse→AdminOverviewResponse",
  "ToolRankItem→ToolRankItem",
  "ToolRankResponse→ToolRankResponse",
  "StorageOwnerItem→StorageOwnerItem",
  "StorageStatsResponse→StorageStatsResponse",
  "AdminToolItem→AdminToolItem",
  "AdminToolListResponse→AdminToolListResponse",
  "TransferOwnerRequest→TransferOwnerRequest",
  "TransferOwnerResponse→TransferOwnerResponse",
  "PurgeToolResponse→PurgeToolResponse",
  "ImportErrorItem→ImportErrorItem",
  "ImportResultResponse→ImportResultResponse",
  "GeneratedPassword→GeneratedPassword",
  "ToolImportItem→ToolImportItem",
  "ToolImportRequest→ToolImportRequest",
];

/* -------------------------------------------------------------------------- */
/* 响应覆盖检查（M4 交付项 4）                                                  */
/* -------------------------------------------------------------------------- */

/**
 * **允许「没有命名形状」的响应** —— 只有这几类：健康检查、导出文件流、
 * 以及 `{"status":"ok"}` 这种动作回执。其余任何 `additionalProperties` /
 * 空 schema 都**报错**。
 *
 * 为什么必须报错而不是跳过：`§15.6` 宣布 openapi 是形状权威，可一旦某个响应是
 * `additionalProperties: true`，它就没有任何字段可比 —— 若守卫在这里静默跳过，
 * 这些端点会长期处在盲区里（M3 的 6 个端点就是这么漏掉的）。
 */
const OPERATIONS_WITHOUT_SHAPE = new Map([
  ["GET /readyz", "健康检查回执"],
  ["GET /api/v1/admin/export/users", "CSV 文件流，schema 为空"],
  ["GET /api/v1/admin/export/tools", "JSON 文件流，schema 为空"],
  ["POST /api/v1/auth/logout", "动作回执 {status}"],
  ["POST /api/v1/auth/change-password", "动作回执 {status}"],
  ["DELETE /api/v1/me/tools/{tool_id}", "动作回执 {status}"],
  ["DELETE /api/v1/me/tools/{tool_id}/images/{image_id}", "动作回执 {status}"],
  ["DELETE /api/v1/me/tools/{tool_id}/versions/{version}", "动作回执 {status}"],
  ["DELETE /api/v1/admin/categories/{category_id}", "动作回执 {status}"],
  ["DELETE /api/v1/admin/groups/{group_id}", "动作回执 {status} + cleaned_acl_entries"],
  ["DELETE /api/v1/admin/groups/{group_id}/members/{user_id}", "动作回执 {status}"],
  ["DELETE /api/v1/admin/tokens/{token_id}", "动作回执 {status}"],
  ["DELETE /api/v1/admin/approval-whitelist/{user_id}", "动作回执 {status}"],
]);

/** 响应里引用的模型若不在 PAIRS 里，至少要能用这些理由说清楚为什么不比。 */
const UNCHECKED_RESPONSE_MODELS = new Map([
  ["HealthResponse", "健康检查回执，字段固定且不参与前端渲染"],
  ["HTTPValidationError", "错误信封，前端由 ApiError 统一解析"],
  ["ValidationError", "错误信封detail项，同上"],
]);

const typesNames = new Set([
  ...rawInterfaces.map((item) => item.name),
  ...typeAliases.keys(),
]);

const pairedOpenapiNames = new Set(PAIRS.map((pair) => pair.split("→")[0]));
const pairedTsNames = new Set(
  PAIRS.map((pair) => ALIASES[pair.split("→")[1]] ?? pair.split("→")[1]),
);

/** `Page_ApprovalQueueItem_` 这类自动生成的分页信封 → 真正的条目模型。 */
function normalizeModelName(name) {
  const page = /^Page_(.+)_$/.exec(name);
  return page ? page[1] : name;
}

function modelCovered(name) {
  const model = normalizeModelName(name);
  if (pairedOpenapiNames.has(model) || pairedOpenapiNames.has(name)) return true;
  if (typesNames.has(model) || typesNames.has(name)) return true;
  const aliased = ALIASES[model];
  if (aliased && (typesNames.has(aliased) || pairedTsNames.has(aliased))) return true;
  return UNCHECKED_RESPONSE_MODELS.has(model);
}

/** 递归收集任意深度里的 `$ref` 模型名。 */
function collectRefs(node, out) {
  if (!node || typeof node !== "object") return;
  if (Array.isArray(node)) {
    for (const item of node) collectRefs(item, out);
    return;
  }
  if (typeof node.$ref === "string") out.add(node.$ref.split("/").pop());
  for (const value of Object.values(node)) collectRefs(value, out);
}

/**
 * 顶层响应 schema → { models, shapeless }。
 * `shapeless` = 拿不到任何命名形状（`additionalProperties` / 空 schema / 无 properties）。
 */
function topLevelResponseModels(schema) {
  if (!schema) return { models: new Set(), shapeless: true };
  if (typeof schema.$ref === "string") {
    return { models: new Set([schema.$ref.split("/").pop()]), shapeless: false };
  }
  if (schema.type === "array") return topLevelResponseModels(schema.items);
  if (schema.properties && Object.keys(schema.properties).length > 0) {
    const models = new Set();
    collectRefs(schema.properties, models);
    return { models, shapeless: false };
  }
  if (schema.additionalProperties) {
    return { models: new Set(), shapeless: true };
  }
  return { models: new Set(), shapeless: true };
}

const responseFailures = [];
let operationsChecked = 0;

for (const [path, operations] of Object.entries(openapi.paths ?? {})) {
  for (const [method, operation] of Object.entries(operations)) {
    if (!["get", "post", "put", "patch", "delete"].includes(method)) continue;
    for (const [code, response] of Object.entries(operation.responses ?? {})) {
      if (!code.startsWith("2")) continue;
      const schema = response.content?.["application/json"]?.schema;
      if (schema === undefined) continue; // 无 JSON 体（204/文件流已单独处理）

      const key = `${method.toUpperCase()} ${path}`;
      const { models, shapeless } = topLevelResponseModels(schema);
      operationsChecked += 1;

      if (shapeless) {
        if (!OPERATIONS_WITHOUT_SHAPE.has(key)) {
          responseFailures.push(
            `${key} 的 ${code} 响应在 openapi.json 里**没有字段定义**（additionalProperties / 空 schema）` +
              ` —— 形状权威必须能给出字段：补 response_model，或加入 OPERATIONS_WITHOUT_SHAPE 并写明理由`,
          );
        }
        continue;
      }

      if (models.size === 0) {
        responseFailures.push(`${key} 的 ${code} 响应有 properties 但没有任何 $ref 模型可比`);
        continue;
      }
      for (const model of models) {
        if (!modelCovered(model)) {
          responseFailures.push(
            `${key} 的 ${code} 响应引用了未纳入比对的模型 ${model}` +
              ` —— 加进 PAIRS，或加入 UNCHECKED_RESPONSE_MODELS 说明理由`,
          );
        }
      }
    }
  }
}

/* -------------------------------------------------------------------------- */
/* 执行比对                                                                     */
/* -------------------------------------------------------------------------- */

const failures = [];
const notes = [];
let checked = 0;

for (const pair of PAIRS) {
  const [openapiName, rawTsName] = pair.split("→");
  const tsName = ALIASES[rawTsName] ?? rawTsName;
  if (tsName === "__skip__" || tsName === "__inline__") continue;

  const openapiFieldMap = openapiFields(openapiName);
  const tsFieldMap = tsInterfaces.get(tsName);

  if (!openapiFieldMap) {
    notes.push(`openapi 无 ${openapiName}（后端已改名？）`);
    continue;
  }
  if (!tsFieldMap) {
    failures.push(`types.ts 缺少 interface ${tsName}（对应 openapi ${openapiName}）`);
    continue;
  }
  checked += 1;

  for (const [field, spec] of openapiFieldMap) {
    if (!tsFieldMap.has(field)) {
      failures.push(`${tsName}.${field} 缺失（openapi ${openapiName} 有该字段）`);
      continue;
    }
    const tsRaw = tsFieldMap.get(field);
    const expected = category(spec);
    const actual = tsCategory(tsRaw);
    const strict = new Set(["number", "string", "boolean", "list"]);
    if (
      strict.has(expected) &&
      strict.has(actual) &&
      expected !== actual
    ) {
      failures.push(
        `${tsName}.${field} 类型不符：openapi=${expected}(${refName(spec)}) vs types=${actual}(${tsRaw})`,
      );
    }
    if (isNullableOpenapi(spec) !== isNullableTs(tsRaw)) {
      const openapiNullable = isNullableOpenapi(spec);
      // 只有「openapi 可空而 types 不可空」才是危险方向（运行时会收到 null）
      if (openapiNullable) {
        failures.push(`${tsName}.${field} 可空性不符：openapi 可空，types 未标 null`);
      } else {
        notes.push(`${tsName}.${field} types 标了 null 但 openapi 不可空（宽松放过）`);
      }
    }
  }

  for (const field of tsFieldMap.keys()) {
    if (!openapiFieldMap.has(field)) {
      failures.push(`${tsName}.${field} 多余（openapi ${openapiName} 没有该字段）`);
    }
  }
}

/* -------------------------------------------------------------------------- */

if (process.env.VERBOSE_API_TYPES === "1") {
  for (const note of notes) console.log(`  · ${note}`);
}

if (failures.length > 0 || responseFailures.length > 0) {
  console.error("check-api-types: 失败（openapi.json 是形状权威，见 CONTRACT §15.6）\n");
  // 两类失败**一起报**，否则前一类会掩盖后一类（M4 时踩过一次）
  if (failures.length > 0) {
    console.error(`【模型字段比对】${failures.length} 处：`);
    for (const item of failures) console.error(`  ✗ ${item}`);
    console.error(`  （已比对 ${checked} 个模型）`);
  }
  if (responseFailures.length > 0) {
    console.error(`\n【响应覆盖检查】${responseFailures.length} 处：`);
    for (const item of responseFailures) console.error(`  ✗ ${item}`);
    console.error(`  （已覆盖 ${operationsChecked} 个 2xx JSON 响应）`);
  }
  process.exit(1);
}

console.log(
  `check-api-types: 通过（比对 ${checked} 个模型；覆盖 ${operationsChecked} 个 2xx 响应；` +
    `字段名、类型类别、可空性与 openapi.json 一致）`,
);
