import { ShieldX } from "lucide-react";
import { Link } from "react-router-dom";

import { EmptyState } from "@/components/common/EmptyState";
import { Button } from "@/components/ui/button";

/** Rendered by `RequireRole` when the current role is insufficient (docs/04 §4). */
export function ForbiddenPage() {
  return (
    <div className="mx-auto w-full max-w-[1400px] px-4 py-16 sm:px-6 lg:px-8">
      <EmptyState
        icon={ShieldX}
        title="没有访问权限"
        description="当前账号的角色无法访问该页面。如果这看起来不对，请联系管理员。"
        action={
          <Button asChild variant="outline">
            <Link to="/">返回首页</Link>
          </Button>
        }
      />
    </div>
  );
}
