"""Offline preparation of the complete OSM Istanbul province relation.

Consumes a SHA-pinned /relation/223474/full.json response, never a city bbox.
No PBF download, publication, account access or existing-map mutation occurs.
"""
import argparse
import hashlib
import json
import math
import unicodedata
from pathlib import Path

from shapely.geometry import LineString, mapping
from shapely.ops import polygonize_full, unary_union


RELATION_ID = 223474
SOURCE_URL = "https://api.openstreetmap.org/api/0.6/relation/223474/full.json"
MAX_INPUT_BYTES = 20_000_000
DISTRICTS_URL = "https://istanbul.gov.tr/"
# Official province list; diacritics are normalized only for name comparison.
DISTRICTS = frozenset(("adalar arnavutkoy atasehir avcilar bagcilar bahcelievler "
    "bakirkoy basaksehir bayrampasa besiktas beykoz beylikduzu beyoglu buyukcekmece "
    "catalca cekmekoy esenler esenyurt eyupsultan fatih gaziosmanpasa gungoren "
    "kadikoy kagithane kartal kucukcekmece maltepe pendik sancaktepe sariyer "
    "silivri sultanbeyli sultangazi sile sisli tuzla umraniye uskudar zeytinburnu").split())


def normalized_name(value):
    value = value.casefold().replace("ı", "i")
    return "".join(char for char in unicodedata.normalize("NFKD", value)
                   if not unicodedata.combining(char))


def province_geometry(document):
    elements = {}
    for element in document["elements"]:
        key = (element["type"], element["id"])
        if key in elements:
            raise ValueError("Duplicate OSM element")
        elements[key] = element
    relation = elements.get(("relation", RELATION_ID), {})
    tags = relation.get("tags", {})
    if any(tags.get(key) != value for key, value in {
        "type": "boundary", "boundary": "administrative", "admin_level": "4",
        "ISO3166-2": "TR-34"}.items()):
        raise ValueError("Full Istanbul province administrative relation required")
    if type(relation.get("version")) is not int or relation["version"] <= 0 or not relation.get("timestamp"):
        raise ValueError("Province revision metadata required")
    districts, district_ids, member_ids = [], set(), set()
    lines = {"outer": [], "inner": []}
    way_revisions = []
    for member in relation["members"]:
        identity = (member["type"], member["ref"])
        if identity in member_ids:
            raise ValueError("Duplicate province member")
        member_ids.add(identity)
        role = member["role"]
        if role == "subarea":
            child = elements.get(identity, {})
            child_tags = child.get("tags", {})
            if (member["type"] != "relation" or child_tags.get("admin_level") != "6"
                    or child_tags.get("boundary") != "administrative"):
                raise ValueError("Complete district metadata required")
            district_ids.add(member["ref"])
            districts.append({"relationId": member["ref"], "name": child_tags.get("name", ""),
                              "version": child.get("version"), "timestamp": child.get("timestamp")})
        elif role in lines:
            way = elements.get(identity, {})
            if member["type"] != "way" or len(way.get("nodes", [])) < 2:
                raise ValueError("Boundary way missing or incomplete")
            coordinates = []
            for identifier in way["nodes"]:
                node = elements.get(("node", identifier), {})
                lon, lat = node.get("lon"), node.get("lat")
                if (not isinstance(lon, (float, int)) or not isinstance(lat, (float, int))
                        or not math.isfinite(lon) or not math.isfinite(lat)
                        or not 26 < lon < 31 or not 39 < lat < 43):
                    raise ValueError("Missing or invalid Istanbul boundary node")
                coordinates.append((lon, lat))
            lines[role].append(LineString(coordinates))
            way_revisions.append({"wayId": member["ref"], "role": role,
                                  "version": way.get("version"), "timestamp": way.get("timestamp")})
        elif role not in ("label", "admin_centre"):
            raise ValueError("Unexpected province boundary member role")
    names = {normalized_name(child["name"]) for child in districts}
    if len(district_ids) != 39 or len(districts) != 39 or names != DISTRICTS:
        raise ValueError("All 39 official Istanbul districts, including Adalar, required")
    if not lines["outer"]:
        raise ValueError("No province exterior boundary")
    polygons = {}
    for role, pieces in lines.items():
        if not pieces:
            polygons[role] = None
            continue
        polygon, cuts, dangles, invalid = polygonize_full(pieces)
        if not cuts.is_empty or not dangles.is_empty or not invalid.is_empty or polygon.is_empty:
            raise ValueError("Boundary members do not form complete valid rings")
        polygons[role] = unary_union(polygon)
    geometry = polygons["outer"]
    if polygons["inner"] is not None:
        if not geometry.covers(polygons["inner"]):
            raise ValueError("Interior boundary lies outside province")
        geometry = geometry.difference(polygons["inner"])
    if geometry.is_empty or not geometry.is_valid or geometry.geom_type not in ("Polygon", "MultiPolygon"):
        raise ValueError("Province geometry is invalid; automatic repair is forbidden")
    return geometry, relation, sorted(districts, key=lambda item: item["relationId"]), way_revisions


def prepare(source, expected_sha256, output):
    if not 0 < source.stat().st_size <= MAX_INPUT_BYTES:
        raise ValueError("Boundary input exceeds 20 MB intake limit")
    raw = source.read_bytes()
    digest = hashlib.sha256(raw).hexdigest()
    if digest != expected_sha256:
        raise ValueError("Boundary input differs from pinned SHA-256")
    geometry, relation, districts, ways = province_geometry(json.loads(raw))
    if output.exists():
        raise ValueError("Use a fresh boundary output directory")
    output.mkdir(parents=True)
    boundary = output / "boundaries" / "tr-istanbul.geojson"
    boundary.parent.mkdir()
    boundary.write_text(json.dumps({"type": "Feature", "properties": {
        "name": "İstanbul ili", "osmRelationId": RELATION_ID},
        "geometry": mapping(geometry)}, ensure_ascii=False, separators=(",", ":")) + "\n",
        encoding="utf-8", newline="\n")
    boundary_sha = hashlib.sha256(boundary.read_bytes()).hexdigest()
    proof = {"schemaVersion": 1, "status": "boundary-prepared-not-map-accepted",
        "sourceURL": SOURCE_URL, "sourceSHA256": digest, "sourceBytes": len(raw),
        "relationId": RELATION_ID, "relationVersion": relation["version"],
        "relationTimestamp": relation["timestamp"], "license": "ODbL-1.0",
        "attribution": "© OpenStreetMap contributors", "licenseURL": "https://www.openstreetmap.org/copyright",
        "districtReferenceURL": DISTRICTS_URL, "districtCount": len(districts), "districts": districts,
        "boundaryWayRevisions": ways, "boundarySHA256": boundary_sha,
        "geometryType": geometry.geom_type, "bounds": list(geometry.bounds),
        "areaDegreesSquared": geometry.area, "geometryRepaired": False,
        "roadPackVerified": False, "vectorMapVerified": False, "hostVerified": False}
    manifest = {"schemaVersion": 1, "regions": [{"id": "tr-istanbul", "sourceId": "beta-istanbul-province",
        "name": "İstanbul ili", "pbfURL": "https://download.geofabrik.de/europe/turkey-latest.osm.pbf",
        "boundary": boundary.name, "boundarySHA256": boundary_sha, "status": "planned"}]}
    for name, value in (("boundary-provenance.json", proof), ("inventory.json", manifest)):
        (output / name).write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8", newline="\n")
    return proof


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", type=Path, required=True)
    parser.add_argument("--input-sha256", required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    print(json.dumps(prepare(args.input, args.input_sha256, args.output), ensure_ascii=True))
