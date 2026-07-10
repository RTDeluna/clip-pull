from url_validation import is_supported_url, parse_url_list


def test_accepts_standard_vimeo_link():
    assert is_supported_url("https://vimeo.com/123456789") is True


def test_accepts_vimeo_link_with_hash_param():
    assert is_supported_url("https://vimeo.com/123456789?h=abcdef1234") is True


def test_accepts_vimeo_path_based_private_link():
    assert is_supported_url("https://vimeo.com/123456789/abc123def") is True


def test_accepts_loom_share_link():
    assert is_supported_url("https://www.loom.com/share/abcdef1234567890") is True


def test_accepts_youtube_link():
    assert is_supported_url("https://www.youtube.com/watch?v=abc123") is True


def test_accepts_arbitrary_https_url():
    assert is_supported_url("https://example.com/some/video/path") is True


def test_rejects_url_without_scheme():
    assert is_supported_url("vimeo.com/123456789") is False


def test_rejects_non_http_scheme():
    assert is_supported_url("ftp://example.com/video") is False


def test_rejects_empty_string():
    assert is_supported_url("") is False


def test_rejects_garbage_text():
    assert is_supported_url("not a url at all") is False


def test_parse_url_list_splits_valid_and_invalid_lines():
    text = "https://vimeo.com/111\nnot a url\nhttps://www.loom.com/share/abc\n\n"
    valid, invalid = parse_url_list(text)
    assert valid == ["https://vimeo.com/111", "https://www.loom.com/share/abc"]
    assert invalid == ["not a url"]


def test_parse_url_list_ignores_blank_lines():
    valid, invalid = parse_url_list("\n\n   \n")
    assert valid == []
    assert invalid == []


def test_parse_url_list_strips_whitespace_around_urls():
    valid, invalid = parse_url_list("  https://vimeo.com/333  \n")
    assert valid == ["https://vimeo.com/333"]
    assert invalid == []
