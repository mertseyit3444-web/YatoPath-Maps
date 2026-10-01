"""Derive exclusive production footprints from audited (overlapping) extracts."""
import argparse
import hashlib
import json
from pathlib import Path

from shapely.geometry import mapping
from shapely.ops import unary_union

try:
    from tools.build_region_pack import load_boundary
    from tools.plan_world_regions import polygonal
    from tools.world_release import checked_inventory, inventory_fingerprint, write_json
except ModuleNotFoundError:
    from build_region_pack import load_boundary
    from plan_world_regions import polygonal
    from world_release import checked_inventory, inventory_fingerprint, write_json


def exclusive_regions(items):
    # Small local extracts own their area first. Broad overlapping extracts
    # (Alps, DACH, US regional aggregates) supply only the remaining area.
    ordered = sorted(items, key=lambda item: (item[1].area, item[0]["id"]))
    owned, result, replaced = None, [], []
    for original, geometry in ordered:
        footprint = geometry if owned is None else geometry.difference(owned)
        if footprint.is_empty or footprint.area == 0:
            replaced.append({"id": original["id"], "sourceId": original["sourceId"],
                             "status": "covered-by-other-footprints"})
            continue
        footprint = polygonal(footprint)
        if not footprint.is_valid:
            raise ValueError("Exclusive footprint is invalid; production was not started")
        region = {**original, "inputBoundarySHA256": original["boundarySHA256"],
                  "removedOverlapArea": geometry.area - footprint.area,
                  "exclusiveArea": footprint.area}
        result.append((region, footprint))
        owned = footprint if owned is None else unary_union([owned, footprint])
    return result, replaced


def prepare(inventory, output):
    document = checked_inventory(inventory)
    items = [(region, load_boundary(inventory.parent / "boundaries" / region["boundary"]))
             for region in document["regions"]]
    selected, replaced = exclusive_regions(items)
    output.mkdir(parents=True, exist_ok=False)
    boundaries = output / "boundaries"
    boundaries.mkdir()
    regions = []
    for region, footprint in selected:
        boundary = boundaries / region["boundary"]
        write_json(boundary, {"type": "Feature", "properties": {"sourceId": region["sourceId"]},
                              "geometry": mapping(footprint)})
        region["boundarySHA256"] = hashlib.sha256(boundary.read_bytes()).hexdigest()
        regions.append(region)
    result = {**document, "regions": sorted(regions, key=lambda region: region["id"]),
              "inputInventoryFingerprint": inventory_fingerprint(inventory),
              "plannedRegionCount": len(regions), "coveredRegions": replaced,
              "footprintPolicy": "smaller audited extracts first; remaining area only",
              "servedRegionCount": 0}
    write_json(output / "inventory.json", result)
    print(json.dumps({"regions": len(regions), "coveredByOthers": len(replaced),
                      "sourceCount": len({r["sourceId"] for r in regions})}))


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--inventory", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    prepare(args.inventory, args.output)
