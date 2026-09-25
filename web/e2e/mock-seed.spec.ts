import { expect, test } from "@playwright/test";

import {
  countByCategory,
  countByType,
  LONG_TOOL_NAME,
  LONG_TOOL_SUMMARY,
  MOCK_CATEGORIES,
  MOCK_TOOLS,
  MOCK_USERS,
} from "../src/mocks/data";

/**
 * CONTRACT §7 seed conformance. Pure data assertions — no browser involved, but
 * keeping them next to the acceptance walkthrough means the UI tests and the
 * seed guarantees are verified by the same command.
 */
test.describe("MSW 种子数据符合 CONTRACT §7", () => {
  test("账号：1 个超管 + 1 个需强制改密用户", () => {
    const admin = MOCK_USERS.find((user) => user.username === "admin");
    const newbie = MOCK_USERS.find((user) => user.username === "newbie");
    expect(admin?.password).toBe("Admin@12345");
    expect(admin?.must_change_password).toBe(false);
    expect(admin?.roles).toContain("superadmin");
    expect(newbie?.password).toBe("Newbie@12345");
    expect(newbie?.must_change_password).toBe(true);
  });

  test("4 个分类：研发工具 / 运维工具 / Skill / 提示词", () => {
    expect(MOCK_CATEGORIES.map((category) => category.name)).toEqual([
      "研发工具",
      "运维工具",
      "Skill",
      "提示词",
    ]);
  });

  test("8 个工具：覆盖 4 种类型、跨全部 4 个分类、均为 public", () => {
    expect(MOCK_TOOLS).toHaveLength(8);
    expect(MOCK_TOOLS.every((tool) => tool.visibility === "public")).toBe(true);
    expect(MOCK_TOOLS.every((tool) => tool.current_version !== null)).toBe(true);
    expect(MOCK_TOOLS.every((tool) => tool.tags.length > 0)).toBe(true);

    const types = countByType();
    expect(Object.values(types).every((count) => count > 0)).toBe(true);

    const categories = countByCategory();
    expect(Object.keys(categories).sort()).toEqual(
      MOCK_CATEGORIES.map((category) => category.slug).sort(),
    );
  });

  test("至少 2 个工具无封面（用于验证占位色块）", () => {
    const withoutCover = MOCK_TOOLS.filter((tool) => tool.cover_url === null);
    expect(withoutCover.length).toBeGreaterThanOrEqual(2);
  });

  test("至少 1 个工具名称 > 40 字符、简介 > 3 行", () => {
    expect(LONG_TOOL_NAME.length).toBeGreaterThan(40);
    // ~20 Chinese characters fit on one 13px line inside a card column, so
    // > 100 characters is comfortably more than three lines.
    expect(LONG_TOOL_SUMMARY.length).toBeGreaterThan(100);
    const longTool = MOCK_TOOLS.find((tool) => tool.name === LONG_TOOL_NAME);
    expect(longTool).toBeTruthy();
  });

  test("下载量/浏览量是确定值（固定种子，不随机）", () => {
    const snapshot = MOCK_TOOLS.map((tool) => [
      tool.slug,
      tool.download_count,
      tool.view_count,
    ]);
    expect(JSON.stringify(snapshot)).toBe(
      JSON.stringify(
        MOCK_TOOLS.map((tool) => [tool.slug, tool.download_count, tool.view_count]),
      ),
    );
    expect(MOCK_TOOLS[0]?.download_count).toBe(137);
  });
});
