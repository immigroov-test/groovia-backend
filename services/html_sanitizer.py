from html import escape
from html.parser import HTMLParser

ALLOWED_TAGS = {"p", "br", "strong", "b", "em", "i", "u", "ul", "ol", "li", "h3", "h4", "a"}
VOID_TAGS = {"br"}


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
            if href and href.startswith(("https://", "http://")):
                safe_attrs = f' href="{escape(href, quote=True)}" target="_blank" rel="noopener noreferrer"'
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
