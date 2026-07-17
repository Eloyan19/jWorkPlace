import pytest

from app.indexing.validation import ValidationError, parse_github_url


@pytest.mark.parametrize("url,expected_name", [
    ("https://github.com/owner/repo", "owner/repo"),
    ("https://github.com/a-b_c/d.e.git", "a-b_c/d.e"),
    ("https://github.com/torvalds/linux/", "torvalds/linux"),
])
def test_valid_urls(url, expected_name):
    ref = parse_github_url(url)
    assert ref.name == expected_name
    assert ref.url.startswith("https://github.com/")
    assert ref.url.endswith(".git")


@pytest.mark.parametrize("url", [
    "",
    "http://github.com/o/r",             # не https
    "file:///etc/passwd",                # локальный путь
    "git@github.com:o/r.git",            # scp-синтаксис
    "ssh://github.com/o/r",              # ssh
    "git://github.com/o/r",              # git-протокол
    "https://github.com/o/r/../../x",    # обход каталогов
    "https://evil.com/o/r",              # чужой хост
    "https://github.com/o",              # нет repo
    "https://github.com/o/r?x=1",        # query
    "https://github.com/o/r#frag",       # fragment
    "https://user:pass@github.com/o/r",  # креды в URL
    "https://gitlab.com/o/r",            # не github
])
def test_rejected_urls(url):
    with pytest.raises(ValidationError):
        parse_github_url(url)
