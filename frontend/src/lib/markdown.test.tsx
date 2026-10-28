/** Тесты markdown-рендера чатов: sanitize (script/img/javascript:),
 *  ссылка target=_blank rel=noopener, подмножество разметки. */

import { describe, expect, it } from "vitest";

import { renderMarkdown } from "./markdown";

describe("renderMarkdown: sanitize", () => {
  it("разметка подмножества рендерится: bold/italic/strike/code/списки/цитаты", () => {
    expect(renderMarkdown("**важно**")).toContain("<strong>важно</strong>");
    expect(renderMarkdown("*курсив*")).toContain("<em>курсив</em>");
    expect(renderMarkdown("~~прочерк~~")).toContain("<del>прочерк</del>");
    expect(renderMarkdown("`код`")).toContain("<code>код</code>");
    expect(renderMarkdown("- один\n- два")).toContain("<ul>");
    expect(renderMarkdown("> цитата")).toContain("<blockquote>");
  });

  it("script-вставка вырезается целиком (текст вне — остаётся)", () => {
    const html = renderMarkdown("<script>alert(1)</script>Привет");
    expect(html).not.toContain("<script");
    expect(html).not.toContain("alert(1)");
    expect(html).toContain("Привет");
  });

  it("img запрещён (БЕЗ картинок в сообщениях)", () => {
    expect(renderMarkdown("![x](https://e/x.png)")).not.toContain("<img");
    expect(renderMarkdown('<img src=x onerror=alert(1)>')).not.toContain("<img");
  });

  it("ссылки открываются в _blank без opener-а", () => {
    const html = renderMarkdown("[сайт](https://example.com)");
    expect(html).toContain('href="https://example.com"');
    expect(html).toContain('target="_blank"');
    expect(html).toContain('rel="noopener noreferrer"');
  });

  it("javascript:-ссылка обезврежена (атрибут href снят)", () => {
    expect(renderMarkdown("[зло](javascript:alert(1))")).not.toContain("javascript:");
  });

  it("переносы строк дают br (breaks)", () => {
    expect(renderMarkdown("строка 1\nстрока 2")).toContain("<br>");
  });
});
