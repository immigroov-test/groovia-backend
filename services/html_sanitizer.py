from html import escape
from html.parser import HTMLParser
from urllib.parse import urlparse

ALLOWED_TAGS = {
    "p", "br", "strong", "b", "em", "i", "u", "s", "code", "pre",
    "ul", "ol", "li", "h2", "h3", "h4", "a", "blockquote", "hr",
    "figure", "figcaption", "img", "table", "thead", "tbody", "tr", "th", "td",
}
VOID_TAGS = {"br", "hr", "img"}


def _safe_web_url(value: str) -> bool:
    parsed = urlparse(value)
    return parsed.scheme in {"http", "https"} and bool(parsed.netloc)


class _Sanitizer(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []
        self.suppressed = 0

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        tag = tag.lower()
        if tag in {"script", "style", "iframe", "object"}:
            self.suppressed += 1
            return
        if self.suppressed or tag not in ALLOWED_TAGS:
            return
        safe_attrs = ""
        if tag == "a":
            href = next((value for name, value in attrs if name.lower() == "href"), None)
            if href and href.startswith("/") and not href.startswith("//"):
                safe_attrs = f' href="{escape(href, quote=True)}"'
            elif href and _safe_web_url(href):
                safe_attrs = f' href="{escape(href, quote=True)}" target="_blank" rel="noopener noreferrer"'
        elif tag == "img":
            src = next((value for name, value in attrs if name.lower() == "src"), None)
            if not src or not _safe_web_url(src):
                return
            alt = next((value for name, value in attrs if name.lower() == "alt"), "") or ""
            title = next((value for name, value in attrs if name.lower() == "title"), None)
            safe_attrs = f' src="{escape(src, quote=True)}" alt="{escape(alt, quote=True)}" loading="lazy"'
            if title:
                safe_attrs += f' title="{escape(title, quote=True)}"'
        elif tag in {"th", "td"}:
            colspan = next((value for name, value in attrs if name.lower() == "colspan"), None)
            rowspan = next((value for name, value in attrs if name.lower() == "rowspan"), None)
            if colspan and colspan.isdigit() and 1 <= int(colspan) <= 12:
                safe_attrs += f' colspan="{colspan}"'
            if rowspan and rowspan.isdigit() and 1 <= int(rowspan) <= 100:
                safe_attrs += f' rowspan="{rowspan}"'
        self.parts.append(f"<{tag}{safe_attrs}>")

    def handle_endtag(self, tag: str) -> None:
        tag = tag.lower()
        if tag in {"script", "style", "iframe", "object"}:
            self.suppressed = max(0, self.suppressed - 1)
        elif not self.suppressed and tag in ALLOWED_TAGS and tag not in VOID_TAGS:
            self.parts.append(f"</{tag}>")

    def handle_data(self, data: str) -> None:
        if not self.suppressed:
            self.parts.append(escape(data))


def sanitize_html(value: str) -> str:
    parser = _Sanitizer()
    parser.feed(value or "")
    parser.close()
    return "".join(parser.parts).strip()
