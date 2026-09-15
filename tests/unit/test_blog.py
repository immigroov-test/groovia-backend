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
