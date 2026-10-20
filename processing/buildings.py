"""Building footprints ∩ flood polygons.

Sources (``BUILDING_SOURCE`` in processing/defaults.py / notebooks/layer_config.py):

- ``"auto"`` (default): LINZ NZ Building Outlines if the local file exists;
  otherwise OpenStreetMap buildings, first from local Geofabrik extracts in
  ``data/osm-buildings/`` (``scripts/download_osm_buildings.py``), then from the
  live Overpass server if no local file has buildings in the AOI.
- ``"linz"``: LINZ only. ``"osm"``: OSM only. ``"none"``: skip building exposure.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import geopandas as gpd
from shapely.geometry import box, shape

NZTM = "EPSG:2193"
WGS84 = "EPSG:4326"

_REPO = Path(__file__).resolve().parents[1]
_DEFAULT_PATHS = (
    "data/nz-building/nz-building-outlines.shp",
    "data/nz-bulding/nz-building-outlines.shp",
)


def resolve_building_path(configured: str | None = None) -> Path | None:
    """Return the first existing shapefile / GeoPackage path."""
    candidates: list[Path] = []
    if configured:
        p = Path(configured)
        candidates.append(p if p.is_absolute() else _REPO / p)
    candidates.extend(_REPO / rel for rel in _DEFAULT_PATHS)
    for path in candidates:
        if path.exists():
            return path
    return None


BUILDING_SOURCES = ("auto", "linz", "osm", "none")
OSM_EXTRACT_DIR = _REPO / "data" / "osm-buildings"


def load_local_osm_buildings(bounds: list[float]) -> gpd.GeoDataFrame | None:
    """Buildings from local OSM extracts that fall inside the AOI, or None.

    Every ``data/osm-buildings/*.gpkg`` is read with a bbox filter (spatial
    index, so cheap). Returns None when no file has buildings in the AOI, so the
    caller can try the live server. File extents are not used to decide coverage
    because countries spanning the antimeridian (Fiji, Kiribati, ...) have
    world-wide bounding boxes.
    """
    import pandas as pd

    files = sorted(OSM_EXTRACT_DIR.glob("*.gpkg"))
    if not files:
        return None
    west, south, east, north = bounds
    parts = []
    for path in files:
        gdf = gpd.read_file(path, bbox=(west, south, east, north))
        if not gdf.empty:
            parts.append(gdf.to_crs(WGS84))
    if not parts:
        return None
    gdf = gpd.GeoDataFrame(pd.concat(parts, ignore_index=True), geometry="geometry", crs=WGS84)
    return gdf[gdf.intersects(box(west, south, east, north))].reset_index(drop=True)


def fetch_osm_buildings(bounds: list[float]) -> gpd.GeoDataFrame:
    """OSM ``building=*`` polygons inside [west, south, east, north] (WGS84).

    Uses the same Overpass mirrors and osmnx settings as the road network.
    An AOI with no mapped buildings returns an empty GeoDataFrame.
    """
    import osmnx as ox

    from processing.roads import overpass_call

    west, south, east, north = bounds
    tags = {"building": True}

    def _download():
        try:
            return ox.features_from_bbox((west, south, east, north), tags=tags)
        except TypeError:  # osmnx 1.x signature
            return ox.features_from_bbox(north, south, east, west, tags=tags)

    try:
        gdf = overpass_call(_download, "buildings")
    except Exception as exc:  # noqa: BLE001
        if type(exc).__name__ == "InsufficientResponseError":
            return gpd.GeoDataFrame(geometry=[], crs=WGS84)
        raise

    gdf = gdf[gdf.geometry.geom_type.isin(["Polygon", "MultiPolygon"])]
    gdf = gdf.reset_index()
    if "id" in gdf.columns:
        gdf = gdf.rename(columns={"id": "osm_id"})
    keep = [c for c in ("osm_id", "building", "name", "building:levels", "amenity") if c in gdf.columns]
    gdf = gpd.GeoDataFrame(gdf[keep + ["geometry"]], geometry="geometry", crs=gdf.crs or WGS84).to_crs(WGS84)
    return gdf[gdf.intersects(box(west, south, east, north))].reset_index(drop=True)


def load_buildings(
    bounds: list[float],
    configured_path: str | None = None,
    source: str = "auto",
) -> tuple[gpd.GeoDataFrame | None, str, str | None]:
    """Load footprints for the AOI from the configured source.

    Returns ``(buildings or None, source_label, note)``. ``source_label`` is
    "LINZ", "OSM" or "none"; ``note`` explains why nothing was loaded.
    """
    source = (source or "auto").lower()
    if source not in BUILDING_SOURCES:
        raise ValueError(f"BUILDING_SOURCE must be one of {BUILDING_SOURCES}, got {source!r}")
    if source == "none":
        return None, "none", "Building exposure switched off (BUILDING_SOURCE = 'none')."
    if source in ("auto", "linz"):
        path = resolve_building_path(configured_path)
        if path is not None:
            return load_buildings_in_bounds(path, bounds).reset_index(drop=True), "LINZ", None
        if source == "linz":
            return None, "LINZ", "LINZ building outlines not found (download to data/nz-building/, see README)."
    local = load_local_osm_buildings(bounds)
    if local is not None:
        return local, "OSM", None
    try:
        return fetch_osm_buildings(bounds), "OSM", None
    except Exception as exc:  # noqa: BLE001 — Overpass must not fail the flood run
        from processing.roads import short_osm_error

        return None, "OSM", f"OSM buildings unavailable.\n{short_osm_error(exc)}"


def fc_payload_to_gdf(payload: dict | list | None) -> gpd.GeoDataFrame:
    """Build a GeoDataFrame from an Earth Engine FeatureCollection getInfo() dict."""
    if isinstance(payload, list):
        payload = {"features": payload}
    feats = (payload or {}).get("features") or []
    if not feats:
        return gpd.GeoDataFrame(geometry=[], crs=WGS84)
    rows = []
    geoms = []
    for feat in feats:
        props = dict(feat.get("properties") or {})
        geom = feat.get("geometry")
        if not geom:
            continue
        geoms.append(shape(geom))
        rows.append(props)
    return gpd.GeoDataFrame(rows, geometry=geoms, crs=WGS84)


def ee_fc_to_gdf(fc) -> gpd.GeoDataFrame:
    """Convert a small Earth Engine FeatureCollection via getInfo()."""
    return fc_payload_to_gdf(fc.getInfo())


def load_buildings_in_bounds(path: Path, bounds: list[float]) -> gpd.GeoDataFrame:
    """Load LINZ outlines clipped to [west, south, east, north] in WGS84."""
    west, south, east, north = bounds
    bbox = (west, south, east, north)
    gdf = gpd.read_file(path, bbox=bbox)
    if gdf.empty:
        return gdf.set_crs(WGS84) if gdf.crs is None else gdf
    if gdf.crs is None:
        gdf = gdf.set_crs(WGS84)
    else:
        gdf = gdf.to_crs(WGS84)
    aoi_poly = box(west, south, east, north)
    return gdf[gdf.intersects(aoi_poly)].copy()


def intersect_buildings_with_flood(
    buildings: gpd.GeoDataFrame,
    flood: gpd.GeoDataFrame,
) -> tuple[gpd.GeoDataFrame, dict[str, Any]]:
    """Buildings that intersect flood polygons; footprint area in the local UTM zone."""
    empty_stats = {
        "buildings_in_aoi": 0,
        "buildings_affected": 0,
        "buildings_affected_pct": 0.0,
        "buildings_affected_footprint_km2": 0.0,
    }
    if buildings.empty:
        return buildings.copy(), empty_stats
    n_aoi = int(len(buildings))
    empty_stats["buildings_in_aoi"] = n_aoi
    if flood.empty:
        out = buildings.copy()
        out["flood_affected"] = False
        return out.iloc[0:0], empty_stats

    b4326 = buildings.to_crs(WGS84)
    f4326 = flood.to_crs(WGS84)
    hit = gpd.sjoin(b4326, f4326[["geometry"]], how="inner", predicate="intersects")
    if "building_i" in hit.columns:
        hit = hit.drop_duplicates(subset=["building_i"])
    else:
        hit = hit[~hit.index.duplicated(keep="first")]
    hit = hit.drop(columns=[c for c in hit.columns if c == "index_right"], errors="ignore")
    n_hit = int(len(hit))
    footprint_km2 = 0.0
    if n_hit:
        metric = b4326.estimate_utm_crs() or NZTM
        flood_m = f4326.to_crs(metric).geometry
        flood_union = flood_m.union_all() if hasattr(flood_m, "union_all") else flood_m.unary_union
        if flood_union is not None and not flood_union.is_empty:
            footprint_km2 = float(hit.to_crs(metric).geometry.intersection(flood_union).area.sum() / 1e6)
    stats = {
        "buildings_in_aoi": n_aoi,
        "buildings_affected": n_hit,
        "buildings_affected_pct": 100.0 * n_hit / n_aoi if n_aoi else 0.0,
        "buildings_affected_footprint_km2": footprint_km2,
    }
    hit["flood_affected"] = True
    return hit, stats


def draw_buildings_panel(
    ax,
    bounds: list[float] | None,
    flood: gpd.GeoDataFrame | None,
    buildings_aoi: gpd.GeoDataFrame | None,
    buildings_affected: gpd.GeoDataFrame | None,
    source_label: str = "LINZ",
) -> None:
    """Map-style panel: flood fill + building outlines (affected in red)."""
    from matplotlib.patches import Patch

    if bounds is None:
        parts = [
            g
            for g in (flood, buildings_aoi, buildings_affected)
            if g is not None and not g.empty
        ]
        if not parts:
            ax.set_axis_off()
            ax.text(0.5, 0.5, f"{source_label} buildings not available", ha="center", va="center")
            return
        west, south, east, north = parts[0].to_crs(WGS84).total_bounds
        for g in parts[1:]:
            minx, miny, maxx, maxy = g.to_crs(WGS84).total_bounds
            west, south = min(west, minx), min(south, miny)
            east, north = max(east, maxx), max(north, maxy)
    else:
        west, south, east, north = bounds
    if flood is not None and not flood.empty:
        flood.to_crs(WGS84).plot(
            ax=ax, facecolor="#ffff00", edgecolor="none", alpha=0.45, zorder=1
        )
    if buildings_aoi is not None and not buildings_aoi.empty:
        buildings_aoi.to_crs(WGS84).plot(
            ax=ax,
            facecolor="none",
            edgecolor="#888888",
            linewidth=0.25,
            zorder=2,
        )
    if buildings_affected is not None and not buildings_affected.empty:
        buildings_affected.to_crs(WGS84).plot(
            ax=ax,
            facecolor="#d73027",
            edgecolor="#7f0000",
            linewidth=0.35,
            alpha=0.75,
            zorder=3,
        )
    ax.set_xlim(west, east)
    ax.set_ylim(south, north)
    ax.set_aspect("equal", adjustable="box")
    ax.set_axis_off()
    n = 0 if buildings_affected is None or buildings_affected.empty else len(buildings_affected)
    ax.set_title(f"{source_label} building outlines ∩ flood\n{n:,} buildings affected")
    if source_label == "OSM":
        ax.text(0.01, 0.01, "© OpenStreetMap contributors", transform=ax.transAxes, fontsize=6, color="0.3")
    ax.legend(
        handles=[
            Patch(facecolor="#ffff00", edgecolor="0.2", linewidth=0.4, label="Final flood"),
            Patch(facecolor="#d73027", edgecolor="#7f0000", linewidth=0.4, label="Affected building"),
            Patch(facecolor="none", edgecolor="#888888", linewidth=0.8, label="Building in AOI"),
        ],
        loc="lower right",
        fontsize=7,
        framealpha=0.92,
    )
