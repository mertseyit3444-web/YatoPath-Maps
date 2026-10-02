# YatoPath first beta map coverage

Prepared 1 October 2026; map publication checked 2 October 2026 UTC.
This is a **three-region beta**, with Istanbul
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

## Published map releases — limited beta publication gate passed

Edition: `beta-20261001`. All three selected source releases have verified
receipts and completed live HTTPS byte-count, SHA-256 and HTTP 206 range checks.
The combined catalog contains **8 packs**: Istanbul (1), Washington DC (1),
Maryland (5), and the unchanged Monaco pilot (1). This completes publication
for the selected beta scope; it does not establish full-world or phone acceptance.

| Source | Release tag | Published catalog |
|---|---|---|
| Istanbul | `beta-20261001-9752309ecc0e1c5109eb` | [catalog](https://github.com/mertseyit3444-web/YatoPath-Maps/releases/download/beta-20261001-9752309ecc0e1c5109eb/catalog.json) |
| DC | `beta-20261001-076b07119b3a14046c1d` | [catalog](https://github.com/mertseyit3444-web/YatoPath-Maps/releases/download/beta-20261001-076b07119b3a14046c1d/catalog.json) |
| Maryland | `beta-20261001-7f7efbdcb8280c594e55` | [catalog](https://github.com/mertseyit3444-web/YatoPath-Maps/releases/download/beta-20261001-7f7efbdcb8280c594e55/catalog.json) |

Each tag has `receipt.json` at the same immutable download prefix. The verified
receipts bind exact source IDs, release tags, the common beta inventory
fingerprint, PBF hashes and regional catalog hashes. Maryland's five road and
five vector assets passed those live checks; its regional catalog SHA-256 is
`96cc7753742818f232e461f209deb48c11652ee7eaaf89b6494ed755f2419e38`.

The immutable combined release is
[`beta-20261001-catalog-1`](https://github.com/mertseyit3444-web/YatoPath-Maps/releases/tag/beta-20261001-catalog-1).
Its [catalog](https://github.com/mertseyit3444-web/YatoPath-Maps/releases/download/beta-20261001-catalog-1/catalog.json)
SHA-256 is
`23268eb56f43cfb355153d312d36a70b266717476d7f537a8c2780c485f53137`.
Its [verified receipt index](https://github.com/mertseyit3444-web/YatoPath-Maps/releases/download/beta-20261001-catalog-1/verified-receipts.json)
SHA-256 is
`80e718d8235ca557e7a40560e59b311b5128e6c17a8c78e8a628083f67c805b6`.
The [current app catalog](https://github.com/mertseyit3444-web/YatoPath-Maps/releases/latest/download/catalog.json)
was checked against those exact combined-catalog bytes.

Monaco's `mc-monaco` identity and existing asset hashes are preserved.
Real iPhone download, offline reopen, GPS and existing-data upgrade checks
remain pending. Simulator checks and published map files do not complete
that device gate or establish TestFlight/App Store acceptance.

## Maryland's five download parts

The current catalog gives all five parts the same display name,
`Maryland · Geofabrik full extract`. Each part covers only its own partition.
Search by the **exact part ID** below to select one unambiguously. City names
are verified search aliases from the finalized road metadata; they are useful
search guides, not a claim that an entire city's administrative area lies in
one part.

| Exact part ID to search | City aliases |
|---|---|
| `us-maryland-p0` | Cumberland, Frederick, Hagerstown, Germantown |
| `us-maryland-p10` | Waldorf, Salisbury, Ocean City, Cambridge |
| `us-maryland-p1100` | Bethesda, Silver Spring, Annapolis, Rockville |
| `us-maryland-p1101` | Baltimore, Towson, Westminster, Ellicott City |
| `us-maryland-p111` | Aberdeen, Elkton, Chestertown, Bel Air |

Download all five parts for the complete Maryland provider footprint; their
combined road/vector download is 1,067,453,238 bytes (about 1.067 GB).
Android currently opens one selected part. Select the relevant downloaded
part when changing areas; downloading every part does not provide automatic
part switching. List position or a rounded download size is not a reliable
way to identify a part.

Final partition checks found no area overlap or omitted child footprint.
The raw provider/leaf symmetric difference was
`3.1675068113117683e-15` square degrees, a floating-point geometry difference;
Hausdorff distance was zero, and coverage passed at a `1e-10` degree tolerance.
The existing minimum-length clipping rule filtered 45 pieces shorter than
0.5 metres, totalling about 8.61 metres across split boundaries. Retained
split pieces matched their actual child records. This is not a claim of
complete OSM data or an independent Maryland administrative-boundary audit.

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

This repository hosts public map tools/tests, the audited public boundary
inventory and map coverage documentation. It does not establish App Store,
TestFlight, Watch, cloud-account, real-phone or full-world acceptance.
