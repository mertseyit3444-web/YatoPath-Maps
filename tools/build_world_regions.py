#!/usr/bin/env python3
"""Build manifest-selected road regions from Geofabrik's current OSM extracts.

The output directory is one immutable release. Use a new directory for each OSM
refresh; the app's region IDs remain stable while file hashes change.
"""

import argparse
import hashlib
from html.parser import HTMLParser
import json
import re
import subprocess
import sys
import time
from pathlib import Path
from urllib.parse import urljoin, urlparse

import requests


INDEX_URL = "https://download.geofabrik.de/index-v1-nogeom.json"
MAX_REDIRECTS = 5
MAX_SOURCE_PAGE_BYTES = 1_048_576
TRANSIENT_HTTP_STATUSES = frozenset((429, 502, 503, 504))
MAX_SOURCE_RETRY_DELAY = 2.0


def official_url(url):
    parsed = urlparse(url)
    return (parsed.scheme == "https" and parsed.hostname == "download.geofabrik.de"
            and parsed.username is None and parsed.password is None
            and parsed.port in (None, 443)
            and not parsed.query and not parsed.fragment and "internal" not in parsed.path)


def hash_file(path):
    with path.open("rb") as handle:
        return hashlib.file_digest(handle, "sha256").hexdigest()


class SourceRedirectError(ValueError):
    """An official source alias loops or exceeds the redirect budget."""


def official_stream(session, url, timeout_attempts=1):
    """Validate every hop; share a bounded timeout/transient-status attempt budget."""
    if type(timeout_attempts) is not int or not 1 <= timeout_attempts <= 3:
        raise ValueError("Official source attempts must be 1...3")
    visited = set()
    for _ in range(MAX_REDIRECTS + 1):
        if not official_url(url):
            raise ValueError("Untrusted Geofabrik URL or redirect")
        if url in visited:
            raise SourceRedirectError("Geofabrik redirect loop")
        visited.add(url)
        for attempt in range(timeout_attempts):
            try:
                response = session.get(url, allow_redirects=False, stream=True,
                                       headers={"Accept-Encoding": "identity"}, timeout=(20, 60))
            except requests.Timeout:
                if attempt + 1 == timeout_attempts:
                    raise
                continue
            if not official_url(response.url):
                response.close()
                raise ValueError("Unexpected Geofabrik response URL")
            if response.status_code in TRANSIENT_HTTP_STATUSES:
                if attempt + 1 == timeout_attempts:
                    try:
                        response.raise_for_status()
                    finally:
                        response.close()
                # Discard the error body before another request. Only a short
                # integer Retry-After is honored; dates/large/malformed values
                # cannot create an unbounded wait or alter the source revision.
                retry_after = response.headers.get("Retry-After", "")
                delay = min(MAX_SOURCE_RETRY_DELAY, 0.25 * (2 ** attempt))
                if re.fullmatch(r"[0-9]{1,2}", retry_after) and int(retry_after) <= MAX_SOURCE_RETRY_DELAY:
                    delay = float(retry_after)
                response.close()
                time.sleep(delay)
                continue
            break
        if not official_url(response.url):
            response.close()
            raise ValueError("Unexpected Geofabrik response URL")
        if response.status_code in (301, 302, 303, 307, 308):
            location = response.headers.get("Location")
            response.close()
            if not location:
                raise ValueError("Geofabrik redirect has no Location")
            url = urljoin(url, location)
            continue
        try:
            response.raise_for_status()
        except Exception:
            response.close()
            raise
        return response
    raise SourceRedirectError("Geofabrik redirect limit exceeded")


def content_size(response, max_bytes):
    raw = response.headers.get("Content-Length")
    if raw is None:
        return None
    if not re.fullmatch(r"[0-9]+", raw):
        raise ValueError("Invalid Geofabrik Content-Length")
    size = int(raw)
    if size > max_bytes:
        raise ValueError(f"Geofabrik source exceeds byte limit ({size} bytes)")
    return size


