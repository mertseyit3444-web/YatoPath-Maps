"""Network-free regression coverage for official Geofabrik source acquisition."""

import hashlib
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

import osmium
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

    def test_complete_redirected_source_records_exact_provenance_without_changing_path_api(self):
        proof = {}
        response = Response(DATED, headers={"Content-Length": str(len(BODY)), "ETag": '"revision"'})
        path = source_file(Session(Response(LATEST, status=302, headers={"Location": DATED}), response),
                           "country/source", LATEST, self.cache, 1024, provenance=proof)
        self.assertIsInstance(path, Path)
        self.assertEqual(proof["requestedURL"], LATEST)
        self.assertEqual(proof["resolvedURL"], DATED)
        self.assertEqual(proof["revision"], "nauru-260930.osm.pbf")
        self.assertEqual(proof["sha256"], hashlib.sha256(path.read_bytes()).hexdigest())
        self.assertEqual(proof["bytes"], len(BODY))
        self.assertEqual(proof["etag"], '"revision"')
        self.assertFalse(proof["cacheReused"])
        self.assertTrue(response.closed)

    def test_checksum_verified_cache_reports_reuse_and_observed_source_url(self):
        source_file(Session(Response(DATED)), "country/source", DATED, self.cache, 1024)
        proof, response = {}, Response(DATED)
        path = source_file(Session(response), "country/source", DATED, self.cache, 1024, provenance=proof)
        self.assertTrue(proof["cacheReused"])
        self.assertFalse(response.consumed)
        self.assertEqual(proof["resolvedURL"], DATED)
        self.assertEqual(proof["sha256"], hashlib.sha256(path.read_bytes()).hexdigest())
        self.assertEqual(proof["bytes"], path.stat().st_size)

    def test_direct_latest_validator_cache_key_is_not_fabricated_dated_revision(self):
        proof = {}
        path = source_file(Session(Response(LATEST, headers={"ETag": '"version-one"',
                           "Content-Length": str(len(BODY))})), "country/source", LATEST,
                           self.cache, 1024, provenance=proof)
        self.assertIsNone(proof["revision"])
        self.assertEqual(proof["resolvedURL"], LATEST)
        self.assertEqual(proof["etag"], '"version-one"')
        self.assertNotIn("latest", path.name)

    def test_truncated_source_never_emits_successful_acquisition_provenance(self):
        proof = {}
        response = Response(DATED, headers={"Content-Length": str(len(BODY) + 1)})
        with self.assertRaisesRegex(ValueError, "Truncated PBF"):
            source_file(Session(response), "country/source", DATED, self.cache, 1024, provenance=proof)
        self.assertEqual(proof, {})
        self.assertTrue(response.closed)
        self.assertEqual(list(self.cache.glob("*.part")), [])

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

    def test_official_page_and_dated_source_retry_timeouts_with_bounded_attempts(self):
        session = Session(requests.ReadTimeout(), requests.ReadTimeout(), requests.ReadTimeout(),
                          Response(BASE + ".html", body=b'<a href="nauru-260930.osm.pbf">'),
                          requests.ReadTimeout(), Response(DATED))
        self.assertEqual(self.download(session).read_bytes(), BODY)
        self.assertEqual(session.calls, [LATEST] + [BASE + ".html"] * 3 + [DATED] * 2)

    def test_transient_official_page_and_dated_pbf_share_three_attempt_budget(self):
        page_url = BASE + ".html"
        responses = [Response(page_url, status=503), Response(page_url, status=502),
                     Response(page_url, body=b'<a href="nauru-260930.osm.pbf">'),
                     Response(DATED, status=429, headers={"Retry-After": "1"}),
                     Response(DATED, status=504), Response(DATED)]
        session = Session(requests.ReadTimeout(), *responses)
        with patch("tools.build_world_regions.time.sleep") as sleep:
            path = self.download(session)
        self.assertEqual(path.read_bytes(), BODY)
        self.assertEqual(path.with_suffix(".pbf.sha256").read_text().strip(), hashlib.sha256(BODY).hexdigest())
        self.assertEqual(session.calls, [LATEST] + [page_url] * 3 + [DATED] * 3)
        self.assertEqual([call.args[0] for call in sleep.call_args_list], [0.25, 0.5, 1.0, 0.5])
        self.assertTrue(all(response.closed for response in responses))
        self.assertTrue(all(not response.consumed for response in responses if response.status_code != 200))

    def test_official_page_503_then_dated_503_downloads_genuine_pbf(self):
        fixture = Path(self.folder.name) / "fixture.osm.pbf"
        with osmium.SimpleWriter(str(fixture)) as writer:
            writer.add_node(osmium.osm.mutable.Node(id=1, location=(166.93, -0.52)))
            writer.add_node(osmium.osm.mutable.Node(id=2, location=(166.931, -0.52)))
            writer.add_way(osmium.osm.mutable.Way(id=10, nodes=[1, 2], tags={"highway": "residential"}))
        genuine = fixture.read_bytes()
        page_error, dated_error = Response(BASE + ".html", status=503), Response(DATED, status=503)
        session = Session(requests.ReadTimeout(), page_error,
                          Response(BASE + ".html", body=b'<a href="nauru-260930.osm.pbf">'),
                          dated_error, Response(DATED, body=genuine))
        with patch("tools.build_world_regions.time.sleep"):
            path = self.download(session, limit=len(genuine))
        self.assertEqual(path.read_bytes(), genuine)
        self.assertEqual(path.with_suffix(".pbf.sha256").read_text().strip(), hashlib.sha256(genuine).hexdigest())
        class Ways(osmium.SimpleHandler):
            def __init__(self):
                super().__init__()
                self.ids = []
            def way(self, way):
                self.ids.append(way.id)
        reader = Ways()
        reader.apply_file(str(path))
        self.assertEqual(reader.ids, [10])
        self.assertTrue(page_error.closed and dated_error.closed)
        self.assertFalse(page_error.consumed or dated_error.consumed)

    def test_untrusted_response_url_is_rejected_before_status_retry(self):
        response = Response("https://evil.invalid/source.osm.pbf", status=503)
        session = Mock()
        session.get.return_value = response
        with patch("tools.build_world_regions.time.sleep") as sleep:
            with self.assertRaisesRegex(ValueError, "Unexpected Geofabrik response URL"):
                official_stream(session, DATED, timeout_attempts=3)
        session.get.assert_called_once()
        self.assertTrue(response.closed)
        self.assertFalse(response.consumed)
        sleep.assert_not_called()

    def test_each_retryable_status_then_success_reuses_exact_dated_url(self):
        for status in (429, 502, 503, 504):
            with self.subTest(status=status):
                first, final = Response(DATED, status=status), Response(DATED)
                session = Session(first, final)
                with patch("tools.build_world_regions.time.sleep") as sleep:
                    response = official_stream(session, DATED, timeout_attempts=3)
                self.assertIs(response, final)
                self.assertEqual(session.calls, [DATED, DATED])
                self.assertTrue(first.closed)
                self.assertFalse(first.consumed)
                sleep.assert_called_once_with(0.25)
                response.close()

    def test_timeout_and_status_retries_do_not_have_separate_budgets(self):
        responses = [Response(DATED, status=503), Response(DATED, status=504)]
        session = Session(requests.ReadTimeout(), *responses)
        with patch("tools.build_world_regions.time.sleep") as sleep:
            with self.assertRaises(requests.HTTPError) as caught:
                official_stream(session, DATED, timeout_attempts=3)
        self.assertIs(caught.exception.response, responses[-1])
        self.assertEqual(session.calls, [DATED] * 3)
        self.assertTrue(all(response.closed and not response.consumed for response in responses))
        sleep.assert_called_once_with(0.5)
        self.assertFalse(self.cache.exists())

    def test_exhausted_transient_responses_all_close_without_another_revision(self):
        responses = [Response(DATED, status=503) for _ in range(3)]
        session = Session(*responses)
        with patch("tools.build_world_regions.time.sleep") as sleep:
            with self.assertRaises(requests.HTTPError):
                official_stream(session, DATED, timeout_attempts=3)
        self.assertEqual(session.calls, [DATED] * 3)
        self.assertEqual(sleep.call_count, 2)
        self.assertTrue(all(response.closed and not response.consumed for response in responses))

    def test_non_retryable_http_statuses_do_not_spend_remaining_attempts(self):
        for status in (401, 403, 404, 500):
            with self.subTest(status=status):
                response = Response(DATED, status=status)
                session = Session(response)
                with patch("tools.build_world_regions.time.sleep") as sleep:
                    with self.assertRaises(requests.HTTPError):
                        official_stream(session, DATED, timeout_attempts=3)
                self.assertEqual(session.calls, [DATED])
                self.assertTrue(response.closed)
                self.assertFalse(response.consumed)
                sleep.assert_not_called()

    def test_retry_after_is_short_bounded_seconds_only(self):
        for header, expected in (("0", 0.0), ("2", 2.0), ("3", 0.25), ("999999999999", 0.25),
                                 ("Wed, 21 Oct 2099 07:28:00 GMT", 0.25), ("-1", 0.25),
                                 ("nan", 0.25), ("1.5", 0.25)):
            with self.subTest(header=header):
                first, final = Response(DATED, status=429, headers={"Retry-After": header}), Response(DATED)
                with patch("tools.build_world_regions.time.sleep") as sleep:
                    response = official_stream(Session(first, final), DATED, timeout_attempts=3)
                sleep.assert_called_once_with(expected)
                self.assertTrue(first.closed)
                response.close()

    def test_latest_transient_error_keeps_single_attempt_without_fallback(self):
        response = Response(LATEST, status=503)
        session = Session(response)
        with patch("tools.build_world_regions.time.sleep") as sleep:
            with self.assertRaises(requests.HTTPError):
                self.download(session)
        self.assertEqual(session.calls, [LATEST])
        self.assertTrue(response.closed)
        sleep.assert_not_called()

    def test_invalid_retry_budgets_never_request_network(self):
        session = Session()
        for attempts in (0, -1, 4, True, 1.5):
            with self.subTest(attempts=attempts):
                with self.assertRaises(ValueError):
                    official_stream(session, DATED, timeout_attempts=attempts)
        self.assertFalse(session.calls)

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

    def test_invalid_dated_body_after_fallback_is_not_retried(self):
        html = Response(DATED, headers={"Content-Type": "text/html"})
        session = Session(requests.ReadTimeout(),
                          Response(BASE + ".html", body=b'<a href="nauru-260930.osm.pbf">'), html)
        with patch("tools.build_world_regions.time.sleep") as sleep:
            with self.assertRaisesRegex(ValueError, "Unexpected PBF"):
                self.download(session)
        self.assertEqual(session.calls, [LATEST, BASE + ".html", DATED])
        self.assertTrue(html.closed)
        self.assertFalse(html.consumed)
        self.assertFalse(self.cache.exists())
        sleep.assert_not_called()

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
