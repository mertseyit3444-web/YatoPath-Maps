# Three-region beta map inventory

This directory keeps the first beta inventory separate from `world/`.
Scope and source snapshot hashes: [BETA_COVERAGE.md](../BETA_COVERAGE.md).

`inventory.json` contains exactly `beta-istanbul-province`, `beta-us-dc`
and `beta-us-md`; each boundary's exact byte SHA-256 is included. Region
status remains `planned` in this preparation inventory. Production, verified
immutable source receipts, catalog publication and real-device acceptance
are separate gates; this directory alone does not establish them.

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
no workflow dispatch is requested by copying this inventory. See the intended
immutable per-source tags in the coverage document. Existing world production
and the verified Monaco pilot are preserved.
