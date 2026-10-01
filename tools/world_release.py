"""Build and verify immutable world map releases from the audited inventory.

Only tools and public source boundaries are copied to the public map repository.
No app source, user database or account credential is part of the release.
"""
import argparse
import datetime as dt
import hashlib
import json
import math
import os
import re
import shutil
import sqlite3
import subprocess
import sys
import time
from contextlib import closing
from pathlib import Path
from types import SimpleNamespace

import requests
from shapely.geometry import box, mapping

try:
    from tools.build_region_pack import build, load_boundary
    from tools.build_world_regions import source_file
    from tools.verify_map_host import catalog_assets, verify
except ModuleNotFoundError:
    from build_region_pack import build, load_boundary
    from build_world_regions import source_file
    from verify_map_host import catalog_assets, verify


ROOT = Path(__file__).resolve().parent
REPOSITORY = None
BASEMAP = "https://build.protomaps.com/20260929.pmtiles"
KNOWN_IDS = {"monaco": "mc-monaco", "bhutan": "bt-bhutan", "bermuda": "bm-bermuda",
             "nauru": "nr-nauru", "seychelles": "sc-seychelles"}


def destination_repository(explicit=None):
    value = explicit or os.environ.get("YATOPATH_MAP_REPOSITORY") or os.environ.get("GITHUB_REPOSITORY")
    if not value or not re.fullmatch(r"[A-Za-z0-9](?:[A-Za-z0-9-]{0,38})/YatoPath-Maps", value):
        raise ValueError("Configure an explicit OWNER/YatoPath-Maps destination repository")
    return value


def sha256(path):
    with path.open("rb") as file:
        return hashlib.file_digest(file, "sha256").hexdigest()


def write_json(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(value, ensure_ascii=False, separators=(",", ":")) + "\n", encoding="utf-8", newline="\n")
    temporary.replace(path)


def checked_inventory(path):
    document = json.loads(path.read_text(encoding="utf-8"))
    if document.get("schemaVersion") != 1 or not document.get("regions"):
        raise ValueError("Nonempty audited inventory required")
    ids = set()
    for region in document["regions"]:
        if not re.fullmatch(r"[a-z0-9]+(?:-[a-z0-9]+)*", region["id"]) or region["id"] in ids:
            raise ValueError("Invalid or duplicate inventory ID")
        ids.add(region["id"])
        boundary = (path.parent / "boundaries" / region["boundary"]).resolve()
        if boundary.parent != (path.parent / "boundaries").resolve():
            raise ValueError("Boundary path escapes the inventory")
        if sha256(boundary) != region["boundarySHA256"]:
            raise ValueError("Boundary does not match audited SHA-256")
        load_boundary(boundary)
    return document


def source_groups(document):
    groups = {}
    for region in document["regions"]:
        groups.setdefault(region["sourceId"], []).append(region)
    return [(key, groups[key]) for key in sorted(groups)]


def inventory_fingerprint(path):
    # Git normalizes line endings between Windows and Linux. Boundary hashes
    # remain exact byte hashes; this manifest identity is canonical JSON.
    document = json.loads(path.read_text(encoding="utf-8"))
    return hashlib.sha256(json.dumps(document, sort_keys=True, separators=(",", ":"),
                                    ensure_ascii=False).encode("utf-8")).hexdigest()


def source_key(source_id):
    return hashlib.sha256(source_id.encode()).hexdigest()[:20]


def split_boundary(boundary):
    west, south, east, north = boundary.bounds
    if max(east-west, north-south) < 0.002:
        raise ValueError("A dense region cannot be split further safely")
    # Two children avoid exponential empty cells on islands and narrow coasts.
    if (east-west) * max(0.1, math.cos(math.radians((south+north)/2))) >= north-south:
        middle = (west+east)/2
        extents = [box(west, south, middle, north), box(middle, south, east, north)]
    else:
        middle = (south+north)/2
        extents = [box(west, south, east, middle), box(west, middle, east, north)]
    parts = [boundary.intersection(extent) for extent in extents]
    if any(part.is_empty or not part.is_valid for part in parts):
        raise ValueError("Split produced an empty or invalid boundary")
    return parts


def vector_estimate(binary, boundary_file, maxzoom):
    result = subprocess.run([str(binary), "extract", BASEMAP, "unused.pmtiles",
                             "--region=" + str(boundary_file), "--maxzoom=" + str(maxzoom), "--dry-run"],
                            check=True, capture_output=True, text=True)
    match = re.search(r"archive size of ([0-9.]+)\s*([kMGT]?B)", result.stdout + result.stderr)
    if not match:
        raise ValueError("PMTiles dry-run did not report a size; extraction was not started")
    factor = {"B": 1, "kB": 1000, "MB": 1000000, "GB": 1000000000, "TB": 1000000000000}[match[2]]
    return math.ceil(float(match[1]) * factor * 1.06)