class SourceLinks(HTMLParser):
    def __init__(self):
        super().__init__()
        self.links = []

    def handle_starttag(self, tag, attrs):
        if tag == "a":
            self.links.extend(value for key, value in attrs if key == "href" and value)


def dated_source_url(session, source_url):
    """Use only a dated extract advertised by the corresponding official page.

    Some official -latest aliases redirect to themselves with an added slash.
    GET shares that server failure with HEAD; raising the redirect limit cannot
    fix it. The public region page lists immutable, dated PBF alternatives.
    """
    if not official_url(source_url) or not source_url.endswith("-latest.osm.pbf"):
        raise ValueError("Dated fallback requires an official latest PBF URL")
    prefix = source_url[:-len("-latest.osm.pbf")]
    page_url = prefix + ".html"
    with official_stream(session, page_url, timeout_attempts=3) as page:
        content_size(page, MAX_SOURCE_PAGE_BYTES)
        data = bytearray()
        for chunk in page.iter_content(chunk_size=65_536):
            data.extend(chunk)
            if len(data) > MAX_SOURCE_PAGE_BYTES:
                raise ValueError("Geofabrik source page exceeds byte limit")
    links = SourceLinks()
    links.feed(data.decode("utf-8"))
    pattern = re.compile(re.escape(prefix) + r"-([0-9]{6})\.osm\.pbf")
    candidates = []
    for link in links.links:
        candidate = urljoin(page_url, link)
        match = pattern.fullmatch(candidate)
        if match and official_url(candidate):
            candidates.append((match.group(1), candidate))
    if not candidates:
        raise ValueError("Official Geofabrik page advertises no dated PBF")
    return max(candidates)[1]


def source_file(session, source_id, source_url, cache, max_bytes):
    if max_bytes <= 0:
        raise ValueError("--max-source-bytes must be positive")
    if not official_url(source_url) or not source_url.endswith(".osm.pbf"):
        raise ValueError(f"Untrusted PBF URL for {source_id}")
    used_dated_fallback = False
    try:
        stream = official_stream(session, source_url)
    except (SourceRedirectError, requests.Timeout):
        if not source_url.endswith("-latest.osm.pbf"):
            raise
        stream = official_stream(session, dated_source_url(session, source_url), timeout_attempts=3)
        used_dated_fallback = True
    except requests.HTTPError as error:
        if (error.response is None or error.response.status_code != 404
                or not source_url.endswith("-latest.osm.pbf")):
            raise
        stream = official_stream(session, dated_source_url(session, source_url), timeout_attempts=3)
        used_dated_fallback = True
    # Some latest aliases return a successful HTML directory page after adding
    # a slash. Treat only that trusted alias failure as a dated-source fallback.
    content_type = stream.headers.get("Content-Type", "").lower()
    if (not used_dated_fallback and stream.status_code == 200 and source_url.endswith("-latest.osm.pbf")
            and (not stream.url.endswith(".osm.pbf") or "text/html" in content_type)):
        stream.close()
        stream = official_stream(session, dated_source_url(session, source_url), timeout_attempts=3)
    with stream:
        if (stream.status_code != 200 or not stream.url.endswith(".osm.pbf")
                or "text/html" in stream.headers.get("Content-Type", "").lower()):
            raise ValueError(f"Unexpected PBF response for {source_id}")
        size = content_size(stream, max_bytes)
        revision = Path(urlparse(stream.url).path).name
        # A server may serve the alias directly. Include its version validators
        # so equal-size newer extracts cannot accidentally reuse an older file.
        if revision.endswith("-latest.osm.pbf"):
            validators = (stream.headers.get("ETag"), stream.headers.get("Last-Modified"))
            if not any(validators):
                revision = None  # No reusable cache identity for this alias.
            else:
                version = hashlib.sha256(repr(validators).encode()).hexdigest()[:16]
                revision = revision.replace("-latest", "-" + version)
        return save_source(stream, source_id, revision, cache, max_bytes, size)


