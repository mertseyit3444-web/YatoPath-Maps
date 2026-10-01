# YatoPath Maps

Public, versioned map data for YatoPath. This repository contains map
distribution information, not the application's source or anyone's activities.

## Initial pilot

The first package covers **Monaco**. This is a limited download pilot;
worldwide city coverage is not yet available. Roads and the full offline
vector basemap are separate downloadable files. The catalog records each
file's byte count and SHA-256 checksum. Vector map styling and fonts are
provided by the application.

Stable catalog: https://github.com/mertseyit3444-web/YatoPath-Maps/releases/latest/download/catalog.json

Release assets use fixed tag URLs. Do not replace assets at an existing URL.
Publish a new version when map data changes. Candidates remain prereleases
until their public HTTPS downloads pass size, checksum and byte-range checks.
Only then may they become the latest release.

## Data attribution and sources

Roads and basemap: Â© [OpenStreetMap contributors](https://www.openstreetmap.org/copyright).
OSM derived databases are available under the
[Open Database License 1.0](https://opendatacommons.org/licenses/odbl/1-0/).
The downloaded SQLite database and PMTiles archive are provided as open map data.
Preserve this attribution when using or redistributing the data.

Basemap processing: [Protomaps](https://docs.protomaps.com/basemaps/downloads),
including Natural Earth public-domain data. The pilot was clipped from
`https://build.protomaps.com/20260929.pmtiles` at maximum zoom 15.
Road source: [Geofabrik Monaco](https://download.geofabrik.de/europe/monaco.html).

Each release includes road/basemap source metadata, the extraction boundary
and attribution. Metadata records the source, boundary and result checksums.
No GPS history, photos, account data or credentials belong in this repository.

## Hosting limits

GitHub Releases currently allows up to 1,000 assets per release and each
asset must be smaller than 2 GiB. YatoPath applies a stricter 2,000,000,000-byte
per-file limit; large regions must be split. This pilot does not guarantee
worldwide production hosting capacity or availability.

See [GitHub release documentation](https://docs.github.com/en/repositories/releasing-projects-on-github/about-releases).

## World production setup

The audited source inventory and public boundaries are retained for future
manual production. No world run is launched by this bootstrap. Prior production
receipts on the former account are historical and do not establish availability
on this repository. The publisher derives its destination from GITHUB_REPOSITORY
in Actions; local build/collect requires YATOPATH_MAP_REPOSITORY or --repository.
No application source, user activity, photos, credentials or signing material
is published here.