def pack_region(source, region_id, name, boundary, folder, binary, asset_limit, maxzoom,
                source_state=None, suffix="", depth=0):
    if depth > 16:
        raise ValueError("Region split exceeded the depth limit")
    candidate_id = region_id + ("-p" + suffix if suffix else "")
    if len(candidate_id) > 60:
        raise ValueError("Split pack ID exceeds the mobile limit")
    candidate = folder / candidate_id
    candidate.mkdir(exist_ok=True, parents=True)
    boundary_file = candidate / "boundary.geojson"
    write_json(boundary_file, {"type": "Feature", "properties": {}, "geometry": mapping(boundary)})
    road = candidate / (candidate_id + ".sqlite")
    metadata = road.with_suffix(".json")
    if not road.exists():
        build(SimpleNamespace(pbf=None if source_state else source,
                              from_db=source if source_state else None, source_state=source_state,
                              region_id=candidate_id, name=name, boundary=boundary_file, output=road, stable_clips=True))
    info = json.loads(metadata.read_text(encoding="utf-8"))
    if sha256(road) != info["sha256"]:
        raise ValueError("Resumed road package checksum mismatch")
    if info.get("idScheme") != "osm-node-pair-geometry-v2":
        raise ValueError("Resumed package uses another ID scheme; use a fresh production directory")
    estimate = vector_estimate(binary, boundary_file, maxzoom)
    if info["bytes"] > asset_limit or estimate > asset_limit:
        result = []
        for index, part in enumerate(split_boundary(boundary)):
            try:
                child = pack_region(road, region_id, name, part, folder, binary, asset_limit,
                                    maxzoom, source_state=candidate_id, suffix=suffix + str(index), depth=depth+1)
            except SystemExit as error:
                if str(error).startswith("No runnable roads found"):
                    continue
                raise
            result.extend(child)
        if not result:
            raise ValueError("Splitting discarded all roads")
        return result
    vector = candidate / (candidate_id + "-basemap.pmtiles")
    if not vector.exists():
        subprocess.run([sys.executable, str(ROOT / "extract_vector_basemap.py"),
                        "--pmtiles-bin", str(binary), "--source", BASEMAP, "--boundary", str(boundary_file),
                        "--maxzoom", str(maxzoom), "--output", str(vector)], check=True)
    if vector.stat().st_size > asset_limit:
        raise ValueError("Actual PMTiles size exceeded the dry-run bound; split before publishing")
    with closing(sqlite3.connect(road)) as db:
        if db.execute("PRAGMA quick_check").fetchone()[0] != "ok":
            raise ValueError("Road database failed quick_check")
    return [(road, vector)]


def run_gh(*arguments, json_result=False):
    result = subprocess.run(["gh", *arguments], check=True, capture_output=True, text=True, encoding="utf-8")
    return json.loads(result.stdout) if json_result else result.stdout


def release_tag(source_id, edition):
    if not re.fullmatch(r"[a-z0-9-]{1,30}", edition):
        raise ValueError("Edition must be an ASCII release slug")
    return edition + "-" + source_key(source_id)


