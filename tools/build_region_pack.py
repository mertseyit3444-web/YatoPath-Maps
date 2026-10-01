#!/usr/bin/env python3
"""Build one portable YatoPath road pack from an OSM PBF or an existing roads DB.

Each pack has globally stable OSM segment IDs. The same region ID must be used
for later rebuilds so completion stays associated with that region.
"""

import argparse
import hashlib
import json
import math
import re
import sqlite3
from contextlib import closing
from pathlib import Path

try:
    from shapely.geometry import LineString, Point, shape
    from shapely.ops import unary_union
except ImportError:
    LineString = Point = shape = unary_union = None

RUNNABLE = {"residential", "living_street", "service", "unclassified", "tertiary", "tertiary_link", "secondary", "secondary_link", "primary", "primary_link", "pedestrian", "footway", "path", "track", "steps", "road"}
FOOT_IF_SIGNED = {"cycleway", "bridleway"}
FOOT_ALLOWED = {"yes", "designated", "official", "permissive"}
FORBIDDEN = {"no", "private"}


class NoRunnableRoads(SystemExit):
    """Completed input processing and SQL count=0; never a parse/network error."""

    def __init__(self, region_id, boundary, input_kind, source_state):
        super().__init__("No runnable roads found; check source region and input")
        self.evidence = {"regionId": region_id, "inputKind": input_kind,
                         "inputFullyRead": True, "roadCount": 0, "roadMeters": 0,
                         "sourceState": source_state,
                         "clippingBoundarySHA256": (hashlib.sha256(boundary.read_bytes()).hexdigest()
                                                    if boundary else None)}


def meters(a, b, c, d):
    r1, r2 = math.radians(a), math.radians(c)
    h = math.sin((r2-r1)/2)**2 + math.cos(r1)*math.cos(r2)*math.sin(math.radians(d-b)/2)**2
    return 12742000 * math.asin(min(1, math.sqrt(h)))


def stable_edge_id(way_id, first_node, second_node, occurrence=0):
    """An unchanged OSM node pair keeps its ID when other way nodes move."""
    low, high = sorted((first_node, second_node))
    value = f"osm-edge-v1:{way_id}:{low}:{high}:{occurrence}".encode("ascii")
    return int.from_bytes(hashlib.blake2b(value, digest_size=8).digest(), "big") & ((1 << 63) - 1)


def clipped_piece_id(segment_id, piece_index):
    if piece_index == 0:
        return segment_id
    value = f"osm-clip-v1:{segment_id}:{piece_index}".encode("ascii")
    return int.from_bytes(hashlib.blake2b(value, digest_size=8).digest(), "big") & ((1 << 63) - 1)


def load_boundary(path):
    if path is None:
        return None
    if shape is None:
        raise SystemExit("Boundary clipping requires: python -m pip install shapely")
    document = json.loads(path.read_text(encoding="utf-8"))
    features = document.get("features", []) if document.get("type") == "FeatureCollection" else [document]
    geometries = [shape(feature.get("geometry", feature)) for feature in features]
    if not geometries or any(g.geom_type not in {"Polygon", "MultiPolygon"} or not g.is_valid or g.is_empty
                             for g in geometries):
        raise SystemExit("Boundary must contain valid GeoJSON Polygon or MultiPolygon geometry")
    boundary = unary_union(geometries)
    west, south, east, north = boundary.bounds
    if not (-180 <= west < east <= 180 and -90 <= south < north <= 90) or east - west > 180:
        raise SystemExit("Boundary coordinates are invalid or cross the antimeridian")
    return boundary


def clipped_parts(segment_id, a, b, c, d, boundary, stable_clips=False):
    """Return stable IDs and pieces of a directed OSM edge inside one region."""
    if boundary is None:
        return [(segment_id, a, b, c, d)]
    line = LineString(((b, a), (d, c)))
    intersection = line.intersection(boundary)
    if intersection.is_empty:
        return []
    pieces = ([intersection] if intersection.geom_type == "LineString" else
              [part for part in getattr(intersection, "geoms", []) if part.geom_type == "LineString"])
    pieces.sort(key=lambda part: line.project(Point(part.coords[0])))
    result = []
    for index, part in enumerate(pieces):
        start, end = part.coords[0], part.coords[-1]
        if line.project(Point(start)) > line.project(Point(end)):
            start, end = end, start
        piece_id = clipped_piece_id(segment_id, index)
        if stable_clips and (start != (b, a) or end != (d, c)):
            # The first clipped piece previously reused the entire edge's ID.
            # Neighboring packages could therefore have the same ID for two
            # different geometries. Include the canonical endpoints instead.
            identity = f"osm-clip-v2:{segment_id}:" + ":".join(
                f"{value:.9f}" for value in (start[1], start[0], end[1], end[0]))
            piece_id = int.from_bytes(hashlib.blake2b(identity.encode(), digest_size=8).digest(), "big") & ((1 << 63)-1)
        result.append((piece_id, start[1], start[0], end[1], end[0]))
    return result


