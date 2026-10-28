/**
 * Markdown-рендер сообщений чата (05-chats-marketplace.md §8): marked +
 * DOMPurify. Подмножество: **bold**, *italic*, ~~strike~~, `code`, ссылки,
 * списки, цитаты. БЕЗ img и произвольного HTML.
 *
 * Разрешённые теги фиксирует ТЗ: p/b/i/em/s/code/pre/ul/ol/li/a/blockquote/
 * strong; добавлены br и del — их генерирует сам marked для переносов строк
 * (breaks) и зачёркивания ~~…~~ (GFM), это тот же безопасный поднабор.
 */

import { marked } from "marked";
import DOMPurify from "dompurify";

// GFM (таблицы не нужны, но strike/ссылки — из него) + переносы строк
marked.setOptions({ gfm: true, breaks: true });

const ALLOWED_TAGS = [
  "p",
  "b",
  "i",
  "em",
  "s",
  "del",
  "strong",
  "code",
  "pre",
  "ul",
  "ol",
  "li",
  "a",
  "blockquote",
  "br",
];

let hooksInstalled = false;

/** Хук DOMPurify: все ссылки открываются в новой вкладке без opener-а. */
function installHooks(): void {
  if (hooksInstalled) return;
  hooksInstalled = true;
  DOMPurify.addHook("afterSanitizeAttributes", (node) => {
    if (node.tagName === "A") {
      node.setAttribute("target", "_blank");
      node.setAttribute("rel", "noopener noreferrer");
    }
  });
}

/** markdown → безопасный HTML (только разрешённые теги, ссылки в _blank). */
export function renderMarkdown(body: string): string {
  installHooks();
  const raw = marked.parse(body, { async: false }) as string;
  return DOMPurify.sanitize(raw, {
    ALLOWED_TAGS,
    ALLOWED_ATTR: ["href", "title"],
  });
}

/** React-обёртка: html из renderMarkdown уже санитизирован. */
export function Markdown({ body }: { body: string }): JSX.Element {
  return <div className="chat-md" dangerouslySetInnerHTML={{ __html: renderMarkdown(body) }} />;
}
