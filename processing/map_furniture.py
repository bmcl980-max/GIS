"""Cartographic furniture for matplotlib maps: alternating scale bar, north arrow.

Shared by the app figure (processing/pipeline.py), the Sentinel-2 figure
(processing/s2_pipeline.py) and the notebooks, so every map uses the same symbols.

- ``add_scale_bar``: ESRI-style alternating black/white bar with tick labels
  (``0  1  2 km``) on a white backing box, sized to a round length.
- ``add_north_arrow``: ESRI-style split arrowhead (left half black, right half
  white) with an "N" above, on a white backing box. Drawn in points, so it keeps
  its shape whatever the panel's aspect ratio.

Both are anchored inside the axes (offset boxes), so they stay put under
constrained layout and never overlap titles.
"""

from __future__ import annotations

from matplotlib.offsetbox import (
    AnchoredOffsetbox,
    AuxTransformBox,
    DrawingArea,
    TextArea,
    VPacker,
)
from matplotlib.patches import Polygon, Rectangle
from matplotlib.text import Text

_NICE_KM = (0.1, 0.2, 0.25, 0.5, 1, 2, 2.5, 5, 10, 20, 25, 50, 100, 200, 500)
_BACKING = {"facecolor": "white", "edgecolor": "0.55", "linewidth": 0.5, "alpha": 0.88}


def nice_scale_km(width_km: float, target_frac: float = 0.25) -> float:
    """Largest round length (km) not longer than ``target_frac`` of the map width."""
    target = max(width_km * target_frac, _NICE_KM[0])
    chosen = _NICE_KM[0]
    for value in _NICE_KM:
        if value <= target:
            chosen = value
    return chosen


def _fmt_km(value: float) -> str:
    return f"{value:g}"


def _style_frame(box: AnchoredOffsetbox) -> None:
    box.patch.set_boxstyle("round,pad=0.25,rounding_size=0.15")
    box.patch.set_facecolor(_BACKING["facecolor"])
    box.patch.set_edgecolor(_BACKING["edgecolor"])
    box.patch.set_linewidth(_BACKING["linewidth"])
    box.patch.set_alpha(_BACKING["alpha"])


def add_scale_bar(
    ax,
    km_per_unit: float,
    loc: str = "lower left",
    segments: int = 4,
    target_frac: float = 0.25,
    fontsize: float = 8,
) -> float:
    """Alternating scale bar on ``ax``; returns its total length in km.

    ``km_per_unit`` = ground km per x data unit at the map centre: km per
    pixel for an ``imshow`` drawn in pixel coordinates, or
    ``111.32 * cos(lat)`` for lon/lat axes.
    """
    x_min, x_max = sorted(ax.get_xlim())
    y_lo, y_hi = ax.get_ylim()
    y_up = 1.0 if y_hi >= y_lo else -1.0  # imshow pixel axes run downwards
    y_span = abs(y_hi - y_lo)

    width_km = (x_max - x_min) * km_per_unit
    total_km = nice_scale_km(width_km, target_frac)
    if total_km / segments < _NICE_KM[0] / 2:
        segments = 2
    seg_units = total_km / segments / km_per_unit
    bar_h = 0.014 * y_span * y_up

    box = AuxTransformBox(ax.transData)
    for i in range(segments):
        box.add_artist(
            Rectangle(
                (i * seg_units, 0.0),
                seg_units,
                bar_h,
                facecolor="black" if i % 2 == 0 else "white",
                edgecolor="black",
                linewidth=0.8,
            )
        )
    label_y = bar_h * 1.9
    ticks = [0, segments // 2, segments] if segments >= 4 else [0, segments]
    for i in ticks:
        km = i * total_km / segments
        text = _fmt_km(km) + (" km" if i == segments else "")
        box.add_artist(
            Text(
                i * seg_units,
                label_y,
                text,
                ha="center",
                va="bottom" if y_up > 0 else "top",
                fontsize=fontsize,
                color="black",
            )
        )

    anchored = AnchoredOffsetbox(loc=loc, child=box, pad=0.45, borderpad=0.7, frameon=True)
    _style_frame(anchored)
    anchored.set_zorder(10)
    ax.add_artist(anchored)
    return total_km


def add_north_arrow(ax, loc: str = "upper right", size: float = 26.0) -> None:
    """ESRI-style north arrow: split arrowhead (black left, white right) under an "N".

    Assumes north is up (true for lon/lat axes and for north-up EE thumbnails).
    ``size`` is the arrow height in points.
    """
    w = size * 0.6
    head = DrawingArea(w, size, 0, 0)
    mid, notch = w / 2.0, size * 0.28
    head.add_artist(
        Polygon([(mid, size), (0, 0), (mid, notch)], closed=True, facecolor="black", edgecolor="black", linewidth=0.8)
    )
    head.add_artist(
        Polygon([(mid, size), (w, 0), (mid, notch)], closed=True, facecolor="white", edgecolor="black", linewidth=0.8)
    )
    label = TextArea("N", textprops={"fontsize": 10, "fontweight": "bold", "color": "black"})
    pack = VPacker(children=[label, head], align="center", pad=0, sep=1)
    anchored = AnchoredOffsetbox(loc=loc, child=pack, pad=0.35, borderpad=0.7, frameon=True)
    _style_frame(anchored)
    anchored.set_zorder(10)
    ax.add_artist(anchored)