def from_pbf(db, path, region_id, boundary=None, stable_clips=False):
    try:
        import osmium
    except ImportError as exc:
        raise SystemExit("PBF processing requires: python -m pip install osmium") from exc

    class Handler(osmium.SimpleHandler):
        def __init__(self):
            super().__init__()
            self.rows, self.bounds = [], []
            self.places = set()

        def node(self, node):
            if node.tags.get("place") not in {"city", "town"} or not node.location.valid():
                return
            if boundary is not None and not boundary.covers(Point(node.location.lon, node.location.lat)):
                return
            for tag in node.tags:
                if tag.k in {"name", "name:en", "name:tr", "name:ar", "name:bn", "name:es",
                             "name:fr", "name:hi", "name:pt", "name:zh", "official_name"}:
                    self.places.update((name.strip(), node.location.lat, node.location.lon)
                                       for name in tag.v.split(";") if name.strip())

        def flush(self):
            if self.rows:
                db.executemany("INSERT INTO segments VALUES (?,?,?,?,?,?,?,?)", self.rows)
                db.executemany("INSERT INTO segment_bounds VALUES (?,?,?,?,?)", self.bounds)
                db.commit()
                self.rows.clear(); self.bounds.clear()

        def way(self, way):
            tags = way.tags
            highway = tags.get("highway")
            if highway not in RUNNABLE and not (highway in FOOT_IF_SIGNED and tags.get("foot") in FOOT_ALLOWED):
                return
            if tags.get("access") in FORBIDDEN or tags.get("foot") in FORBIDDEN:
                return
            if highway == "service" and tags.get("service") in {"parking_aisle", "driveway"}:
                return
            if tags.get("construction") or tags.get("area") == "yes":
                return
            seen_pairs = {}
            for index in range(len(way.nodes)-1):
                p, q = way.nodes[index].location, way.nodes[index+1].location
                if not p.valid() or not q.valid():
                    continue
                a, b, c, d = p.lat, p.lon, q.lat, q.lon
                first_node, second_node = way.nodes[index].ref, way.nodes[index+1].ref
                pair = tuple(sorted((first_node, second_node)))
                occurrence = seen_pairs.get(pair, 0)
                seen_pairs[pair] = occurrence + 1
                if first_node > second_node:
                    a, b, c, d = c, d, a, b
                length = meters(a, b, c, d)
                if not (0.5 <= length <= 10_000):
                    continue
                segment_id = stable_edge_id(way.id, first_node, second_node, occurrence)
                for piece_id, p_lat, p_lon, q_lat, q_lon in clipped_parts(segment_id, a, b, c, d, boundary, stable_clips):
                    piece_length = meters(p_lat, p_lon, q_lat, q_lon)
                    if piece_length < 0.5:
                        continue
                    self.rows.append((piece_id, way.id, region_id, p_lat, p_lon, q_lat, q_lon, piece_length))
                    self.bounds.append((piece_id, min(p_lon, q_lon), max(p_lon, q_lon),
                                        min(p_lat, q_lat), max(p_lat, q_lat)))
                if len(self.rows) >= 10_000:
                    self.flush()

    handler = Handler()
    # File-backed location lookup avoids retaining a continent's node index
    # in RAM on the production runner.
    handler.apply_file(str(path), locations=True, idx="sparse_file_array")
    handler.flush()
    db.executemany("INSERT OR IGNORE INTO places VALUES (?,?,?)", sorted(handler.places))
    db.commit()


def from_database(db, source_path, source_state, region_id, boundary=None, stable_clips=False):
    db.execute("ATTACH DATABASE ? AS source", (str(source_path),))
    if boundary is None:
        db.execute("INSERT INTO segments SELECT id, way_id, ?, lat1, lon1, lat2, lon2, length FROM source.segments WHERE state=?", (region_id, source_state))
        db.execute("INSERT INTO segment_bounds SELECT b.* FROM source.segment_bounds b JOIN segments s ON s.id=b.id")
    else:
        cursor = db.execute("SELECT id,way_id,lat1,lon1,lat2,lon2 FROM source.segments WHERE state=?", (source_state,))
        while rows := cursor.fetchmany(10_000):
            segments, bounds = [], []
            for segment_id, way_id, a, b, c, d in rows:
                for piece_id, p_lat, p_lon, q_lat, q_lon in clipped_parts(segment_id, a, b, c, d, boundary, stable_clips):
                    length = meters(p_lat, p_lon, q_lat, q_lon)
                    if length < 0.5:
                        continue
                    segments.append((piece_id, way_id, region_id, p_lat, p_lon, q_lat, q_lon, length))
                    bounds.append((piece_id, min(p_lon, q_lon), max(p_lon, q_lon),
                                   min(p_lat, q_lat), max(p_lat, q_lat)))
            db.executemany("INSERT INTO segments VALUES (?,?,?,?,?,?,?,?)", segments)
            db.executemany("INSERT INTO segment_bounds VALUES (?,?,?,?,?)", bounds)
    db.commit()
    if db.execute("SELECT 1 FROM source.sqlite_master WHERE type='table' AND name='places'").fetchone():
        places = db.execute("SELECT name,lat,lon FROM source.places").fetchall()
        if boundary is not None:
            places = [row for row in places if boundary.covers(Point(row[2], row[1]))]
        db.executemany("INSERT OR IGNORE INTO places VALUES (?,?,?)", places)
        db.commit()
    db.execute("DETACH DATABASE source")


