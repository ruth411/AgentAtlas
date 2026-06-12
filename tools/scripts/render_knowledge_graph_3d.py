"""Render the bulk Ayiru catalog as a single-file 3D force graph.

Open the output HTML in any browser. Drag to pan, scroll to zoom,
right-drag to spin. The graph uses Vasturiano's `3d-force-graph`
(loaded from CDN — no build step).

Improvements over the older `graph_view_3d.py`:
  - Tools coloured by FAMILY (gh-*, git-*, docker-*, …) so the
    five-surface decomposition shows up as a coherent cluster.
  - Source-host nodes (e.g. `cli.github.com`) get a hue per family
    too, so you can spot which families share which docs origins.
  - DB and output paths are CLI-overridable for sanity / testing.

Usage:
    python tools/scripts/render_knowledge_graph_3d.py
    python tools/scripts/render_knowledge_graph_3d.py --db backend/ayiru.db --out /tmp/g.html
"""

from __future__ import annotations

import argparse
import colorsys
import hashlib
import json
import sqlite3
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_DB = REPO_ROOT / "backend" / "ayiru_v0.2_bulk.db"
DEFAULT_OUT = REPO_ROOT / "ayiru_graph_3d.html"

_STATUS_COLOR = {
    "accepted": "#4ade80",
    "pending": "#fbbf24",
    "rejected": "#ef4444",
    "conflict_detected": "#f97316",
    "requires_human_review": "#a78bfa",
}


def family_of(tool_id: str) -> str:
    """Strip the surface suffix to get the family: gh-cli → gh, postgres-psql → postgres."""
    return tool_id.split("-", 1)[0]


