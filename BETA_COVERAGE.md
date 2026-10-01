# YatoPath first beta map coverage

Prepared 1 October 2026. This is a **three-region beta**, with Istanbul
province, Washington DC and Maryland. It is not a complete world map release.
The checked beta inventory is [`beta/inventory.json`](beta/inventory.json);
its canonical SHA-256 is
`01718be676ae6e0cfe66a4888280faabf2a4b85cc91e1c9f03b02faaf493be71`.
The separate 522-source world inventory remains unchanged.

## Boundary scope

- **Istanbul province:** the complete OpenStreetMap administrative province
  relation 223474 (TR-34), including its coast/island extent. The independent
  geometry audit read the complete Turkey PBF and found all 39 district
  relations, including Adalar. API province and PBF province geometry had zero
  symmetric difference. District geometries are an audit, not the production
  clip: their union differs from the province by 0.625449361807468 square
  degrees. That difference is not assumed to be empty sea or subtracted. The
  complete province boundary is retained.
- **Washington DC and Maryland:** the complete, unmodified official Geofabrik
  extract clip geometries for `us/district-of-columbia` and `us/maryland`.
  These are provider extract footprints, with possible border/coastal margins;
  they are not separately verified administrative relation geometries.
  Maryland is not reduced by the overlapping world-exclusive-region plan.
  The full clip retains 0.004833913186665725 square degrees removed by that
  plan. DC and Maryland clip overlap is retained; stable road identities and
  geometry are used when checking overlap, rather than deleting an arbitrary
  source footprint.

The boundary files preserve valid complete polygonal geometry without silent
repair or simplification. Areas above are coordinate-space diagnostic areas,
not land square kilometres or road mileage. Whole-source road parsing,
basemap extraction and on-device acceptance are separate checks.

## Fresh OSM source snapshots

These are completed local downloads from the official public Geofabrik host;
each `latest` alias resolved to a dated file. They do not use the old bundled
DC/MD/VA road database, whose original PBF snapshot hashes are unavailable.

| Source | Dated resolved URL | Bytes | PBF SHA-256 |
|---|---|---:|---|
| `beta-istanbul-province` | [Turkey 260930](https://download.geofabrik.de/europe/turkey-260930.osm.pbf) | 648,624,151 | `3a96f36ccb7beab3016512892ac3e4279f33f2430b19dda0f43d2a55dbf2da66` |
| `beta-us-dc` | [DC 260930](https://download.geofabrik.de/north-america/us/district-of-columbia-260930.osm.pbf) | 21,008,531 | `7a8824953e5259c4fa30fba646651eebd931f464763161c26af2f04f15ad8a6d` |
| `beta-us-md` | [Maryland 260930](https://download.geofabrik.de/north-america/us/maryland-260930.osm.pbf) | 214,847,994 | `c6c95cfc5a98896afa28344c7cecb72b5f1a7226ba7ab74acfa9731f90f592ea` |

Public source aliases are respectively
`https://download.geofabrik.de/europe/turkey-latest.osm.pbf`,
`https://download.geofabrik.de/north-america/us/district-of-columbia-latest.osm.pbf`
and `https://download.geofabrik.de/north-america/us/maryland-latest.osm.pbf`.
Recorded Last-Modified values are 30 September 2026 23:22:39 UTC (Turkey),
1 October 2026 09:24:38 UTC (DC) and 1 October 2026 07:44:54 UTC (Maryland).
Download metadata dates and boundary response dates are not substituted for
PBF byte hashes. Public PBFs omit contributor personal metadata; internal
Geofabrik history/metadata files are not used.

## Intended immutable release URLs — publication gate still open

Edition: `beta-20261001`. These are intended per-source immutable receipt and
catalog locations, not a claim that the assets have passed live HTTPS checks:

| Source | Release tag | Intended catalog |
|---|---|---|
| Istanbul | `beta-20261001-9752309ecc0e1c5109eb` | [catalog](https://github.com/mertseyit3444-web/YatoPath-Maps/releases/download/beta-20261001-9752309ecc0e1c5109eb/catalog.json) |
| DC | `beta-20261001-076b07119b3a14046c1d` | [catalog](https://github.com/mertseyit3444-web/YatoPath-Maps/releases/download/beta-20261001-076b07119b3a14046c1d/catalog.json) |
| Maryland | `beta-20261001-7f7efbdcb8280c594e55` | [catalog](https://github.com/mertseyit3444-web/YatoPath-Maps/releases/download/beta-20261001-7f7efbdcb8280c594e55/catalog.json) |

Each tag also requires `receipt.json` at the same immutable download prefix.
Release collection accepts a source only after its verified receipt binds the
exact source ID, release tag, common beta inventory fingerprint, PBF hash and
catalog hash. Every advertised road/vector asset must pass live HTTPS byte
count, SHA-256 and range checks. A local build or a prepared boundary is not
that evidence. The publisher updates this status only after actual verification;
no incomplete beta is represented as complete world coverage.

The verified Monaco pilot must retain its existing pack identity and hashes
when the beta catalog is collected. Installed data and legacy progress are not
rewritten merely because a new catalog or source ID exists. Real iPhone
download/offline/reopen/GPS/data-upgrade checks remain a separate beta gate.

## Licensing and repository boundary

OSM roads and boundaries: **© OpenStreetMap contributors**, under
[ODbL 1.0](https://www.openstreetmap.org/copyright). Geofabrik distributes the
public extracts; its [DC](https://download.geofabrik.de/north-america/us/district-of-columbia.html)
and [Maryland](https://download.geofabrik.de/north-america/us/maryland.html)
pages explain extract footprints and public-data scope. Basemap extraction
uses the pinned `https://build.protomaps.com/20260929.pmtiles` source; existing
basemap/style/font licenses and attribution must remain with delivered assets.
Map bytes are generated from these sources, not bulk-downloaded from OSM's
public tile server.

Only public map tools/tests, audited public boundary inventory and this
coverage documentation belong here. Private app/backend source, user GPS or
coverage databases, account/signing credentials and unpublished operator/legal
documents are excluded. This repository does not establish App Store,
TestFlight, Watch, cloud-account or full-world acceptance.
