# Published three-region beta map inventory

This directory keeps the first beta inventory separate from `world/`.
Scope and source snapshot hashes: [BETA_COVERAGE.md](../BETA_COVERAGE.md).

`inventory.json` contains exactly `beta-istanbul-province`, `beta-us-dc`
and `beta-us-md`; each boundary's exact byte SHA-256 is included. Region
status remains `planned` in this immutable preparation inventory. Publication
is tracked separately: all 3 selected sources now have verified immutable
receipts, and `beta-20261001-catalog-1` publishes 8 packs (Istanbul 1, DC 1,
Maryland 5, preserved Monaco pilot 1). This is the selected beta publication
gate, not full-world completion. Real iPhone download/offline/GPS and upgrade
acceptance remain pending.

Published [catalog](https://github.com/mertseyit3444-web/YatoPath-Maps/releases/download/beta-20261001-catalog-1/catalog.json)
SHA-256:
`23268eb56f43cfb355153d312d36a70b266717476d7f537a8c2780c485f53137`.
The [verified receipt index](https://github.com/mertseyit3444-web/YatoPath-Maps/releases/download/beta-20261001-catalog-1/verified-receipts.json)
SHA-256 is
`80e718d8235ca557e7a40560e59b311b5128e6c17a8c78e8a628083f67c805b6`.

Maryland uses five parts with identical current display names. Search an exact
part ID to select one:

| Exact part ID | City aliases |
|---|---|
| `us-maryland-p0` | Cumberland, Frederick, Hagerstown |
| `us-maryland-p10` | Waldorf, Salisbury, Ocean City |
| `us-maryland-p1100` | Bethesda, Silver Spring, Annapolis |
| `us-maryland-p1101` | Baltimore, Towson, Westminster |
| `us-maryland-p111` | Aberdeen, Elkton, Chestertown |

Download all five for the complete provider footprint. Android opens one
selected part; choose the relevant downloaded part when changing areas.
These city aliases do not establish whole-city administrative coverage.
Partition geometry passed the documented floating-point tolerance with no
area overlap or omitted child. The existing clipping rule filtered 45 pieces
under 0.5 metres (about 8.61 metres total); full OSM completeness is not claimed.

Read-only validation from the repository root:

```sh
python -c 'from pathlib import Path; from tools.world_release import checked_inventory, inventory_fingerprint; p=Path("beta/inventory.json"); checked_inventory(p); print(inventory_fingerprint(p))'
python -m unittest tools.test_istanbul_beta_boundary tools.test_beta_us_inventory -v
```

Expected inventory fingerprint:
`01718be676ae6e0cfe66a4888280faabf2a4b85cc91e1c9f03b02faaf493be71`.

`prepare_istanbul_beta_boundary.py` consumes a SHA-pinned complete OSM
province response and validates the 39 district relation metadata.
The complete-PBF district geometry audit is additional evidence described
in the coverage document. `prepare_beta_us_inventory.py` preserves complete
official DC/MD extract clip geometries and does not apply world-exclusive
subtraction. It only fetches the small official metadata index when its
explicit `--fetch-index` option is chosen; it does not download a PBF.

Map production and publication are performed explicitly by the publisher;
no workflow dispatch is requested by copying this inventory. See the published
immutable per-source tags in the coverage document. Existing world production
and the verified Monaco pilot are preserved.
