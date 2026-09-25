import DOMPurify, { type Config as DOMPurifyConfig } from "dompurify";
import MarkdownIt from "markdown-it";

/**
 * Markdown → sanitised HTML pipeline (docs/04 §7.1). Kept out of the component
 * file so the renderer can be reused by previews and tests.
 *
 * Defence in depth:
 *  1. `markdown-it` runs with `html: false` — raw HTML in the source is escaped,
 *     so `<script>` can never reach the DOM as markup.
 *  2. The rendered HTML then goes through DOMPurify with a tag/attribute
 *     allow-list and an `ALLOWED_URI_REGEXP` limited to https?/mailto, which
 *     kills `javascript:` and `data:` URLs.
 */

const md = new MarkdownIt({
  html: false, // no raw HTML, ever
  linkify: true,
  breaks: false,
});

/* External links open in a new tab, safely. */
const defaultLinkOpen =
  md.renderer.rules.link_open ??
  ((tokens, idx, options, _env, self) => self.renderToken(tokens, idx, options));

md.renderer.rules.link_open = (tokens, idx, options, env, self) => {
  const token = tokens[idx];
  if (token) {
    token.attrSet("target", "_blank");
    token.attrSet("rel", "noopener noreferrer");
  }
  return defaultLinkOpen(tokens, idx, options, env, self);
};

const ALLOWED_TAGS = [
  "p",
  "br",
  "hr",
  "strong",
  "em",
  "del",
  "s",
  "code",
  "pre",
  "blockquote",
  "ul",
  "ol",
  "li",
  "a",
  "h1",
  "h2",
  "h3",
  "h4",
  "h5",
  "h6",
  "table",
  "thead",
  "tbody",
  "tr",
  "th",
  "td",
  "span",
  "div",
];

const ALLOWED_ATTR = ["href", "title", "class", "target", "rel"];

const SANITIZE_CONFIG: DOMPurifyConfig = {
  ALLOWED_TAGS,
  ALLOWED_ATTR,
  // Only https?, mailto: survive — javascript:, data:, vbscript: are dropped.
  ALLOWED_URI_REGEXP: /^(?:https?|mailto):/i,
  ALLOW_DATA_ATTR: false,
};

export function renderMarkdown(source: string | null | undefined): string {
  const raw = md.render(source ?? "");
  return String(DOMPurify.sanitize(raw, SANITIZE_CONFIG));
}

export function markdownHasCodeBlock(html: string): boolean {
  return html.includes("<pre>") || html.includes("<pre ");
}
