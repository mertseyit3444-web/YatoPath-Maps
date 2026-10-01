#!/usr/bin/env python3
"""Publishable catalog for independently hosted road and basemap packages."""
import argparse
import datetime as dt
import hashlib
import json
from pathlib import Path
from urllib.parse import quote, urlparse


def asset(path, base_url):
    with path.open('rb') as handle:
        digest = hashlib.file_digest(handle, 'sha256').hexdigest()
    return {'url': base_url.rstrip('/') + '/' + quote(path.name), 'bytes': path.stat().st_size, 'sha256': digest}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--directory', type=Path, required=True)
    parser.add_argument('--base-url', required=True, help='HTTPS URL serving package files')
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--allow-road-only', action='store_true', help='Development only; not a complete offline map')
    args = parser.parse_args()
    if urlparse(args.base_url).scheme != 'https':
        parser.error('base-url must use HTTPS')
    packs = []
    for road in sorted(args.directory.glob('*.sqlite')):
        info_file = road.with_suffix('.json')
        if not info_file.is_file():
            parser.error(f'missing metadata for {road}')
        info = json.loads(info_file.read_text(encoding='utf-8'))
        if info.get('id') != road.stem:
            parser.error(f'pack ID does not match filename: {road}')
        tiles = args.directory / f"{info['id']}.mbtiles"
        dark_tiles = args.directory / f"{info['id']}-dark.mbtiles"
        vector_tiles = args.directory / f"{info['id']}.pmtiles"
        if not road.is_file():
            parser.error(f'missing {road}')
        if not tiles.is_file() and not vector_tiles.is_file() and not args.allow_road_only:
            parser.error(f'missing {tiles} or {vector_tiles}; an offline basemap is required')
        if tiles.is_file() and not dark_tiles.is_file() and not vector_tiles.is_file() and not args.allow_road_only:
            parser.error(f'missing {dark_tiles}; a dark offline basemap is required')
        if road.stat().st_size != info['bytes'] or asset(road, args.base_url)['sha256'] != info['sha256']:
            parser.error(f'road checksum mismatch for {road}')
        if road.stat().st_size > 2_000_000_000:
            parser.error(f'{road} exceeds the mobile per-file limit; split the region')
        for basemap_asset in (tiles, dark_tiles, vector_tiles):
            if basemap_asset.exists() and not 8 <= basemap_asset.stat().st_size <= 2_000_000_000:
                parser.error(f'{basemap_asset} exceeds the mobile per-file limit; split the region')
        packs.append({'id': info['id'], 'name': info['name'], 'bounds': info['bounds'],
                      'road': asset(road, args.base_url),
                      'basemap': asset(tiles, args.base_url) if tiles.is_file() else None,
                      'basemapDark': asset(dark_tiles, args.base_url) if dark_tiles.is_file() else None,
                      'vectorBasemap': asset(vector_tiles, args.base_url) if vector_tiles.is_file() else None})
        if info.get('searchNames'):
            packs[-1]['searchNames'] = info['searchNames']
    if not packs:
        parser.error('no packs found')
    catalog = {'schemaVersion': 1, 'generatedAt': dt.datetime.now(dt.timezone.utc).isoformat(), 'packs': packs}
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(catalog, ensure_ascii=False, indent=2), encoding='utf-8')
    print(f'wrote {len(packs)} packs to {args.output}')


if __name__ == '__main__':
    main()
