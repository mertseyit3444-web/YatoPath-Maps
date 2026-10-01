"""Prepare full Geofabrik DC/MD extract boundaries without world-overlap subtraction.

Only --fetch-index performs network I/O: one bounded official metadata request.
No PBF download, publication, source-ID migration or user database access occurs.
"""
import argparse
import datetime as dt
import hashlib
import json
import math
import time
from email.utils import parsedate_to_datetime
from pathlib import Path

import requests
from shapely.geometry import shape


INDEX_URL = "https://download.geofabrik.de/index-v1.json"
MAX_INDEX_BYTES = 8_000_000
REGIONS = (
    ("us/district-of-columbia", "US-DC", "us-district-of-columbia", "beta-us-dc", "Washington DC · Geofabrik full extract", "district-of-columbia"),
    ("us/maryland", "US-MD", "us-maryland", "beta-us-md", "Maryland · Geofabrik full extract", "maryland"),
)


def digest(raw):
    return hashlib.sha256(raw).hexdigest()


def strict_json(raw):
    def reject_constant(value):
        raise ValueError("Non-finite JSON number forbidden: " + value)
    return json.loads(raw, parse_constant=reject_constant)


def write_json(path, value):
    path.write_text(json.dumps(value, ensure_ascii=False, separators=(",", ":"), allow_nan=False) + "\n",
                    encoding="utf-8", newline="\n")


def fetch_index(output):
    if output.exists():
        raise ValueError("Use a fresh metadata output directory")
    started = time.monotonic()
    with requests.get(INDEX_URL, stream=True, allow_redirects=False, timeout=(10, 30),
                      headers={"Accept-Encoding": "identity", "User-Agent": "YatoPath-beta-boundary-preparation/1.0"}) as response:
        if response.status_code != 200 or response.url != INDEX_URL:
            raise ValueError("The single official index request must return HTTP 200 without redirect")
        declared = response.headers.get("Content-Length")
        if declared is not None and not 0 < int(declared) <= MAX_INDEX_BYTES:
            raise ValueError("Official index exceeds the metadata byte limit")
        parts, length = [], 0
        for chunk in response.iter_content(64 * 1024):
            length += len(chunk)
            if length > MAX_INDEX_BYTES or time.monotonic() - started > 60:
                raise ValueError("Official index exceeds the bounded download budget")
            parts.append(chunk)
        raw = b"".join(parts)
        if not raw or (declared is not None and not response.headers.get("Content-Encoding") and len(raw) != int(declared)):
            raise ValueError("Official index response is empty or truncated")
        if strict_json(raw).get("type") != "FeatureCollection":
            raise ValueError("Official GeoJSON feature collection required")
        server_date = response.headers.get("Date")
        if not server_date or parsedate_to_datetime(server_date).tzinfo is None:
            raise ValueError("Official response Date header required")
        metadata = {"schemaVersion": 1, "requestedURL": INDEX_URL, "resolvedURL": response.url,
                    "httpStatus": response.status_code, "httpDate": server_date,
                    "lastModified": response.headers.get("Last-Modified"), "etag": response.headers.get("ETag"),
                    "fetchedAtUTC": dt.datetime.now(dt.timezone.utc).isoformat(),
                    "sha256": digest(raw), "bytes": len(raw), "pbfDownloaded": False}
    output.mkdir(parents=True)
    index = output / "index-v1.json"
    index.write_bytes(raw)
    metadata_path = output / "index-http-provenance.json"
    write_json(metadata_path, metadata)
    return index, metadata_path, metadata["sha256"]


def full_geometry(geometry):
    if not isinstance(geometry, dict) or geometry.get("type") not in ("Polygon", "MultiPolygon"):
        raise ValueError("Full polygonal extract boundary required")
    coordinates = geometry.get("coordinates")
    polygons = [coordinates] if geometry["type"] == "Polygon" else coordinates
    if not isinstance(polygons, list) or not polygons:
        raise ValueError("Empty extract geometry")
    for polygon in polygons:
        if not isinstance(polygon, list) or not polygon:
            raise ValueError("Empty extract polygon")
        for ring in polygon:
            if not isinstance(ring, list) or len(ring) < 4 or ring[0] != ring[-1]:
                raise ValueError("Extract rings must already be closed; automatic repair forbidden")
            for coordinate in ring:
                if (not isinstance(coordinate, list) or len(coordinate) != 2
                        or any(type(value) not in (int, float) or not math.isfinite(value) for value in coordinate)
                        or not -80 < coordinate[0] < -74 or not 37 < coordinate[1] < 41):
                    raise ValueError("Non-finite, non-2D or unexpected DC/MD extract coordinate")
    result = shape(geometry)
    if result.is_empty or not result.is_valid or result.area <= 0:
        raise ValueError("Invalid extract geometry; automatic repair forbidden")
    return result


