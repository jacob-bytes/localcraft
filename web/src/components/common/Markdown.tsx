import * as React from "react";

import { renderMarkdown } from "@/lib/markdown";
import { cn } from "@/lib/utils";

/**
 * The single Markdown rendering component (docs/04 §7.1). Sanitisation lives in
 * `lib/markdown.ts`; code blocks are highlighted with Shiki, imported lazily on
 * first use so neither the highlighter nor any grammar enters the first-load
 * bundle (docs/04 §9).
 *
 * Shiki is loaded through its fine-grained entry points (`shiki/core` + the
 * JavaScript regex engine + one module per grammar). Importing the convenience
 * `shiki` entry instead would emit a chunk for every language Shiki knows,
 * which is not acceptable for a portal whose first paint has no code at all.
 */

interface Highlighter {
  codeToHtml: (code: string, options: { lang: string; theme: string }) => string;
}

const GRAMMAR_LOADERS: Record<string, () => Promise<unknown>> = {
  bash: () => import("shiki/langs/bash.mjs"),
  json: () => import("shiki/langs/json.mjs"),
  javascript: () => import("shiki/langs/javascript.mjs"),
  typescript: () => import("shiki/langs/typescript.mjs"),
  tsx: () => import("shiki/langs/tsx.mjs"),
  jsx: () => import("shiki/langs/jsx.mjs"),
  python: () => import("shiki/langs/python.mjs"),
  sql: () => import("shiki/langs/sql.mjs"),
  yaml: () => import("shiki/langs/yaml.mjs"),
  markdown: () => import("shiki/langs/markdown.mjs"),
  shell: () => import("shiki/langs/shellscript.mjs"),
  log: () => import("shiki/langs/log.mjs"),
};

/** markdown-it fence info string → Shiki grammar id. */
const LANG_ALIASES: Record<string, string> = {
  sh: "shell",
  shell: "shell",
  zsh: "shell",
  bash: "bash",
  console: "shell",
  py: "python",
  ts: "typescript",
  js: "javascript",
  yml: "yaml",
  md: "markdown",
  postgres: "sql",
  psql: "sql",
};

let highlighterPromise: Promise<Highlighter> | null = null;

async function createHighlighter(): Promise<Highlighter> {
  const [{ createHighlighterCore }, { createJavaScriptRegexEngine }] = await Promise.all([
    import("shiki/core"),
    import("shiki/engine/javascript"),
  ]);

  const highlighter = await createHighlighterCore({
    themes: [
      import("shiki/themes/github-light.mjs"),
      import("shiki/themes/github-dark.mjs"),
    ] as never,
    langs: Object.values(GRAMMAR_LOADERS).map((load) => load()) as never,
    engine: createJavaScriptRegexEngine(),
  });

  return highlighter as unknown as Highlighter;
}

function getHighlighter(): Promise<Highlighter> {
  if (!highlighterPromise) {
    highlighterPromise = createHighlighter().catch((error: unknown) => {
      highlighterPromise = null; // allow a later retry
      throw error;
    });
  }
  return highlighterPromise;
}

export interface MarkdownProps {
  source: string | null | undefined;
  className?: string;
}

export function Markdown({ source, className }: MarkdownProps) {
  const containerRef = React.useRef<HTMLDivElement>(null);
  const html = React.useMemo(() => renderMarkdown(source), [source]);

  // Progressive enhancement: only import Shiki when a code block is present, and
  // only rewrite nodes inside this container. Shiki HTML-escapes the code text
  // it is given, so the injected markup cannot carry attacker-controlled HTML.
  React.useEffect(() => {
    const container = containerRef.current;
    if (!container) return;
    const blocks = Array.from(container.querySelectorAll("pre > code"));
    if (blocks.length === 0) return;

    let cancelled = false;
    void (async () => {
      let highlighter: Highlighter;
      try {
        highlighter = await getHighlighter();
      } catch {
        return; // losing syntax highlighting is a graceful degradation
      }
      if (cancelled) return;
      const isDark = document.documentElement.classList.contains("dark");
      const theme = isDark ? "github-dark" : "github-light";
      for (const block of blocks) {
        const pre = block.parentElement;
        if (!pre) continue;
        const languageClass = Array.from(block.classList).find((name) =>
          name.startsWith("language-"),
        );
        const fenceLang = (languageClass?.slice("language-".length) || "").toLowerCase();
        const lang = LANG_ALIASES[fenceLang] ?? fenceLang;
        if (!lang || !(lang in GRAMMAR_LOADERS)) continue; // plain <pre> is fine
        try {
          pre.outerHTML = highlighter.codeToHtml(block.textContent ?? "", { lang, theme });
        } catch {
          /* unknown language — keep the plain <pre> */
        }
      }
    })();

    return () => {
      cancelled = true;
    };
  }, [html]);

  return (
    <div
      ref={containerRef}
      className={cn("prose prose-sm dark:prose-invert max-w-none", className)}
      // Safe: markdown-it runs with html:false and DOMPurify applies the
      // tag/attribute/URI allow-list in `renderMarkdown`.
      dangerouslySetInnerHTML={{ __html: html }}
    />
  );
}
