from typing import Literal

from langchain_groq import ChatGroq
from pydantic import BaseModel, Field

import config
from services.html_sanitizer import sanitize_html


class CtaSuggestion(BaseModel):
    kind: Literal["mentors", "webinars", "country"]
    label: str = Field(min_length=3, max_length=100)


class ImagePrompt(BaseModel):
    prompt: str = Field(min_length=5, max_length=300)
    alt_text: str = Field(min_length=3, max_length=160)
    caption: str = Field(default="", max_length=240)


class GeneratedArticle(BaseModel):
    title: str = Field(min_length=4, max_length=160)
    excerpt: str = Field(min_length=20, max_length=400)
    content_html: str = Field(min_length=50, max_length=100000)
    seo_title: str = Field(min_length=4, max_length=70)
    seo_description: str = Field(min_length=20, max_length=170)
    image_prompts: list[ImagePrompt] = Field(default_factory=list, max_length=4)
    ctas: list[CtaSuggestion] = Field(default_factory=list, max_length=3)


_SYSTEM_PROMPT = """You are Immigroov's senior editor. Turn rough contributor notes into a useful,
human-sounding article for people moving country. Preserve facts from the notes; never invent laws,
deadlines, statistics, personal experiences, source URLs, or guarantees. Clearly qualify anything
that needs verification. Return body HTML without an h1. Use only: p, h2, h3, strong, em, u, s,
ul, ol, li, blockquote, table, thead, tbody, tr, th, td, a, br, hr. Keep paragraphs short and use
descriptive headings. Add internal links only from the supplied Groovia URLs. Add external links
only when the exact URL appears in the supplied sources. Include 2-4 image ideas separately; in the
body, mark their recommended locations using a blockquote that begins 'Image suggestion:'. Suggest
useful, non-pushy CTAs. Do not claim that a link is a backlink. Do not include scripts, styles,
iframes, classes, tracking parameters, or fake citations."""


async def generate_article(
    *, raw_content: str, country_code: str | None, category: str,
    tone: str, sources: list[str], related_posts: list[dict] | None = None,
) -> dict:
    country = (country_code or "").upper()
    internal_urls = ["/webinars", "/mentors"]
    if country:
        internal_urls.extend([f"/countries/{country.lower()}", f"/mentors?country={country}"])
    article_links = [
        {"title": post["title"], "url": f"/blog/{post['slug']}"}
        for post in (related_posts or [])
        if post.get("title") and post.get("slug")
    ]
    internal_urls.extend(item["url"] for item in article_links)

    llm = ChatGroq(
        model=config.BLOG_WRITER_MODEL_NAME,
        temperature=0.2,
        api_key=config.GROQ_API_KEY,
        max_retries=0,
    ).with_structured_output(GeneratedArticle)
    article = await llm.ainvoke([
        ("system", _SYSTEM_PROMPT),
        ("human", f"""Country ISO code: {country or 'not selected'}
Category: {category}
Tone: {tone}
Allowed Groovia URLs: {internal_urls}
Relevant published Groovia articles: {article_links}
Contributor-provided source URLs: {sources}

ROUGH CONTENT:
{raw_content}"""),
    ])
    if not isinstance(article, GeneratedArticle):
        article = GeneratedArticle.model_validate(article)

    allowed_ctas = []
    seen: set[str] = set()
    for cta in article.ctas:
        if cta.kind in seen or (cta.kind == "country" and not country):
            continue
        seen.add(cta.kind)
        href = {
            "mentors": f"/mentors?country={country}" if country else "/mentors",
            "webinars": "/webinars",
            "country": f"/countries/{country.lower()}",
        }[cta.kind]
        allowed_ctas.append({"kind": cta.kind, "label": cta.label, "href": href})

    return {
        "title": article.title.strip(),
        "excerpt": article.excerpt.strip(),
        "content_html": sanitize_html(article.content_html),
        "seo_title": article.seo_title.strip(),
        "seo_description": article.seo_description.strip(),
        "image_prompts": [item.model_dump() for item in article.image_prompts],
        "ctas": allowed_ctas,
    }