def build_source(args):
    document = checked_inventory(args.inventory)
    groups = dict(source_groups(document))
    regions = groups[args.source]
    tag = release_tag(args.source, args.edition)
    folder = args.work / source_key(args.source)
    folder.mkdir(parents=True, exist_ok=True)
    # Reuse only a fully verified, immutable receipt from this exact plan.
    existing = (subprocess.run(["gh", "release", "view", tag, "--repo", REPOSITORY,
                               "--json", "isPrerelease"], capture_output=True, text=True)
                if args.publish else None)
    if existing is not None and existing.returncode == 0:
        run_gh("release", "download", tag, "--repo", REPOSITORY, "--pattern", "receipt.json", "--dir", str(folder), "--clobber")
        receipt = json.loads((folder / "receipt.json").read_text(encoding="utf-8"))
        if (receipt.get("inventorySHA256") != inventory_fingerprint(args.inventory)
                or receipt.get("verified") is not True or receipt.get("sourceId") != args.source):
            raise ValueError("Existing release is incomplete or uses another audited inventory")
        return receipt
    urls = {region["pbfURL"] for region in regions}
    if len(urls) != 1:
        raise ValueError("Dateline parts must use one source snapshot")
    if args.source_pbf:
        source = args.source_pbf
        if not args.source_sha256 or sha256(source) != args.source_sha256:
            raise ValueError("A pinned source PBF must match its explicit SHA-256")
    else:
        with requests.Session() as session:
            source = source_file(session, args.source, urls.pop(), args.work / "source-cache", args.max_source_bytes)
    if shutil.disk_usage(args.work).free < max(source.stat().st_size * 3, 3_000_000_000):
        raise ValueError("Insufficient free space for road extraction; source is retained for retry")
    selected = []
    for region in regions:
        id = KNOWN_IDS.get(args.source, region["id"]) if len(regions) == 1 else region["id"]
        boundary = load_boundary(args.inventory.parent / "boundaries" / region["boundary"])
        try:
            selected.extend(pack_region(source, id, region["name"], boundary, folder / "build",
                                        args.pmtiles_bin.resolve(), args.asset_limit, args.maxzoom))
        except SystemExit as error:
            if str(error).startswith("No runnable roads found"):
                continue
            raise
    if not selected:
        raise ValueError("No runnable roads in this source; disposition requires review")
    if len(selected) * 2 + 3 > 1000:
        raise ValueError("Too many assets for one GitHub release")
    files = folder / "packages"
    files.mkdir(exist_ok=True)
    for road, vector in selected:
        for asset in (road, road.with_suffix(".json"), vector):
            destination = files / (road.stem + ".pmtiles" if asset == vector else asset.name)
            if destination.exists():
                if sha256(destination) != sha256(asset):
                    raise ValueError("Conflicting immutable package")
            else:
                # Hard links avoid making a third multi-GB staging copy.
                destination.hardlink_to(asset)
    base_url = "https://github.com/" + REPOSITORY + "/releases/download/" + tag
    catalog_file = folder / "catalog.json"
    subprocess.run([sys.executable, str(ROOT / "make_road_catalog.py"), "--directory", str(files),
                    "--base-url", base_url, "--output", str(catalog_file)], check=True)
    catalog = json.loads(catalog_file.read_text(encoding="utf-8"))
    catalog_assets(catalog)
    receipt = {"schemaVersion": 1, "sourceId": args.source, "tag": tag,
               "inventorySHA256": inventory_fingerprint(args.inventory), "sourceSHA256": sha256(source),
               "catalogSHA256": sha256(catalog_file), "basemapSource": BASEMAP,
               "packCount": len(catalog["packs"]), "verified": False}
    if args.publish:
        notes = folder / "notes.md"
        notes.write_text("YatoPath offline road and vector map packages.\n\n"
                         "Roads and map: © OpenStreetMap contributors, ODbL 1.0.\n"
                         "https://www.openstreetmap.org/copyright\n\n"
                         "Basemap processing: Protomaps; additional Natural Earth public-domain data.\n"
                         "https://docs.protomaps.com/basemaps/downloads\n", encoding="utf-8")
        run_gh("release", "create", tag, "--repo", REPOSITORY, "--prerelease", "--latest=false",
               "--title", "Offline maps: " + regions[0]["name"], "--notes-file", str(notes),
               str(catalog_file), *[str(files / Path(asset["url"]).name) for asset in catalog_assets(catalog)])
        budget = catalog_file.stat().st_size + sum(asset["bytes"] + 8 for asset in catalog_assets(catalog))
        receipt["hostVerification"] = verify(base_url + "/catalog.json", budget)
        receipt["verified"] = True
        write_json(folder / "receipt.json", receipt)
        run_gh("release", "upload", tag, "--repo", REPOSITORY, str(folder / "receipt.json"))
        # Keep regional releases out of /latest. Only the catalog coordinator
        # may advance the public app catalog after all selected assets pass.
    write_json(folder / "receipt.json", receipt)
    print(json.dumps(receipt), flush=True)
    return receipt


def matrix(args):
    document = checked_inventory(args.inventory)
    groups = source_groups(document)
    selected = groups[args.shard * args.batch_size:(args.shard+1) * args.batch_size]
    if args.sources:
        requested = set(args.sources.split(","))
        selected = [(id, regions) for id, regions in groups if id in requested]
        if requested != {id for id, _ in selected}:
            raise ValueError("Requested source is not in the audited inventory")
    if not 1 <= len(selected) <= 256:
        raise ValueError("Matrix must contain 1...256 sources")
    print(json.dumps({"include": [{"source": id} for id, _ in selected]}))


