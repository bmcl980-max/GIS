"""HTML flood **analysis** from the comparison figure and area stats.

Uses Google Gemini. The API key is loaded from the repo-root ``.env``
(``GEMINI_KEY`` or ``GOOGLE_API_KEY``). This module is independent of
the notebooks; call it from a notebook cell or from a backend UI.
"""

from __future__ import annotations

import base64
import io
import json
import os
import re
from contextlib import contextmanager
from pathlib import Path
from typing import Any

from dotenv import load_dotenv

_REPO_ROOT = Path(__file__).resolve().parents[1]
load_dotenv(_REPO_ROOT / ".env")

DEFAULT_MODEL = os.environ.get("GEMINI_MODEL", "gemini-2.5-flash")

_SYSTEM = """You are an analyst for a GEOG761 HA-DR (humanitarian assistance /
disaster relief) product, FloodSat Pacific (team Geographically Informed Speculators).

Task: ANALYSE the attached flood figure together with the stats/meta JSON.
Do not write a caption-style description of “what the panels show”. Use the
image and the numbers as evidence: compare, interpret, and judge what the
mapping implies for a partner choosing a logistics or beach-landing site.

How the map was made (use this to interpret, not to recap as a methods essay):
- Event Sentinel-1 GRD IW VV+VH (~10 m); WI = VV_dB + VH_dB;
  ΔWI = historical mean WI − event WI (lookback years ending at event start).
- Flood cluster is an unsupervised 2-D GMM on (WI, ΔWI): the component named
  by physics (lowest WI among components with positive mean ΔWI), not a
  hand-set dB cutoff (dark and newly darker). meta.gmm_flood_id /
  meta.gmm_components give the chosen component and the full table;
  meta.kmeans_flood_id / meta.kmeans_components are the same samples fit with
  k-means, reported only as a comparison, not used to build the flood raster.
- Permanent water removed afterwards (WorldCover 80, JRC occurrence, DEM nodata
  sea). WorldCover/DEM are post-masks, not GMM features.
- Low/flat ground from FABDEM (bare earth), not a DSM with buildings/trees.
- Optional third panel: LINZ building outlines intersecting final_flood.
- Road network from OSM; edges tagged bridge/tunnel are skipped from the
  ground-road analysis (osm_bridge_tunnel_skipped). A road is "flood affected"
  when its flooded length is >= 50 m or >= 30% of the edge; this is
  exposure, not a confirmed closure. Never call these roads "closed" or
  "likely closed"; say "flood affected". "AOI detour" means an alternate
  ground route exists within the AOI, as opposed to no in-AOI alternative.

Analysis requirements:
- Resolve lat/lon in meta to a named place (Hawke’s Bay, etc.) when the
  coordinates allow; do not invent a place if they do not.
- Read the figure: dark SAR vs WI colour, where yellow flood sits, whether
  buildings (if present) cluster on flood or on the edge. Tie each claim to
  a number in stats or a visible pattern in the image.
- Treat stats as a funnel. Analyse shrinkage (event water → change mask →
  candidate → terrain/MMU → final). Say what a large drop at a step means
  (permanent water vs slope vs MMU), using only keys that exist in JSON.
- If building counts exist (buildings_affected, buildings_in_aoi, footprint),
  interpret exposure (share of AOI buildings, spatial concentration). These
  are roof outlines from imagery, not households or occupancy.
- If road/network keys exist (roads_in_aoi_km, roads_flooded_km,
  roads_flooded_area_km2, osm_edges_in_aoi, osm_ground_edges,
  osm_bridge_tunnel_skipped, roads_flood_affected_edges,
  roads_flood_affected_names, roads_with_aoi_detour, roads_no_aoi_detour,
  roads_flood_affected_name_list, roads_detours): interpret network
  exposure, not just area. Report what share of the road network is flooded
  (km and % of roads_in_aoi_km), how many distinct named roads are flood
  affected versus how many edges, and what the split between
  roads_with_aoi_detour and roads_no_aoi_detour implies for access.
  Include an HTML table of roads_detours (do not invent rows). Columns:
  flood affected road name, flooded length (m), detour status, detour_route
  (named streets in order, joined with arrows), extra_length_m (extra
  metres vs the flooded edge). Use detour_route as the actual in-AOI
  alternative path; empty detour_route with status "no alternative in AOI"
  means the AOI graph has no bypass. "not computed (cap)" means the
  search was skipped. Note that bridge/tunnel edges are excluded, so
  flood-affected roads may be undercounted where the real cut is a bridge.
  If meta.osm_road_error is set, OSM download failed: quote that string as
  the reason the road panel is empty. Do not invent a generic phrase such
  as "external service error". Do not invent road km, names, or detour
  rows when osm_road_error is set.
- Discuss HA-DR implications: which parts of the AOI look inundated vs
  usable for access; which routes into/out of the flooded area remain open;
  where road and building exposure overlap versus where they diverge
  (e.g. buildings dry but access roads cut); what the numbers do and do
  not support.
- Limitations as they affect THIS analysis: speckle, lookback composite vs
  pair, WorldCover 2021, layover/shadow proxy, FABDEM residual error,
  building-outline lag, OSM completeness/currency, and flood-affected roads
  inferred from geometry intersection rather than confirmed road-condition
  reports.
  Do not list them as a boilerplate dump.

Rules:
- Complete HTML only (html, head, body). No Markdown. No code fences.
- <style> with readable typography (max-width article, tables).
- Image placeholder exactly once: <img src="{{FIGURE}}" alt="Flood analysis figure">
- Sections (<h1>/<h2>): always include Question and Key Flood Statistics
  (a compact table of the JSON numbers that exist, grouped by area/water,
  buildings, and roads where applicable). Then include ONLY the extra
  sections listed in the user message. Omit any section that is not listed.
  Do not invent a Methods dump or a Results caption.
- Use only numbers in the stats/meta JSON. Do not invent areas, counts,
  road names, or dates.
- Prefer argument over inventory: every paragraph should answer “so what?”.
- No RAG. No Python. No API keys.
"""