def prepare(index, metadata_path, expected_sha256, output):
    if not 0 < index.stat().st_size <= MAX_INDEX_BYTES:
        raise ValueError("Index metadata input exceeds the intake limit")
    raw = index.read_bytes()
    source_sha = digest(raw)
    if source_sha != expected_sha256:
        raise ValueError("Index input differs from the pinned SHA-256")
    metadata = strict_json(metadata_path.read_bytes())
    if (metadata.get("schemaVersion") != 1 or metadata.get("requestedURL") != INDEX_URL
            or metadata.get("resolvedURL") != INDEX_URL or metadata.get("httpStatus") != 200
            or metadata.get("sha256") != source_sha or metadata.get("bytes") != len(raw)
            or metadata.get("pbfDownloaded") is not False):
        raise ValueError("Complete official HTTP metadata must match the exact index bytes")
    if not metadata.get("httpDate") or parsedate_to_datetime(metadata["httpDate"]).tzinfo is None:
        raise ValueError("Official HTTP Date metadata required")
    if dt.datetime.fromisoformat(metadata.get("fetchedAtUTC", "")).tzinfo is None:
        raise ValueError("Timezone-aware fetch timestamp required")
    document = strict_json(raw)
    if document.get("type") != "FeatureCollection" or not isinstance(document.get("features"), list):
        raise ValueError("Official feature collection required")
    selected, geometries = [], []
    for feature_id, iso, region_id, source_id, name, alias in REGIONS:
        matching = [feature for feature in document["features"] if feature.get("properties", {}).get("id") == feature_id]
        if len(matching) != 1:
            raise ValueError("Exactly one full official extract feature required: " + feature_id)
        feature = matching[0]
        props = feature["properties"]
        pbf = "https://download.geofabrik.de/north-america/us/" + alias + "-latest.osm.pbf"
        if props.get("iso3166-2") != [iso] or props.get("urls", {}).get("pbf") != pbf:
            raise ValueError("Official explicit DC/MD identity and public PBF URL required")
        geometry = full_geometry(feature.get("geometry"))
        geometries.append(geometry)
        selected.append((feature, {"id": region_id, "sourceId": source_id, "name": name,
                                  "pbfURL": pbf, "boundary": region_id + ".geojson", "status": "planned"}, geometry))
    if output.exists():
        raise ValueError("Use a fresh prepared inventory directory")
    output.mkdir(parents=True)
    (output / "boundaries").mkdir()
    regions, proofs = [], []
    for feature, region, geometry in selected:
        path = output / "boundaries" / region["boundary"]
        # Keep the source coordinate/ring structure itself, including every island
        # and hole. Do not normalize, union, simplify, subtract DC or buffer(0).
        boundary = {"type": "Feature", "properties": {"id": region["id"], "name": region["name"],
                    "geofabrikFeatureId": feature["properties"]["id"], "coverageKind": "complete-source-extract-clip"},
                    "geometry": feature["geometry"]}
        write_json(path, boundary)
        if full_geometry(strict_json(path.read_bytes())["geometry"]).symmetric_difference(geometry).area != 0:
            raise ValueError("Output geometry changed from the complete official source clip")
        region["boundarySHA256"] = digest(path.read_bytes())
        regions.append(region)
        proofs.append({"id": region["id"], "sourceId": region["sourceId"], "featureId": feature["properties"]["id"],
                       "boundarySHA256": region["boundarySHA256"], "geometryType": geometry.geom_type,
                       "bounds": list(geometry.bounds), "areaDegreesSquared": geometry.area,
                       "polygonCount": len(geometry.geoms) if geometry.geom_type == "MultiPolygon" else 1,
                       "holeCount": sum(len(part.interiors) for part in geometry.geoms) if geometry.geom_type == "MultiPolygon" else len(geometry.interiors),
                       "sourceClipDifferenceArea": 0.0, "geometryRepaired": False, "worldOverlapSubtracted": False,
                       "administrativeBoundaryVerified": False, "roadPackVerified": False, "vectorMapVerified": False})
    write_json(output / "inventory.json", {"schemaVersion": 1, "regions": regions})
    write_json(output / "boundary-provenance.json", {"schemaVersion": 1, "status": "boundary-prepared-not-map-accepted",
               "indexHTTP": metadata, "indexMetadataSHA256": digest(metadata_path.read_bytes()),
               "license": "ODbL-1.0", "attribution": "© OpenStreetMap contributors",
               "licenseURL": "https://www.openstreetmap.org/copyright", "regions": proofs,
               "dcMarylandFullClipOverlapAreaDegreesSquared": geometries[0].intersection(geometries[1]).area,
               "pbfDownloaded": False, "hostVerified": False, "userDataAccessed": False})
    return output / "inventory.json"


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--fetch-index", action="store_true", help="Fetch one bounded official index into a fresh output directory")
    parser.add_argument("--index", type=Path)
    parser.add_argument("--index-metadata", type=Path)
    parser.add_argument("--expected-sha256")
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    if args.fetch_index:
        if any((args.index, args.index_metadata, args.expected_sha256)):
            parser.error("Do not mix fetch mode and existing pinned inputs")
        index, metadata, expected = fetch_index(args.output)
        path = prepare(index, metadata, expected, args.output / "prepared")
    else:
        if not all((args.index, args.index_metadata, args.expected_sha256)):
            parser.error("Existing index, HTTP metadata and exact expected SHA-256 required")
        path = prepare(args.index, args.index_metadata, args.expected_sha256, args.output)
    print("Prepared full DC/MD source-clip inventory only:", path)


if __name__ == "__main__":
    main()
