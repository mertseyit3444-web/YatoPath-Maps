"""Network-free regression coverage for official Geofabrik source acquisition."""

import hashlib
import tempfile
import unittest
from pathlib import Path

import requests

from tools.build_world_regions import (MAX_REDIRECTS, MAX_SOURCE_PAGE_BYTES,
                                       SourceRedirectError, official_stream, source_file)


BASE = "https://download.geofabrik.de/australia-oceania/nauru"
LATEST = BASE + "-latest.osm.pbf"
DATED = BASE + "-260930.osm.pbf"
BODY = b"\x00\x00\x00\x10OSMHeader-test-PBF"


class Response:
    def __init__(self, url, body=BODY, status=200, headers=None):
        self.url = url
        self.body = body
        self.status_code = status
        self.headers = headers if headers is not None else {"Content-Length": str(len(body))}
        self.closed = False
        self.consumed = False

    def raise_for_status(self):
        if self.status_code >= 400:
            raise requests.HTTPError(f"HTTP {self.status_code}", response=self)

    def iter_content(self, chunk_size):
        self.consumed = True
        for offset in range(0, len(self.body), 3):
            yield self.body[offset:offset + 3]

    def close(self):
        self.closed = True

    def __enter__(self):
        return self

    def __exit__(self, *_):
        self.close()


class Session:
    def __init__(self, *responses):
        self.responses = list(responses)
        self.calls = []

    def get(self, url, **kwargs):
        self.calls.append(url)
        assert kwargs["allow_redirects"] is False
        assert kwargs["stream"] is True
        assert kwargs.get("verify", True) is True
        assert kwargs["headers"]["Accept-Encoding"] == "identity"
        response = self.responses.pop(0)
        if isinstance(response, Exception):
            raise response
        assert url == response.url, (url, response.url)
        return response