def build(args):
    if not re.fullmatch(r"[a-z0-9]+(?:-[a-z0-9]+)*", args.region_id):
        raise SystemExit("region-id must be lowercase ASCII words separated by hyphens")
    if args.output.exists():
        raise SystemExit(f"Refusing to overwrite {args.output}")
    boundary = load_boundary(args.boundary)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    db = sqlite3.connect(args.output)
    built = False
    try:
        db.executescript("""
            PRAGMA journal_mode=DELETE;
            PRAGMA synchronous=FULL;
            CREATE TABLE segments (id INTEGER PRIMARY KEY, way_id INTEGER NOT NULL, state TEXT NOT NULL, lat1 REAL NOT NULL, lon1 REAL NOT NULL, lat2 REAL NOT NULL, lon2 REAL NOT NULL, length REAL NOT NULL);
            CREATE VIRTUAL TABLE segment_bounds USING rtree(id, min_lon, max_lon, min_lat, max_lat);
            CREATE TABLE region_totals (state TEXT PRIMARY KEY, meters REAL NOT NULL);
            CREATE TABLE pack_info (region_id TEXT PRIMARY KEY, name TEXT NOT NULL, south REAL NOT NULL, west REAL NOT NULL, north REAL NOT NULL, east REAL NOT NULL, road_count INTEGER NOT NULL, id_scheme TEXT NOT NULL);
            CREATE TABLE places (name TEXT, lat REAL, lon REAL, PRIMARY KEY(name,lat,lon));
        """)
        if args.pbf:
            from_pbf(db, args.pbf, args.region_id, boundary, getattr(args, "stable_clips", False))
        else:
            from_database(db, args.from_db, args.source_state, args.region_id, boundary, getattr(args, "stable_clips", False))
        count, total = db.execute("SELECT count(*), coalesce(sum(length),0) FROM segments").fetchone()
        if count == 0:
            raise NoRunnableRoads(args.region_id, args.boundary,
                                  "pbf" if args.pbf else "roads-db", args.source_state)
        bounds = db.execute("SELECT min(min_lat),min(min_lon),max(max_lat),max(max_lon) FROM segment_bounds").fetchone()
        db.execute("INSERT INTO region_totals VALUES (?,?)", (args.region_id, total))
        id_scheme = "osm-node-pair-v1" if args.pbf else (
            "legacy-way-index-v1" if boundary is None else "geometry-v1")
        if getattr(args, "stable_clips", False):
            id_scheme = "osm-node-pair-geometry-v2"
        db.execute("INSERT INTO pack_info VALUES (?,?,?,?,?,?,?,?)", (args.region_id, args.name, *bounds, count, id_scheme))
        db.execute("CREATE INDEX segments_state ON segments(state)")
        db.execute("PRAGMA user_version=2")
        db.commit()
        db.execute("VACUUM")
        assert db.execute("PRAGMA quick_check").fetchone()[0] == "ok"
        built = True
    finally:
        db.close()
        if not built:
            args.output.unlink(missing_ok=True)
    with args.output.open("rb") as artifact:
        digest = hashlib.file_digest(artifact, "sha256").hexdigest()
    source = args.pbf or args.from_db
    with source.open("rb") as artifact:
        source_digest = hashlib.file_digest(artifact, "sha256").hexdigest()
    info = {"id": args.region_id, "name": args.name, "bounds": dict(zip(("south","west","north","east"), bounds)), "roadCount": count, "roadMeters": total, "bytes": args.output.stat().st_size, "sha256": digest, "sourceSha256": source_digest, "idScheme": id_scheme}
    with closing(sqlite3.connect(args.output)) as metadata_db:
        info["searchNames"] = [row[0] for row in metadata_db.execute("SELECT DISTINCT name FROM places ORDER BY name")]
    if args.boundary:
        info["boundarySha256"] = hashlib.sha256(args.boundary.read_bytes()).hexdigest()
    args.output.with_suffix(".json").write_text(json.dumps(info, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(info))


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    source = parser.add_mutually_exclusive_group(required=True)
    source.add_argument("--pbf", type=Path)
    source.add_argument("--from-db", type=Path)
    parser.add_argument("--boundary", type=Path, help="GeoJSON polygon for a city or smaller region")
    parser.add_argument("--stable-clips", action="store_true", help="Give clipped geometries distinct stable IDs (new production packs)")
    parser.add_argument("--source-state", help="Legacy state code when using --from-db")
    parser.add_argument("--region-id", required=True)
    parser.add_argument("--name", required=True)
    parser.add_argument("--output", type=Path, required=True)
    arguments = parser.parse_args()
    if arguments.from_db and not arguments.source_state:
        parser.error("--source-state is required with --from-db")
    build(arguments)
