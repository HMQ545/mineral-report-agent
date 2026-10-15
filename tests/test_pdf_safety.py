"""SSRF, scheme, size and path restrictions for the PDF tool."""

from __future__ import annotations

import pytest

from minereport.common import config
from minereport.common.http import UrlNotAllowedError, assert_url_allowed
from minereport.pdf.safety import NotAPdfError, PdfInputError, assert_pdf, resolve_pdf_bytes


def test_private_ip_is_rejected():
    with pytest.raises(UrlNotAllowedError):
        assert_url_allowed("http://127.0.0.1/x.pdf")
    with pytest.raises(UrlNotAllowedError):
        assert_url_allowed("http://10.0.0.5/x.pdf")
    with pytest.raises(UrlNotAllowedError):
        assert_url_allowed("http://169.254.169.254/latest/meta-data")


def test_non_http_scheme_is_rejected():
    with pytest.raises(UrlNotAllowedError):
        assert_url_allowed("ftp://example.com/x.pdf")


def test_host_allowlist_is_enforced():
    with pytest.raises(UrlNotAllowedError):
        assert_url_allowed("https://8.8.8.8/x.pdf", allow_hosts=("minedocs.com",))


def test_fixture_reference_resolves_offline():
    resolved = resolve_pdf_bytes("fixture://pdf/synthetic_jorc_statement.pdf")
    assert resolved["provenance"].value == "fixture"
    assert isinstance(resolved["data"], bytes)


def test_file_url_outside_data_dir_is_rejected():
    with pytest.raises(PdfInputError):
        resolve_pdf_bytes("file:///C:/Windows/win.ini")


def test_file_url_inside_data_dir_is_allowed():
    target = config.FIXTURES_DIR / "pdf" / "synthetic_jorc_statement.pdf"
    resolved = resolve_pdf_bytes(target.as_uri())
    assert isinstance(resolved["data"], bytes)


def test_non_pdf_payload_is_rejected():
    with pytest.raises(NotAPdfError):
        assert_pdf(b"<html>not a pdf</html>")


def test_bare_windows_path_is_rejected():
    # A bare Windows path parses as the (disallowed) "c:" scheme.
    with pytest.raises((PdfInputError, UrlNotAllowedError)):
        resolve_pdf_bytes("C:\\Windows\\win.ini")

