import asyncio
from unittest.mock import patch

from services import blog_writer
from services.html_sanitizer import sanitize_html


def test_blog_html_keeps_editor_formatting_and_safe_links():
    value = '<h3>Guide</h3><p>Read <a href="https://example.com">this</a>.</p>'
    clean = sanitize_html(value)
    assert '<h3>Guide</h3>' in clean
    assert 'rel="noopener noreferrer"' in clean


def test_blog_html_removes_scripts_and_unsafe_attributes():
    value = '<p onclick="steal()">Safe</p><script>alert(1)</script><a href="javascript:bad()">bad</a>'
    clean = sanitize_html(value)
    assert 'onclick' not in clean
    assert 'script' not in clean
    assert 'alert' not in clean
    assert 'javascript:' not in clean


def test_blog_html_keeps_article_blocks_images_and_internal_links():
    value = (
        '<h2>Documents</h2><blockquote><p>Check the original source.</p></blockquote>'
        '<a href="/mentors?country=DE">Find a mentor</a>'
        '<img src="https://cdn.example.com/guide.webp" alt="Visa documents" onerror="bad()">'
        '<table><tbody><tr><th colspan="2">Step</th></tr></tbody></table>'
    )
    clean = sanitize_html(value)
    assert '<h2>Documents</h2>' in clean
    assert 'href="/mentors?country=DE"' in clean
    assert 'src="https://cdn.example.com/guide.webp"' in clean
    assert 'alt="Visa documents"' in clean
    assert 'onerror' not in clean
    assert '<table>' in clean


def test_blog_html_rejects_protocol_relative_and_data_image_urls():
    clean = sanitize_html(
        '<a href="//evil.example">bad</a><img src="data:image/svg+xml,bad" alt="bad">'
    )
    assert 'href=' not in clean
    assert '<img' not in clean


def test_ai_writer_normalizes_cta_routes_and_sanitizes_html():
    generated = blog_writer.GeneratedArticle(
        title="A practical Germany guide",
        excerpt="A practical overview for people preparing to move to Germany.",
        content_html='<h2>Start here</h2><script>bad()</script><p>Useful notes for your move.</p>',
        seo_title="Germany relocation guide",
        seo_description="Plan a move to Germany with a clear overview of the important first steps.",
        image_prompts=[{"prompt": "A Berlin arrival scene", "alt_text": "Traveller arriving in Berlin", "caption": "Arriving in Germany"}],
        ctas=[
            {"kind": "mentors", "label": "Talk to a Germany mentor"},
            {"kind": "country", "label": "Explore Germany"},
        ],
    )

    class FakeStructuredLlm:
        async def ainvoke(self, _messages):
            return generated

    class FakeLlm:
        def with_structured_output(self, _schema):
            return FakeStructuredLlm()

    with patch.object(blog_writer, "ChatGroq", return_value=FakeLlm()):
        result = asyncio.run(blog_writer.generate_article(
            raw_content="These are sufficiently detailed rough notes about moving to Germany.",
            country_code="de",
            category="settling_in",
            tone="clear",
            sources=[],
        ))

    assert "script" not in result["content_html"]
    assert result["ctas"][0]["href"] == "/mentors?country=DE"
    assert result["ctas"][1]["href"] == "/countries/de"
    assert result["image_prompts"][0]["alt_text"] == "Traveller arriving in Berlin"
