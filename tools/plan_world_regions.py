"""Build a reproducible, unserved production inventory from a Geofabrik snapshot."""
import argparse
import hashlib
import json
import re
from pathlib import Path
from urllib.parse import urlsplit

from shapely import make_valid
from shapely.errors import ShapelyError
from shapely.geometry import MultiPolygon, box, mapping, shape
from shapely.ops import unary_union
from shapely.validation import explain_validity


def public_pbf(url):
    p = urlsplit(url)
    return (p.scheme == "https" and p.netloc == "download.geofabrik.de"
            and p.path.endswith(".osm.pbf") and "internal" not in p.path
            and not p.query and not p.fragment)


def polygon_parts(geometry):
    if geometry.is_empty:
        return []
    if geometry.geom_type == "Polygon":
        return [geometry]
    if geometry.geom_type in ("MultiPolygon", "GeometryCollection"):
        return [part for item in geometry.geoms for part in polygon_parts(item)]
    return []


def polygonal(geometry):
    parts = polygon_parts(geometry)
    if not parts:
        raise ValueError("Boundary has no polygon area")
    return parts[0] if len(parts) == 1 else MultiPolygon(parts)


def normalize_boundary(geometry):
    """Audit small upstream defects without silently inventing replacement areas."""
    g = shape(geometry)
    if g.is_empty or g.geom_type not in ("Polygon", "MultiPolygon") or g.area <= 0:
        raise ValueError("Source boundary must have nonempty polygon area")
    west, south, east, north = g.bounds
    if not (-180 <= west < east <= 180 and -90 <= south < north <= 90):
        raise ValueError("Source boundary has invalid coordinates")
    audit = None
    if not g.is_valid:
        # MultiPolygon components represent a union. Repair each polygon first,
        # then union: make_valid on nested shells as a whole can create holes.
        repaired = polygonal(unary_union([
            polygonal(make_valid(part)) for part in polygon_parts(g)
        ]))
        area_change = abs(repaired.area - g.area) / g.area
        if not repaired.is_valid or area_change > 0.005 or repaired.bounds != g.bounds:
            raise ValueError("Boundary repair exceeded the 0.5% area or unchanged-bounds gate")
        audit = {"reason": explain_validity(g), "method": "make_valid components; polygon union",
                 "relativeAreaChange": area_change, "originalArea": g.area, "repairedArea": repaired.area}
        g = repaired
    return g, audit


def regional_boundaries(geometry):
    """Separate dateline islands instead of producing one almost-planet-wide bbox."""
    g, _ = normalize_boundary(geometry)
    west, _, east, _ = g.bounds
    # Web Mercator basemaps do not cover the geographic poles.
    g = g.intersection(box(-180, -85.05112878, 180, 85.05112878))
    if not polygon_parts(g):
        raise ValueError("Source has no area within the basemap's latitude range")
    if east - west <= 180:
        return [("", polygonal(g))]
    result = []
    for suffix, extent in (("west", box(-180, -85.05112878, 0, 85.05112878)),
                           ("east", box(0, -85.05112878, 180, 85.05112878))):
        clipped = g.intersection(extent)
        if polygon_parts(clipped):
            result.append((suffix, polygonal(clipped)))
    return result


def source_name(properties):
    source_id = properties["id"]
    name = properties.get("name") or source_id
    if name == source_id and "/" in name:
        name = name.rsplit("/", 1)[-1].replace("-", " ").title()
    codes = properties.get("iso3166-1:alpha2", [])
    if not codes:
        codes = sorted({value.split("-", 1)[0] for value in properties.get("iso3166-2", [])})
    return name + (" · " + "/".join(codes) if codes else "")


def plan(document):
    features = document.get("features", [])
    if document.get("type") != "FeatureCollection" or not features:
        raise ValueError("A nonempty GeoJSON source inventory is required")
    by_id = {}
    for feature in features:
        p = feature.get("properties", {})
        if not isinstance(p.get("id"), str) or p["id"] in by_id:
            raise ValueError("Source IDs must be unique strings")
        by_id[p["id"]] = feature
    parents = {item["properties"].get("parent") for item in features}
    selected = [feature for source_id, feature in sorted(by_id.items()) if source_id not in parents]
    regions, rejected, used = [], [], set()
    for feature in selected:
        p = feature["properties"]
        url = p.get("urls", {}).get("pbf")
        if not isinstance(url, str) or not public_pbf(url):
            rejected.append({"sourceId": p["id"], "reason": "No public official PBF"})
            continue
        stem = re.sub(r"[^a-z0-9]+", "-", p["id"].lower()).strip("-")
        # Slash/underscore spellings and east/west suffixes can otherwise collide.
        # Derive the hash even before a second spelling appears in a later index.
        slug = "gf-" + stem[:39].rstrip("-") + "-" + hashlib.sha256(p["id"].encode()).hexdigest()[:10]
        try:
            _, repair = normalize_boundary(feature.get("geometry"))
            boundaries = regional_boundaries(feature.get("geometry"))
        except (ValueError, TypeError, AttributeError, ShapelyError) as error:
            rejected.append({"sourceId": p["id"], "reason": str(error)})
            continue
        for suffix, boundary in boundaries:
            region_id = slug + ("-" + suffix if suffix else "")
            if region_id in used:
                raise ValueError("Normalized region ID collision")
            used.add(region_id)
            regions.append({"id": region_id, "name": source_name(p) + (" / " + suffix if suffix else ""),
                            "sourceId": p["id"], "pbfURL": url, "parent": p.get("parent"),
                            "status": "planned", "boundary": region_id + ".geojson",
                            "boundaryRepair": repair, "geometry": mapping(boundary)})
    return regions, rejected


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--index", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    raw = args.index.read_bytes()
    regions, rejected = plan(json.loads(raw))
    args.output.mkdir(parents=True, exist_ok=False)
    boundary_folder = args.output / "boundaries"
    boundary_folder.mkdir()
    for region in regions:
        geometry = region.pop("geometry")
        boundary = json.dumps({"type": "Feature", "properties": {"sourceId": region["sourceId"]},
                               "geometry": geometry}, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
        (boundary_folder / region["boundary"]).write_bytes(boundary)
        region["boundarySHA256"] = hashlib.sha256(boundary).hexdigest()
    result = {"schemaVersion": 1, "sourceURL": "https://download.geofabrik.de/index-v1.json",
              "sourceSHA256": hashlib.sha256(raw).hexdigest(), "sourceFeatureCount": len(json.loads(raw)["features"]),
              "plannedRegionCount": len(regions), "regions": regions, "rejectedSources": rejected,
              "servedRegionCount": 0, "coverage": "Geofabrik leaf extracts; not a published world catalog"}
    (args.output / "inventory.json").write_text(json.dumps(result, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    print(json.dumps({key: result[key] for key in ("sourceFeatureCount", "plannedRegionCount", "servedRegionCount")}))
    print("Rejected sources:", len(rejected))


if __name__ == "__main__":
    main()
