from unittest.mock import patch, MagicMock


def test_parse_pdf_returns_string(sample_pdf_bytes):
    from utils import parse_pdf_to_text
    result = parse_pdf_to_text(sample_pdf_bytes)
    assert isinstance(result, str)


def test_parse_pdf_blank_page_is_empty(sample_pdf_bytes):
    from utils import parse_pdf_to_text
    assert parse_pdf_to_text(sample_pdf_bytes) == ""


def test_parse_docx_calls_docx2txt():
    from utils import parse_docx_to_text
    with patch("utils.docx2txt.process", return_value="Parsed resume text") as mock_proc:
        result = parse_docx_to_text(b"fake docx bytes")
        mock_proc.assert_called_once()
        assert result == "Parsed resume text"


def test_web_search_returns_error_string_on_exception():
    from utils import web_search
    mock_tavily = MagicMock()
    mock_tavily.invoke.side_effect = RuntimeError("network error")
    with patch("utils._tavily", mock_tavily):
        result = web_search.invoke({"query": "tech jobs in Germany"})
    assert "[SEARCH_ERROR]" in result
    assert "web_search" in result


def test_web_search_returns_string_on_success():
    from utils import web_search
    mock_result = [{"title": "Tech scene in Germany", "url": "https://example.com", "content": "Germany has a booming tech sector."}]
    mock_tavily = MagicMock()
    mock_tavily.invoke.return_value = mock_result
    with patch("utils._tavily", mock_tavily):
        result = web_search.invoke({"query": "tech scene Germany"})
    assert isinstance(result, str)
    assert len(result) > 0
