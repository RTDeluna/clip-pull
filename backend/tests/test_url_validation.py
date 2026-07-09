from url_validation import is_vimeo_url, parse_url_list


def test_is_vimeo_url_accepts_standard_link():
    assert is_vimeo_url("https://vimeo.com/123456789") is True


def test_is_vimeo_url_accepts_link_with_hash_param():
    assert is_vimeo_url("https://vimeo.com/123456789?h=abcdef1234") is True


def test_is_vimeo_url_accepts_player_embed_link():
    assert is_vimeo_url("https://player.vimeo.com/video/123456789") is True


def test_is_vimeo_url_rejects_non_vimeo_link():
    assert is_vimeo_url("https://youtube.com/watch?v=abc123") is False


def test_is_vimeo_url_rejects_empty_string():
    assert is_vimeo_url("") is False


def test_is_vimeo_url_rejects_garbage_text():
    assert is_vimeo_url("not a url at all") is False


def test_parse_url_list_splits_valid_and_invalid_lines():
    text = "https://vimeo.com/111\nnot a url\nhttps://vimeo.com/222?h=abc\n\n"
    valid, invalid = parse_url_list(text)
    assert valid == ["https://vimeo.com/111", "https://vimeo.com/222?h=abc"]
    assert invalid == ["not a url"]


def test_parse_url_list_ignores_blank_lines():
    valid, invalid = parse_url_list("\n\n   \n")
    assert valid == []
    assert invalid == []


def test_parse_url_list_strips_whitespace_around_urls():
    valid, invalid = parse_url_list("  https://vimeo.com/333  \n")
    assert valid == ["https://vimeo.com/333"]
    assert invalid == []