def figure_to_png_bytes(figure: Any) -> bytes:
    """Accept a matplotlib Figure, PNG path, raw bytes, or base64 string."""
    if figure is None:
        raise ValueError("figure is required (matplotlib Figure, PNG path, bytes, or base64).")
    if isinstance(figure, (bytes, bytearray)):
        return bytes(figure)
    if isinstance(figure, str):
        try:
            is_path = Path(figure).is_file()
        except OSError:
            is_path = False
        if is_path:
            return Path(figure).read_bytes()
        return base64.b64decode(figure)
    if isinstance(figure, Path):
        return figure.read_bytes()
    if hasattr(figure, "save") and not hasattr(figure, "savefig"):
        buf = io.BytesIO()
        figure.save(buf, format="PNG")
        return buf.getvalue()
    if hasattr(figure, "savefig"):
        buf = io.BytesIO()
        figure.savefig(buf, format="png", dpi=120, bbox_inches="tight")
        return buf.getvalue()
    raise TypeError(f"Unsupported figure type: {type(figure)!r}")


_ADC_ENV = (
    "GOOGLE_APPLICATION_CREDENTIALS",
    "GOOGLE_GENAI_USE_VERTEXAI",
    "GOOGLE_CLOUD_PROJECT",
    "GCLOUD_PROJECT",
    "GOOGLE_CLOUD_LOCATION",
)


@contextmanager
def _gemini_api_key_only():
    """Use GEMINI_KEY and hide Earth Engine ADC for the Gemini HTTP call."""
    key = (os.environ.get("GEMINI_KEY") or os.environ.get("GOOGLE_API_KEY") or "").strip()
    if not key:
        raise RuntimeError(
            "Missing API key. Set GEMINI_KEY (or GOOGLE_API_KEY) in the repo-root .env."
        )
    saved = {name: os.environ.pop(name) for name in _ADC_ENV if name in os.environ}
    os.environ["GEMINI_API_KEY"] = key
    try:
        yield key
    finally:
        for name, value in saved.items():
            os.environ[name] = value


