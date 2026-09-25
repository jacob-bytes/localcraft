import { Navigate } from "react-router-dom";

/**
 * `/admin` —— 管理概览页属于 M3（docs/04 §6.9，CONTRACT §6.1 明确
 * `GET /admin/overview` / `/admin/stats/*` 不在 M2 冻结清单里）。
 *
 * 因此这里**只做重定向**，让 `/admin` 永远不落进死胡同：审批队列是 M2
 * 管理台唯一的入口页，直接送过去。
 */
export default function AdminIndexPage() {
  return <Navigate to="/admin/approvals" replace />;
}