def family_color(family: str) -> str:
    """Deterministic pastel-ish HSL → RGB hex from the family name.

    Hashing keeps `gh` always the same color across re-renders even if
    new families land. The lightness / saturation is fixed so colors
    stay readable on the dark background.
    """
    digest = hashlib.sha1(family.encode("utf-8")).digest()
    hue = digest[0] / 255.0
    r, g, b = colorsys.hls_to_rgb(hue, 0.6, 0.55)
    return f"#{int(r * 255):02x}{int(g * 255):02x}{int(b * 255):02x}"


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", type=Path, default=DEFAULT_DB)
    parser.add_argument("--out", type=Path, default=DEFAULT_OUT)
    args = parser.parse_args()

    if not args.db.is_file():
        raise SystemExit(f"DB not found: {args.db}")

    conn = sqlite3.connect(f"file:{args.db}?mode=ro", uri=True)
    nodes: list[dict] = []
    links: list[dict] = []
    seen: set[str] = set()

    tool_ids = [
        row[0]
        for row in conn.execute(
            "SELECT DISTINCT tool_id FROM knowledge_claims ORDER BY tool_id"
        )
    ]
    families = sorted({family_of(t) for t in tool_ids})
    color_for_tool = {t: family_color(family_of(t)) for t in tool_ids}

    for tool_id in tool_ids:
        nid = f"tool:{tool_id}"
        nodes.append(
            {
                "id": nid,
                "label": tool_id,
                "tooltip": f"family: {family_of(tool_id)}",
                "group": "tool",
                "color": color_for_tool[tool_id],
                "val": 18,
            }
        )
        seen.add(nid)

    for claim_id, tool_id, subject, statement, status, conf in conn.execute(
        "SELECT claim_id, tool_id, subject, statement, verification_status, confidence "
        "FROM knowledge_claims"
    ):
        cid = f"claim:{claim_id}"
        nodes.append(
            {
                "id": cid,
                "label": (subject or "")[:80],
                "tooltip": (statement or "")[:280].replace("\n", " "),
                "group": "claim",
                "color": _STATUS_COLOR.get(status, "#9ca3af"),
                "val": 3 + (float(conf or 0) * 4),
            }
        )
        links.append({"source": f"tool:{tool_id}", "target": cid})
        seen.add(cid)

    # Sources deduped by URL; coloured by the family of the first claim
    # that cited them (most sources only have one family anyway).
    src_family: dict[str, str] = {}
    for claim_id, src in conn.execute("SELECT claim_id, source_uri FROM evidence"):
        sid = f"src:{src}"
        # Look up the citing claim's tool_id once for color.
        if sid not in src_family:
            row = conn.execute(
                "SELECT tool_id FROM knowledge_claims WHERE claim_id = ?",
                (claim_id,),
            ).fetchone()
            src_family[sid] = family_of(row[0]) if row else ""
        if sid not in seen:
            host = src.split("/")[2] if "://" in src else src[:40]
            color = family_color(src_family[sid]) if src_family[sid] else "#94a3b8"
            # Sources get a much darker version of the family color so
            # they don't visually compete with tool nodes.
            nodes.append(
                {
                    "id": sid,
                    "label": host,
                    "tooltip": src,
                    "group": "source",
                    "color": color,
                    "val": 2,
                }
            )
            seen.add(sid)
        links.append({"source": f"claim:{claim_id}", "target": sid})

    conn.close()

    family_legend = "".join(
        f'<div><span class="swatch" style="background:{family_color(f)}"></span>{f}</div>'
        for f in families
    )

    data = {"nodes": nodes, "links": links}
    html = """<!doctype html>
<html><head><meta charset="utf-8"><title>Ayiru knowledge graph (3D)</title>
<style>
  html, body { margin: 0; height: 100%; background: #0b1020; color: #cbd5e1; font-family: -apple-system, system-ui, sans-serif; }
  #legend { position: fixed; top: 12px; left: 12px; background: rgba(11,16,32,0.88); padding: 12px 14px; border: 1px solid #1e293b; border-radius: 8px; font-size: 12px; line-height: 1.6; z-index: 10; max-height: 80vh; overflow-y: auto; max-width: 220px; }
  #legend h4 { margin: 8px 0 4px; font-size: 11px; text-transform: uppercase; color: #94a3b8; letter-spacing: 0.5px; }
  #legend .swatch { display: inline-block; width: 10px; height: 10px; border-radius: 50%; margin-right: 6px; vertical-align: middle; }
  #stats { position: fixed; bottom: 12px; left: 12px; font-size: 11px; color: #64748b; z-index: 10; }
  #search { position: fixed; top: 12px; right: 12px; padding: 6px 10px; background: #0f172a; color: #e2e8f0; border: 1px solid #334155; border-radius: 6px; font-size: 13px; width: 220px; z-index: 10; }
</style>
</head><body>
<div id="legend">
  <div style="font-weight:600;font-size:13px">Ayiru knowledge graph</div>
  <h4>Claim status</h4>
  <div><span class="swatch" style="background:#4ade80"></span>accepted</div>
  <div><span class="swatch" style="background:#fbbf24"></span>pending</div>
  <div><span class="swatch" style="background:#ef4444"></span>rejected</div>
  <div><span class="swatch" style="background:#a78bfa"></span>requires human review</div>
  <h4>Tool families (__NFAMS__)</h4>
  __FAMILYLEG__
</div>
<input id="search" placeholder="filter by tool / subject / URL…" />
<div id="stats">__NNODES__ nodes · __NLINKS__ edges</div>
<div id="g"></div>
<script src="https://unpkg.com/3d-force-graph"></script>
<script>
const DATA = __DATA__;
const elem = document.getElementById('g');
const G = ForceGraph3D()(elem)
  .graphData(DATA)
  .backgroundColor('#0b1020')
  .nodeLabel(n => `<div style="background:#0f172a;color:#e2e8f0;padding:6px 10px;border:1px solid #334155;border-radius:6px;max-width:380px;font-size:12px"><b>${n.label}</b>${n.tooltip ? '<br><span style="color:#94a3b8">' + n.tooltip + '</span>' : ''}</div>`)
  .nodeColor(n => n._dim ? '#1e293b' : n.color)
  .nodeVal(n => n.val)
  .linkColor(() => 'rgba(148,163,184,0.18)')
  .linkOpacity(0.25)
  .linkWidth(0.4)
  .onNodeClick(n => {
    const dist = 60;
    const ratio = 1 + dist / Math.hypot(n.x, n.y, n.z);
    G.cameraPosition({x: n.x*ratio, y: n.y*ratio, z: n.z*ratio}, n, 1500);
  });

document.getElementById('search').addEventListener('input', e => {
  const q = e.target.value.toLowerCase().trim();
  DATA.nodes.forEach(n => {
    n._dim = q.length > 0 && !(n.label||'').toLowerCase().includes(q) && !(n.tooltip||'').toLowerCase().includes(q);
  });
  G.nodeColor(G.nodeColor());
});
</script></body></html>
"""
    html = (
        html.replace("__DATA__", json.dumps(data))
        .replace("__FAMILYLEG__", family_legend)
        .replace("__NFAMS__", str(len(families)))
        .replace("__NNODES__", f"{len(nodes):,}")
        .replace("__NLINKS__", f"{len(links):,}")
    )
    args.out.write_text(html)
    print(
        f"wrote {args.out}  "
        f"({len(nodes):,} nodes / {len(links):,} edges / {len(families)} families)"
    )
    print(f"open with: open {args.out}")


if __name__ == "__main__":
    main()
