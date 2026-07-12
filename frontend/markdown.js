// Minimal, purpose-built markdown-to-HTML renderer for LLM-generated
// summaries. Deliberately not a full CommonMark implementation -- there's
// no bundler in this app to pull in a real markdown library, and the
// actual formatting surface (Claude's summary prompt output) only ever
// needs headings, bold/italic, inline code, and bullet/numbered lists.
//
// Safety: escapeHtml runs FIRST on the raw text, before any markdown
// transform. Every transform after that only wraps already-escaped text
// in tags this module constructs itself as literal strings -- so nothing
// in the source text (even something adversarial, since this ultimately
// comes from a video's audio content the user doesn't control) can ever
// become real markup.

function escapeHtml(text) {
  return text
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;")
    .replace(/'/g, "&#39;");
}

function renderInline(text) {
  let html = escapeHtml(text);
  html = html.replace(/\*\*(.+?)\*\*/g, "<strong>$1</strong>");
  html = html.replace(/__(.+?)__/g, "<strong>$1</strong>");
  html = html.replace(/`(.+?)`/g, "<code>$1</code>");
  html = html.replace(/\*(.+?)\*/g, "<em>$1</em>");
  html = html.replace(/(?<![\w])_(.+?)_(?![\w])/g, "<em>$1</em>");
  return html;
}

export function renderMarkdown(markdownText) {
  if (!markdownText) return "";
  const lines = markdownText.replace(/\r\n/g, "\n").split("\n");
  const htmlParts = [];
  let listItems = null; // { tag: "ul" | "ol", items: string[] }
  let paragraphLines = [];

  const flushParagraph = () => {
    if (paragraphLines.length) {
      htmlParts.push(`<p>${renderInline(paragraphLines.join(" "))}</p>`);
      paragraphLines = [];
    }
  };
  const flushList = () => {
    if (listItems) {
      const itemsHtml = listItems.items.map((item) => `<li>${renderInline(item)}</li>`).join("");
      htmlParts.push(`<${listItems.tag}>${itemsHtml}</${listItems.tag}>`);
      listItems = null;
    }
  };

  for (const rawLine of lines) {
    const line = rawLine.trim();
    if (!line) {
      flushParagraph();
      flushList();
      continue;
    }

    const headingMatch = line.match(/^(#{1,4})\s+(.*)$/);
    if (headingMatch) {
      flushParagraph();
      flushList();
      const level = headingMatch[1].length;
      htmlParts.push(`<h${level}>${renderInline(headingMatch[2])}</h${level}>`);
      continue;
    }

    const bulletMatch = line.match(/^[-*]\s+(.*)$/);
    if (bulletMatch) {
      flushParagraph();
      if (!listItems || listItems.tag !== "ul") {
        flushList();
        listItems = { tag: "ul", items: [] };
      }
      listItems.items.push(bulletMatch[1]);
      continue;
    }

    const numberedMatch = line.match(/^\d+\.\s+(.*)$/);
    if (numberedMatch) {
      flushParagraph();
      if (!listItems || listItems.tag !== "ol") {
        flushList();
        listItems = { tag: "ol", items: [] };
      }
      listItems.items.push(numberedMatch[1]);
      continue;
    }

    flushList();
    paragraphLines.push(line);
  }
  flushParagraph();
  flushList();
  return htmlParts.join("");
}