def _client(api_key: str):
    from google import genai

    return genai.Client(api_key=api_key, vertexai=False)


def _strip_fences(text: str) -> str:
    """Drop ```html / ``` wrappers if the model adds them."""
    stripped = text.strip()
    if stripped.startswith("```"):
        stripped = stripped.split("\n", 1)[-1]
        if stripped.endswith("```"):
            stripped = stripped[: stripped.rfind("```")].rstrip()
    return stripped.strip()


def _embed_figure(html: str, png: bytes) -> str:
    """Replace {{FIGURE}} or insert a data-URI image of the comparison figure."""
    uri = "data:image/png;base64," + base64.b64encode(png).decode("ascii")
    img = f'<img src="{uri}" alt="Flood comparison figure" style="max-width:100%;height:auto;">'
    if "{{FIGURE}}" in html:
        return html.replace("{{FIGURE}}", uri)
    match = re.search(r"<body[^>]*>", html, flags=re.IGNORECASE)
    if match:
        i = match.end()
        return html[:i] + "\n" + img + html[i:]
    return img + "\n" + html


def generate_flood_report(
    stats: dict[str, Any],
    figure: Any,
    meta: dict[str, Any] | None = None,
    *,
    model: str | None = None,
    max_tokens: int = 8192,
    include_analysis: bool = False,
    include_implications: bool = False,
    include_uncertainties: bool = False,
    include_conclusion: bool = False,
) -> str:
    """Ask Gemini to analyse the flood figure and area statistics.

    Extra narrative sections follow the REPORT_* flags (layer_config / defaults).
    Returns a complete HTML document (figure embedded as a data URI).
    Does not print or log the API key.
    """
    from google.genai import types

    extra = []
    if include_analysis:
        extra.append("Analysis")
    if include_implications:
        extra.append("Implications")
    if include_uncertainties:
        extra.append("Uncertainties")
    if include_conclusion:
        extra.append("Conclusion")
    extra_line = (
        "Also write these extra sections: " + ", ".join(extra) + "."
        if extra
        else "Do not write Analysis, Implications, Uncertainties, or Conclusion."
    )

    png = figure_to_png_bytes(figure)
    payload = {"stats": stats, "meta": meta or {}}
    user_text = (
        "Analyse this figure with the JSON stats and metadata. Argue from the "
        "evidence; do not merely describe the panels or restate the table. "
        "Always include Question and a Key Flood Statistics table. "
        f"{extra_line} Output a complete HTML document only.\n\n"
        f"```json\n{json.dumps(payload, indent=2, default=str)}\n```"
    )
    config = types.GenerateContentConfig(
        system_instruction=_SYSTEM,
        max_output_tokens=max_tokens,
        automatic_function_calling=types.AutomaticFunctionCallingConfig(disable=True),
    )
    with _gemini_api_key_only() as api_key:
        # Keep `client` referenced for the whole call: google-genai's Client
        # closes its underlying HTTP client on GC, and an inline chained call
        # (``_client(api_key).models.generate_content(...)``) can let that
        # happen before the request is sent, raising "client has been closed".
        client = _client(api_key)
        response = client.models.generate_content(
            model=model or DEFAULT_MODEL,
            contents=[
                types.Part.from_bytes(data=png, mime_type="image/png"),
                user_text,
            ],
            config=config,
        )
    html = _strip_fences(response.text or "")
    return _embed_figure(html, png)


def html_as_iframe(html: str, *, height: str = "85vh") -> str:
    """Wrap a full HTML document so its CSS cannot restyle the notebook.

    Jupyter / VS Code injects ``display(HTML(...))`` into the page. Unscoped
    rules such as ``body``, ``h1``, ``p`` then hide or restyle markdown cells.
    An iframe with ``srcdoc`` keeps the report isolated.
    """
    srcdoc = html.replace("&", "&amp;").replace('"', "&quot;")
    return (
        f'<iframe srcdoc="{srcdoc}" '
        f'style="width:100%;height:{height};border:1px solid #ccc;background:#fff;" '
        'sandbox="allow-same-origin"></iframe>'
    )

