"""Download OpenStreetMap building footprints for Pacific countries (run once).

Source: Geofabrik daily country extracts (GeoPackage, "free" layers),
https://download.geofabrik.de/australia-oceania.html. Data © OpenStreetMap
contributors, ODbL 1.0.

For each country this downloads ``<slug>-latest-free.gpkg.zip`` to
``data/raw/osm/`` (gitignored), keeps only the building polygons and writes
``data/osm-buildings/<slug>.gpkg``. ``processing/buildings.py`` reads those
files for the AOI before falling back to the live Overpass server.

Usage (from the repo root):

    uv run python scripts/download_osm_buildings.py              # all Pacific countries
    uv run python scripts/download_osm_buildings.py fiji samoa   # just these
    uv run python scripts/download_osm_buildings.py --include-nz # also NZ (large; LINZ is better there)

Re-run to refresh with the latest OSM edits (e.g. after a HOT mapping campaign).
"""

from __future__ import annotations

import sys
import zipfile
from datetime import date
from pathlib import Path
from urllib.request import urlopen

import geopandas as gpd
import pyogrio

REPO = Path(__file__).resolve().parents[1]
RAW_DIR = REPO / "data" / "raw" / "osm"
OUT_DIR = REPO / "data" / "osm-buildings"
BASE = "https://download.geofabrik.de/australia-oceania/{slug}-latest-free.gpkg.zip"

# Geofabrik slugs (Australia has no GeoPackage extract; NZ is opt-in).
PACIFIC = [
    "american-oceania",
    "cook-islands",
    "fiji",
    "kiribati",
    "marshall-islands",
    "micronesia",
    "nauru",
    "new-caledonia",
    "niue",
    "palau",
    "papua-new-guinea",
    "pitcairn-islands",
    "polynesie-francaise",
    "samoa",
    "solomon-islands",
    "tokelau",
    "tonga",
    "tuvalu",
    "vanuatu",
    "wallis-et-futuna",
]


def _download(url: str, dest: Path) -> None:
    dest.parent.mkdir(parents=True, exist_ok=True)
    tmp = dest.with_suffix(dest.suffix + ".part")
    with urlopen(url, timeout=120) as resp, open(tmp, "wb") as fh:
        total = int(resp.headers.get("Content-Length") or 0)
        done = 0
        while chunk := resp.read(1 << 20):
            fh.write(chunk)
            done += len(chunk)
            if total:
                print(f"\r  {done / 1e6:6.1f} / {total / 1e6:.1f} MB", end="", flush=True)
    print()
    tmp.replace(dest)


def extract_buildings(slug: str) -> Path:
    """Download one country and write its building polygons to OUT_DIR."""
    zpath = RAW_DIR / f"{slug}-latest-free.gpkg.zip"
    print(f"{slug}: downloading")
    _download(BASE.format(slug=slug), zpath)

    unpacked = RAW_DIR / slug
    with zipfile.ZipFile(zpath) as zf:
        zf.extractall(unpacked)
    gpkgs = sorted(unpacked.rglob("*.gpkg"))
    if not gpkgs:
        raise RuntimeError(f"{slug}: no .gpkg inside {zpath.name}")

    parts = []
    for gpkg in gpkgs:
        for layer_name, geom_type in pyogrio.list_layers(gpkg):
            if "building" in str(layer_name).lower():
                parts.append(gpd.read_file(gpkg, layer=layer_name))
    if not parts:
        raise RuntimeError(f"{slug}: no building layer found in {[g.name for g in gpkgs]}")

    gdf = gpd.GeoDataFrame(pd_concat(parts), crs=parts[0].crs).to_crs("EPSG:4326")
    gdf = gdf[gdf.geometry.geom_type.isin(["Polygon", "MultiPolygon"])]
    rename = {"type": "building", "fclass": "osm_class"}
    gdf = gdf.rename(columns={k: v for k, v in rename.items() if k in gdf.columns})
    keep = [c for c in ("osm_id", "building", "osm_class", "name") if c in gdf.columns]
    gdf = gdf[keep + ["geometry"]].reset_index(drop=True)

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    out = OUT_DIR / f"{slug}.gpkg"
    gdf.to_file(out, layer="buildings", driver="GPKG")
    print(f"{slug}: {len(gdf):,} buildings -> {out.relative_to(REPO)}")
    return out


def pd_concat(parts):
    import pandas as pd

    return pd.concat(parts, ignore_index=True)


def main(argv: list[str]) -> int:
    include_nz = "--include-nz" in argv
    slugs = [a for a in argv if not a.startswith("--")] or list(PACIFIC)
    if include_nz:
        slugs.append("new-zealand")
    failed = []
    for slug in slugs:
        try:
            extract_buildings(slug)
        except Exception as exc:  # noqa: BLE001 — keep going with the other countries
            print(f"{slug}: FAILED ({type(exc).__name__}: {exc})")
            failed.append(slug)
    (OUT_DIR / "SOURCE.txt").write_text(
        "OpenStreetMap building polygons from Geofabrik extracts "
        f"(https://download.geofabrik.de/australia-oceania.html), downloaded {date.today().isoformat()}.\n"
        "Data © OpenStreetMap contributors, ODbL 1.0.\n",
        encoding="utf-8",
    )
    print(f"Done. {len(slugs) - len(failed)} countries ok" + (f", failed: {', '.join(failed)}" if failed else "."))
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
