#!/usr/bin/env python3
"""Extract a pinned Protomaps vector PMTiles region and verify the archive."""

import argparse
import hashlib
import json
import subprocess
from pathlib import Path
from urllib.parse import urlparse


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--pmtiles-bin", type=Path, required=True)
    parser.add_argument("--source", required=True,
                        help="Pinned Protomaps build URL, e.g. https://build.protomaps.com/20260929.pmtiles")
    parser.add_argument("--boundary", type=Path, required=True)
    parser.add_argument("--maxzoom", type=int, default=15)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    source = urlparse(args.source)
    if source.scheme != "https" or source.hostname != "build.protomaps.com" or not source.path.endswith(".pmtiles"):
        parser.error("source must be a pinned HTTPS Protomaps build")
    if not args.pmtiles_bin.is_file() or not args.boundary.is_file():
        parser.error("pmtiles binary and GeoJSON boundary must exist")
    if not 0 <= args.maxzoom <= 15:
        parser.error("maxzoom must be 0...15 for this basemap source")
    if args.output.exists() or args.output.with_suffix(".json").exists():
        parser.error("output exists; use a fresh release directory")
    args.output.parent.mkdir(parents=True, exist_ok=True)
    partial = args.output.with_suffix(args.output.suffix + ".part")
    if partial.exists():
        parser.error(f"incomplete extraction exists: {partial}")
    try:
        subprocess.run([str(args.pmtiles_bin), "extract", args.source, str(partial),
                        f"--region={args.boundary}", f"--maxzoom={args.maxzoom}"], check=True)
        subprocess.run([str(args.pmtiles_bin), "verify", str(partial)], check=True)
        result = subprocess.run([str(args.pmtiles_bin), "show", str(partial), "--header-json"],
                                check=True, capture_output=True, text=True)
        header = json.loads(result.stdout)
        if header.get("tile_type") != "mvt" or header.get("maxzoom", 99) > args.maxzoom:
            raise ValueError("Expected a vector PMTiles archive within the chosen zoom range")
        with partial.open("rb") as handle:
            digest = hashlib.file_digest(handle, "sha256").hexdigest()
        metadata = {"source": args.source, "boundarySha256": hashlib.sha256(args.boundary.read_bytes()).hexdigest(),
                    "maxzoom": header["maxzoom"], "bytes": partial.stat().st_size, "sha256": digest}
        partial.replace(args.output)
        args.output.with_suffix(".json").write_text(json.dumps(metadata, indent=2) + "\n", encoding="utf-8")
        print(json.dumps(metadata))
    finally:
        partial.unlink(missing_ok=True)


if __name__ == "__main__":
    main()
