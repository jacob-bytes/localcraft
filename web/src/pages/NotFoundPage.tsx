import { SearchX } from "lucide-react";
import { Link } from "react-router-dom";

import { EmptyState } from "@/components/common/EmptyState";
import { Button } from "@/components/ui/button";

/**
 * 404 (docs/04 §6.22). Deliberately terse: "页面不存在或你没有访问权限".
 * No "possible reasons" list — for unauthorised resources that would leak
 * information.
 */
export default function NotFoundPage() {
  return (
    <div className="mx-auto w-full max-w-[1400px] px-4 py-16 sm:px-6 lg:px-8">
      <p
        aria-hidden="true"
        className="text-center text-6xl font-semibold tracking-tight text-muted-foreground/40"
      >
        404
      </p>
      <EmptyState
        className="border-none"
        icon={SearchX}
        title="页面不存在或你没有访问权限"
        action={
          <Button asChild>
            <Link to="/">返回首页</Link>
          </Button>
        }
      />
    </div>
  );
}
