import os
from pathlib import Path

import pytest

from src.html_links import PageLink, extract_links, parse_file_url, parse_page_url

HOST = "canvas.example.ac.uk"

# Synthetic body mirroring real Canvas RCE markup (no real verifier tokens).
BODY = f"""
<p><img src="https://{HOST}/courses/1/files/900/preview?verifier=X" alt="banner.jpg"
        data-api-endpoint="https://{HOST}/api/v1/courses/1/files/900" data-api-returntype="File"></p>
<p><a class="inline_disabled" href="https://{HOST}/courses/1/files/101/download?download_frd=1&amp;verifier=X"
      data-api-endpoint="https://{HOST}/api/v1/courses/1/files/101" data-api-returntype="File">Slides</a></p>
<p><a class="instructure_file_link instructure_scribd" href="https://{HOST}/courses/1/files/102?wrap=1"
      data-api-endpoint="https://{HOST}/api/v1/courses/1/files/102">Notes</a></p>
<p><a href="/courses/1/files/103/preview">relative preview</a></p>
<p><a href="/files/104/">bare files link</a></p>
<p><a href="/courses/1/files?preview=105">files tab preview</a></p>
<p><a href="https://{HOST}/courses/1/files/101/download">duplicate of 101</a></p>
<p><iframe src="https://{HOST}/courses/1/files/106/file_preview"></iframe></p>
<p><a href="https://{HOST}/courses/1/pages/week-2-extra?module_item_id=55"
      data-api-endpoint="https://{HOST}/api/v1/courses/1/pages/week-2-extra" data-api-returntype="Page">Extra</a></p>
<p><a href="https://evil.example.com/courses/1/files/666/download">other host</a></p>
<p><a href="https://{HOST}/courses/1/files/folder/Week%201">folder, not a file</a></p>
<p><a href="https://www.youtube.com/watch?v=abc">video</a></p>
"""


def test_extracts_linked_files_in_order_deduped():
    links = extract_links(BODY, HOST)
    assert links.file_ids == [101, 102, 103, 104, 105, 106]


def test_inline_images_are_ignored():
    assert 900 not in extract_links(BODY, HOST).file_ids


def test_other_hosts_are_ignored():
    assert 666 not in extract_links(BODY, HOST).file_ids


def test_page_links():
    assert extract_links(BODY, HOST).pages == [PageLink(1, "week-2-extra")]


def test_empty_and_none():
    assert extract_links(None, HOST).file_ids == []
    assert extract_links("", HOST).pages == []


@pytest.mark.parametrize(
    "url,expected",
    [
        (f"https://{HOST}/api/v1/courses/7/files/42", 42),
        (f"https://{HOST}/api/v1/files/42", 42),
        ("/courses/7/files/42/download?download_frd=1&amp;verifier=z", 42),
        ("/courses/7/files/42", 42),
        ("/courses/7/files/folder/x", None),
        ("/courses/7/filesystem/42", None),
        ("/courses/7/pages/x", None),
    ],
)
def test_parse_file_url(url, expected):
    assert parse_file_url(url, HOST) == expected


def test_parse_page_url_unquotes_slug():
    assert parse_page_url("/courses/7/pages/caf%C3%A9-notes", HOST) == PageLink(7, "café-notes")


# Real page bodies captured by the Phase 0 probe; they contain per-user verifier
# tokens, so they live outside the repo. Run with CANVAS_FIXTURES=<dir>.
REAL = os.environ.get("CANVAS_FIXTURES")


@pytest.mark.skipif(not REAL, reason="CANVAS_FIXTURES not set")
def test_real_ai1_week1_page():
    body = (Path(REAL) / "72717_page_week-1-materials-and-exercises.html").read_text()
    ids = set(extract_links(body, "canvas.bham.ac.uk").file_ids)
    assert {16149317, 16149318, 16149319, 16149320} <= ids
    assert 15714471 not in ids  # ShuoPhoto-2.jpg is an inline <img> only
