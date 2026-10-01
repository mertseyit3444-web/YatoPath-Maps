"""Read-only HTTPS gate; the payload budget includes catalog, assets and range probes."""
import argparse
from contextlib import contextmanager
import hashlib
import json
import re
from urllib.parse import urljoin, urlsplit

import requests

CATALOG_LIMIT = 5_000_000
ASSET_KEYS = ("road", "basemap", "basemapDark", "vectorBasemap")


def require_https(url):
    parsed = urlsplit(url)
    if parsed.scheme != "https" or not parsed.hostname or parsed.username is not None or parsed.fragment:
        raise ValueError("A credential-free HTTPS URL is required")


@contextmanager
def https_get(session, url, headers=None):
    for _ in range(6):
        require_https(url)
        response = session.get(url, stream=True, allow_redirects=False, timeout=(15, 60),
                               headers={"Accept-Encoding": "identity", **(headers or {})})
        if response.status_code in (301, 302, 303, 307, 308):
            location = response.headers.get("Location")
            response.close()
            if not location:
                raise ValueError("Redirect has no Location")
            url = urljoin(url, location)
            require_https(url)
            continue
        with response:
            response.raise_for_status()
            if response.headers.get("Content-Encoding", "identity").lower() != "identity":
                raise ValueError("Server must honor Accept-Encoding: identity")
            yield response
        return
    raise ValueError("Too many redirects")


def check_length(response, limit, exact=False):
    declared = response.headers.get("Content-Length")
    if declared is not None:
        try:
            size = int(declared)
        except ValueError as error:
            raise ValueError("Invalid Content-Length") from error
        if size < 0 or size > limit or (exact and size != limit):
            raise ValueError("Unexpected declared download length")


def bounded_blocks(response, limit):
    check_length(response, limit)
    size = 0
    while True:
        # Probe one excess byte instead of consuming an overlong response.
        block = response.raw.read(min(65536, limit - size + 1), decode_content=False)
        if not block:
            break
        size += len(block)
        if size > limit:
            raise ValueError("Unexpected download length")
        yield block


def catalog_assets(catalog):
    if not isinstance(catalog, dict) or type(catalog.get("schemaVersion")) is not int or catalog["schemaVersion"] != 1:
        raise ValueError("Invalid catalog schema")
    packs = catalog.get("packs")
    if not isinstance(packs, list) or not packs:
        raise ValueError("Catalog requires map packs")
    assets = {}
    ids = set()
    for pack in packs:
        if not isinstance(pack, dict) or not isinstance(pack.get("id"), str) or pack["id"] in ids:
            raise ValueError("Invalid or duplicate pack ID")
        ids.add(pack["id"])
        if not pack.get("road") or not (pack.get("vectorBasemap") or (pack.get("basemap") and pack.get("basemapDark"))):
            raise ValueError("Pack requires roads and a complete offline basemap")
        for key in ASSET_KEYS:
            asset = pack.get(key)
            if asset is None:
                continue
            if not isinstance(asset, dict) or type(asset.get("bytes")) is not int or not 8 <= asset["bytes"] <= 2_000_000_000:
                raise ValueError("Invalid asset size")
            if not isinstance(asset.get("url"), str) or not re.fullmatch(r"[a-f0-9]{64}", str(asset.get("sha256", ""))):
                raise ValueError("Invalid asset URL or checksum")
            require_https(asset["url"])
            previous = assets.get(asset["url"])
            if previous and (previous["bytes"], previous["sha256"]) != (asset["bytes"], asset["sha256"]):
                raise ValueError("Conflicting asset metadata for one URL")
            assets[asset["url"]] = asset
    return list(assets.values())


def verify(catalog_url, max_bytes):
    require_https(catalog_url)
    if type(max_bytes) is not int or max_bytes <= 0:
        raise ValueError("A positive payload budget is required")
    with requests.Session() as session:
        with https_get(session, catalog_url) as response:
            if response.status_code != 200:
                raise ValueError("Catalog requires a complete HTTP 200 response")
            raw_catalog = b"".join(bounded_blocks(response, min(CATALOG_LIMIT, max_bytes)))
        assets = catalog_assets(json.loads(raw_catalog))
        payload_bytes = len(raw_catalog) + sum(asset["bytes"] + 8 for asset in assets)
        if payload_bytes > max_bytes:
            raise ValueError("Verification exceeds the explicit payload budget")
        for asset in assets:
            with https_get(session, asset["url"]) as download:
                if download.status_code != 200:
                    raise ValueError("Asset requires a complete HTTP 200 response")
                check_length(download, asset["bytes"], exact=True)
                digest, size, prefix = hashlib.sha256(), 0, b""
                for block in bounded_blocks(download, asset["bytes"]):
                    size += len(block)
                    prefix += block[:max(0, 8 - len(prefix))]
                    digest.update(block)
                if size != asset["bytes"] or digest.hexdigest() != asset["sha256"]:
                    raise ValueError("Download checksum mismatch")
            with https_get(session, asset["url"], {"Range": "bytes=0-7"}) as part:
                if part.status_code != 206 or part.headers.get("Content-Range") != f"bytes 0-7/{size}":
                    raise ValueError("Range/resume requests are not supported correctly")
                check_length(part, 8, exact=True)
                sample = b"".join(bounded_blocks(part, 8))
                if sample != prefix:
                    raise ValueError("Partial response does not match the verified file")
            print(f"Verified {asset['url']}: {size:,} bytes, SHA-256 and HTTP Range")
        return {"catalogBytes": len(raw_catalog), "verifiedAssets": len(assets), "payloadBytes": payload_bytes}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("catalog_url")
    parser.add_argument("--max-bytes", type=int, default=100_000_000)
    args = parser.parse_args()
    print(json.dumps(verify(args.catalog_url, args.max_bytes), indent=2))


if __name__ == "__main__":
    main()