def save_source(stream, source_id, revision, cache, max_bytes, size):
    # The official index contains IDs such as france/ile-de-france. Never
    # interpret an upstream identifier as a filesystem path.
    cache_key = hashlib.sha256(source_id.encode("utf-8")).hexdigest()[:20]
    target = cache / f"{cache_key}-{revision or 'unversioned.osm.pbf'}"
    digest_file = target.with_suffix(target.suffix + ".sha256")
    if (revision and target.is_file() and digest_file.is_file()
            and target.stat().st_size <= max_bytes
            and (size is None or target.stat().st_size == size)
            and digest_file.read_text().strip() == hash_file(target)):
        return target
    cache.mkdir(parents=True, exist_ok=True)
    partial = target.with_suffix(target.suffix + ".part")
    try:
        received = 0
        digest = hashlib.sha256()
        with partial.open("wb") as handle:
            for chunk in stream.iter_content(chunk_size=1_048_576):
                if not chunk:
                    continue
                received += len(chunk)
                if received > max_bytes:
                    raise ValueError(f"Source {source_id} exceeded --max-source-bytes")
                handle.write(chunk)
                digest.update(chunk)
        if not received or (size is not None and received != size):
            raise ValueError(f"Truncated PBF for {source_id}: {received} of {size} bytes")
        partial.replace(target)
        digest_file.write_text(digest.hexdigest() + "\n", encoding="ascii")
        return target
    finally:
        partial.unlink(missing_ok=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--output-directory", type=Path, required=True)
    parser.add_argument("--source-cache", type=Path, required=True)
    parser.add_argument("--max-source-bytes", type=int, default=2_000_000_000)
    args = parser.parse_args()
    manifest = json.loads(args.manifest.read_text(encoding="utf-8"))
    regions = manifest.get("regions", [])
    if manifest.get("schemaVersion") != 1 or not regions:
        parser.error("manifest must contain schemaVersion 1 and nonempty regions")
    ids = [region.get("id") for region in regions]
    if len(ids) != len(set(ids)) or any(not isinstance(id, str) or
                                       not re.fullmatch(r"[a-z0-9]+(?:-[a-z0-9]+)*", id)
                                       for id in ids):
        parser.error("region IDs must be unique lowercase ASCII slugs")
    with requests.Session() as session:
        index_response = session.get(INDEX_URL, timeout=(20, 60))
        index_response.raise_for_status()
        index = {feature["properties"]["id"]: feature["properties"]
                 for feature in index_response.json()["features"]}
        args.output_directory.mkdir(parents=True, exist_ok=True)
        sources = {}
        for region in regions:
            source_id = region.get("geofabrikId")
            if source_id not in index or not index[source_id].get("urls", {}).get("pbf"):
                parser.error(f"Geofabrik region not found: {source_id}")
            name = region.get("name")
            if not isinstance(name, str) or not name.strip():
                parser.error(f"missing name for {region['id']}")
            boundary = region.get("boundary")
            boundary_path = (args.manifest.parent / boundary).resolve() if boundary else None
            if boundary_path and not boundary_path.is_file():
                parser.error(f"missing boundary for {region['id']}: {boundary_path}")
            output = args.output_directory / f"{region['id']}.sqlite"
            if output.exists() or output.with_suffix(".json").exists():
                parser.error(f"output already exists; use a new release directory: {output}")
            if source_id not in sources:
                sources[source_id] = source_file(session, source_id, index[source_id]["urls"]["pbf"],
                                                args.source_cache, args.max_source_bytes)
            source = sources[source_id]
            command = [sys.executable, str(Path(__file__).with_name("build_region_pack.py")),
                       "--pbf", str(source), "--region-id", region["id"],
                       "--name", name, "--output", str(output)]
            if boundary_path:
                command += ["--boundary", str(boundary_path)]
            subprocess.run(command, check=True)
            print(f"Built {region['id']} from {source.name}", flush=True)


if __name__ == "__main__":
    main()