class SourceDownloadTests(unittest.TestCase):
    def setUp(self):
        self.folder = tempfile.TemporaryDirectory()
        self.addCleanup(self.folder.cleanup)
        self.cache = Path(self.folder.name) / "cache"

    def download(self, session, url=LATEST, limit=1024):
        return source_file(session, "country/source", url, self.cache, limit)

    def test_stalled_latest_uses_official_advertised_dated_source(self):
        session = Session(requests.ReadTimeout("stalled latest alias"),
                          Response(BASE + ".html", body=b'<a href="nauru-260930.osm.pbf">extract</a>'),
                          Response(DATED))
        self.assertEqual(self.download(session).read_bytes(), BODY)
        self.assertEqual(session.calls, [LATEST, BASE + ".html", DATED])

    def test_successful_alias_directory_page_uses_dated_source_without_reading_html(self):
        page = Response(LATEST + "/", headers={"Content-Type": "text/html"})
        session = Session(Response(LATEST, status=301, headers={"Location": LATEST + "/"}),
                          page, Response(BASE + ".html", body=b'<a href="nauru-260930.osm.pbf">'),
                          Response(DATED))
        self.assertEqual(self.download(session).read_bytes(), BODY)
        self.assertTrue(page.closed)
        self.assertFalse(page.consumed)

    def test_html_at_latest_pbf_url_uses_dated_source(self):
        session = Session(Response(LATEST, headers={"Content-Type": "text/html; charset=utf-8"}),
                          Response(BASE + ".html", body=b'<a href="nauru-260930.osm.pbf">'),
                          Response(DATED))
        self.assertEqual(self.download(session).read_bytes(), BODY)

    def test_official_page_and_dated_source_retry_only_timeouts_with_bounded_attempts(self):
        session = Session(requests.ReadTimeout(), requests.ReadTimeout(), requests.ReadTimeout(),
                          Response(BASE + ".html", body=b'<a href="nauru-260930.osm.pbf">'),
                          requests.ReadTimeout(), Response(DATED))
        self.assertEqual(self.download(session).read_bytes(), BODY)
        self.assertEqual(session.calls, [LATEST] + [BASE + ".html"] * 3 + [DATED] * 2)

    def test_official_page_timeout_budget_is_finite(self):
        session = Session(requests.ReadTimeout(), *[requests.ReadTimeout() for _ in range(3)])
        with self.assertRaises(requests.Timeout):
            self.download(session)
        self.assertEqual(len(session.calls), 4)
        self.assertFalse(self.cache.exists())

    def test_dated_url_timeout_does_not_select_another_revision(self):
        session = Session(requests.ReadTimeout())
        with self.assertRaises(requests.Timeout):
            self.download(session, DATED)
        self.assertEqual(session.calls, [DATED])

    def test_dated_html_response_is_rejected_without_promotion(self):
        session = Session(Response(DATED, headers={"Content-Type": "text/html"}))
        with self.assertRaisesRegex(ValueError, "Unexpected PBF"):
            self.download(session, DATED)
        self.assertFalse(self.cache.exists())

    def test_latest_self_loop_uses_newest_advertised_official_dated_extract(self):
        responses = [
            Response(LATEST, status=301, headers={"Location": LATEST + "/"}),
            Response(LATEST + "/", status=301, headers={"Location": LATEST + "/"}),
            Response(BASE + ".html", body=(
                '<a href="nauru-260929.osm.pbf">old</a>'
                '<a href="nauru-260930.osm.pbf">new</a>'
                '<a href="https://evil.invalid/nauru-991231.osm.pbf">unsafe</a>'
                '<a href="nauru-internal-991231.osm.pbf">private</a>').encode()),
            Response(DATED),
        ]
        session = Session(*responses)
        path = self.download(session)
        self.assertEqual(path.read_bytes(), BODY)
        self.assertEqual(len(session.calls), 4)
        self.assertTrue(all(response.closed for response in responses))
        self.assertEqual(path.parent, self.cache)
        self.assertIn("-nauru-260930.osm.pbf", path.name)

    def test_official_redirect_downloads_body_with_one_get(self):
        first = Response(LATEST, status=302, headers={"Location": "nauru-260930.osm.pbf"})
        second = Response(DATED)
        session = Session(first, second)
        self.assertEqual(self.download(session).read_bytes(), BODY)
        self.assertEqual(session.calls, [LATEST, DATED])
        self.assertTrue(first.closed and second.closed)

    def test_untrusted_redirect_never_gets_requested_or_uses_fallback(self):
        for destination in ("http://download.geofabrik.de/a.osm.pbf",
                            "https://evil.invalid/a.osm.pbf",
                            "https://download.geofabrik.de:8443/a.osm.pbf",
                            "https://download.geofabrik.de/internal/a.osm.pbf"):
            with self.subTest(destination=destination):
                response = Response(LATEST, status=302, headers={"Location": destination})
                session = Session(response)
                with self.assertRaisesRegex(ValueError, "Untrusted"):
                    self.download(session)
                self.assertEqual(session.calls, [LATEST])
                self.assertTrue(response.closed)

    def test_untrusted_input_never_gets_requested(self):
        session = Session()
        with self.assertRaises(ValueError):
            self.download(session, "https://evil.invalid/source.osm.pbf")
        self.assertFalse(session.calls)

    def test_redirect_limit_is_bounded_and_closes_every_response(self):
        responses = [Response(BASE + f"-{n}.osm.pbf", status=301,
                              headers={"Location": BASE + f"-{n + 1}.osm.pbf"})
                     for n in range(MAX_REDIRECTS + 1)]
        session = Session(*responses)
        with self.assertRaises(SourceRedirectError):
            official_stream(session, responses[0].url)
        self.assertEqual(len(session.calls), MAX_REDIRECTS + 1)
        self.assertTrue(all(response.closed for response in responses))

    def test_404_alias_uses_official_page(self):
        missing = Response(LATEST, status=404)
        session = Session(missing, Response(BASE + ".html", body=b'<a href="nauru-260930.osm.pbf">'),
                          Response(DATED))
        self.assertEqual(self.download(session).read_bytes(), BODY)
        self.assertTrue(missing.closed)

    def test_auth_and_server_errors_do_not_switch_source(self):
        for status in (401, 403, 429, 500):
            with self.subTest(status=status):
                response = Response(LATEST, status=status)
                session = Session(response)
                with self.assertRaises(requests.HTTPError):
                    self.download(session)
                self.assertEqual(session.calls, [LATEST])
                self.assertTrue(response.closed)

    def test_size_ceiling_checks_headers_before_reading(self):
        response = Response(DATED, headers={"Content-Length": "2048"})
        with self.assertRaisesRegex(ValueError, "byte limit"):
            self.download(Session(response), DATED)
        self.assertFalse(response.consumed)
        self.assertTrue(response.closed)
        self.assertFalse(self.cache.exists())

    def test_size_ceiling_checks_stream_without_length(self):
        response = Response(DATED, body=b"x" * 100, headers={})
        with self.assertRaisesRegex(ValueError, "max-source-bytes"):
            self.download(Session(response), DATED, limit=50)
        self.assertFalse(list(self.cache.iterdir()))
        self.assertTrue(response.closed)

    def test_truncated_body_removes_partial_and_does_not_promote(self):
        response = Response(DATED, headers={"Content-Length": str(len(BODY) + 1)})
        with self.assertRaisesRegex(ValueError, "Truncated"):
            self.download(Session(response), DATED)
        self.assertFalse(list(self.cache.iterdir()))

    def test_empty_pbf_is_rejected(self):
        with self.assertRaisesRegex(ValueError, "Truncated"):
            self.download(Session(Response(DATED, body=b"")), DATED)
        self.assertFalse(list(self.cache.iterdir()))

    def test_malformed_length_is_rejected(self):
        for value in ("-1", "invalid", "1, 2"):
            with self.subTest(value=value):
                with self.assertRaisesRegex(ValueError, "Content-Length"):
                    self.download(Session(Response(DATED, headers={"Content-Length": value})), DATED)

    def test_valid_checksum_cache_reopens_without_redownloading_body(self):
        path = self.download(Session(Response(DATED)), DATED)
        again = Response(DATED)
        self.assertEqual(self.download(Session(again), DATED), path)
        self.assertFalse(again.consumed)
        self.assertEqual(path.with_suffix(".pbf.sha256").read_text().strip(),
                         hashlib.sha256(BODY).hexdigest())

    def test_corrupted_cached_bytes_are_replaced(self):
        path = self.download(Session(Response(DATED)), DATED)
        path.write_bytes(b"x" * len(BODY))
        again = Response(DATED)
        self.download(Session(again), DATED)
        self.assertTrue(again.consumed)
        self.assertEqual(path.read_bytes(), BODY)

    def test_failed_redownload_preserves_previously_cached_bytes(self):
        path = self.download(Session(Response(DATED)), DATED)
        path.with_suffix(".pbf.sha256").write_text("invalid")
        with self.assertRaisesRegex(ValueError, "Truncated"):
            self.download(Session(Response(DATED, body=b"short", headers={"Content-Length": "100"})), DATED)
        self.assertEqual(path.read_bytes(), BODY)
        self.assertFalse(path.with_suffix(".pbf.part").exists())

    def test_smaller_current_limit_does_not_reuse_large_cached_file(self):
        self.download(Session(Response(DATED)), DATED)
        with self.assertRaisesRegex(ValueError, "byte limit"):
            self.download(Session(Response(DATED)), DATED, limit=1)

    def test_equal_size_latest_with_changed_validator_gets_new_cache_path(self):
        first = Response(LATEST, headers={"Content-Length": str(len(BODY)), "ETag": '"a"'})
        second = Response(LATEST, body=b"x" * len(BODY),
                          headers={"Content-Length": str(len(BODY)), "ETag": '"b"'})
        old = self.download(Session(first))
        new = self.download(Session(second))
        self.assertNotEqual(old, new)
        self.assertEqual(old.read_bytes(), BODY)
        self.assertEqual(new.read_bytes(), b"x" * len(BODY))

    def test_latest_without_validators_never_reuses_cache(self):
        self.download(Session(Response(LATEST)))
        second = Response(LATEST, body=b"x" * len(BODY))
        self.assertEqual(self.download(Session(second)).read_bytes(), b"x" * len(BODY))
        self.assertTrue(second.consumed)

    def test_fallback_html_size_is_bounded(self):
        session = Session(Response(LATEST, status=301, headers={"Location": LATEST}),
                          Response(BASE + ".html", headers={"Content-Length": str(MAX_SOURCE_PAGE_BYTES + 1)}))
        with self.assertRaisesRegex(ValueError, "byte limit"):
            self.download(session)

    def test_fallback_without_matching_dated_link_fails_closed(self):
        session = Session(Response(LATEST, status=301, headers={"Location": LATEST}),
                          Response(BASE + ".html", body=b'<a href="../monaco-260930.osm.pbf">wrong region</a>'))
        with self.assertRaisesRegex(ValueError, "no dated PBF"):
            self.download(session)


if __name__ == "__main__":
    unittest.main()