def collect(args):
    document = checked_inventory(args.inventory)
    pages = run_gh("api", "--paginate", "--slurp", "repos/" + REPOSITORY + "/releases", json_result=True)
    releases = [release for page in pages for release in page]
    by_tag = {release["tag_name"]: release for release in releases}
    packs, missing, receipts = {}, [], []
    for source, _ in source_groups(document):
        tag = release_tag(source, args.edition)
        release = by_tag.get(tag)
        assets = {asset["name"]: asset for asset in release["assets"]} if release else {}
        if "receipt.json" not in assets or "catalog.json" not in assets:
            missing.append(source)
            continue
        with requests.Session() as session:
            receipt_response = session.get(assets["receipt.json"]["browser_download_url"], timeout=30)
            receipt_response.raise_for_status()
            receipt = receipt_response.json()
            if receipt.get("verified") is not True or receipt.get("inventorySHA256") != inventory_fingerprint(args.inventory):
                raise ValueError("Unverified or mismatched regional release")
            catalog_response = session.get(assets["catalog.json"]["browser_download_url"], timeout=30)
            catalog_response.raise_for_status()
            catalog = catalog_response.json()
            if hashlib.sha256(catalog_response.content).hexdigest() != receipt.get("catalogSHA256"):
                raise ValueError("Regional catalog changed after its live verification")
        catalog_assets(catalog)
        for pack in catalog["packs"]:
            if pack["id"] in packs:
                raise ValueError("Duplicate downloadable region ID")
            packs[pack["id"]] = pack
        receipts.append(receipt)
    # Always retain the currently published Monaco pack, including its exact ID
    # and hashes; the new catalogue must not implicitly replace installed data.
    current_url = "https://github.com/" + REPOSITORY + "/releases/latest/download/catalog.json"
    current_response = requests.get(current_url + "?yatopath_catalog=" + str(time.time_ns()), timeout=30)
    current_response.raise_for_status()
    for pack in current_response.json()["packs"]:
        if pack["id"] == "mc-monaco":
            packs[pack["id"]] = pack
        else:
            packs.setdefault(pack["id"], pack)
    report = {"sourceCount": len(source_groups(document)), "verifiedSourceCount": len(receipts),
              "missingSources": missing, "packCount": len(packs), "complete": not missing}
    write_json(args.work / "world-status.json", report)
    catalog = {"schemaVersion": 1, "generatedAt": dt.datetime.now(dt.timezone.utc).isoformat(),
               "packs": sorted(packs.values(), key=lambda pack: pack["id"])}
    catalog_file = args.work / "catalog.json"
    write_json(catalog_file, catalog)
    catalog_assets(catalog)
    if catalog_file.stat().st_size > 5_000_000 or len(packs) > 10000:
        raise ValueError("Catalog exceeds the verified mobile limits")
    if args.publish:
        tag = args.edition + "-catalog-" + str(args.catalog_revision)
        run_gh("release", "create", tag, "--repo", REPOSITORY, "--prerelease", "--latest=false",
               "--title", "YatoPath offline map catalog", "--notes", "Verified regional map downloads; coverage status is in world-status.json.",
               str(catalog_file), str(args.work / "world-status.json"))
        live = requests.get("https://github.com/" + REPOSITORY + "/releases/download/" + tag + "/catalog.json", timeout=30)
        live.raise_for_status()
        if live.content != catalog_file.read_bytes():
            raise ValueError("Published catalog is not identical to the candidate")
        run_gh("release", "edit", tag, "--repo", REPOSITORY, "--prerelease=false", "--latest")
        latest = requests.get(current_url + "?yatopath_catalog=" + str(time.time_ns()), timeout=30)
        latest.raise_for_status()
        if latest.content != catalog_file.read_bytes():
            raise ValueError("Latest catalog did not advance to the verified candidate")
    print(json.dumps(report), flush=True)


def main():
    global REPOSITORY
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("matrix", "build", "collect"))
    parser.add_argument("--inventory", type=Path, required=True)
    parser.add_argument("--work", type=Path, default=Path("outputs/world"))
    parser.add_argument("--source")
    parser.add_argument("--source-pbf", type=Path)
    parser.add_argument("--source-sha256")
    parser.add_argument("--sources")
    parser.add_argument("--edition", default="world-20261001-v2")
    parser.add_argument("--shard", type=int, default=0)
    parser.add_argument("--batch-size", type=int, default=180)
    parser.add_argument("--pmtiles-bin", type=Path)
    parser.add_argument("--max-source-bytes", type=int, default=8_000_000_000)
    parser.add_argument("--asset-limit", type=int, default=250_000_000)
    parser.add_argument("--maxzoom", type=int, default=15)
    parser.add_argument("--catalog-revision", type=int, default=1)
    parser.add_argument("--publish", action="store_true")
    parser.add_argument("--repository", help="Map repository; otherwise YATOPATH_MAP_REPOSITORY or GITHUB_REPOSITORY")
    args = parser.parse_args()
    if not 1_000_000 <= args.asset_limit <= 2_000_000_000:
        parser.error("Asset limit must be 1 MB...2 GB")
    if args.action == "build" and (not args.source or not args.pmtiles_bin):
        parser.error("build requires --source and --pmtiles-bin")
    if args.action != "matrix":
        try:
            REPOSITORY = destination_repository(args.repository)
        except ValueError as error:
            parser.error(str(error))
    args.work.mkdir(parents=True, exist_ok=True)
    {"matrix": matrix, "build": build_source, "collect": collect}[args.action](args)


if __name__ == "__main__":
    main()
