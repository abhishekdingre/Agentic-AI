"""Transform data/allowlist.json + the graphify extraction into docs/knowledge_graph.json.

Node identity and metadata (source_id, domain, title, version, status, supersedes) come
straight from allowlist.json -- the same registry backend/allowlist.py loads at request
time -- so this graph can never drift from what the live app actually enforces.
The one piece that needs real reading comprehension (the LIT-007 vs IR-006/CT-004 hERG
contradiction) is carried over from the graphify semantic-extraction pass in
graphify-out/graph.json, where a subagent read all three documents in full.

Run after `/graphify` has built graphify-out/graph.json. Not part of the live app.
"""
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
allowlist = json.loads((ROOT / "data/allowlist.json").read_text(encoding="utf-8"))
graph = json.loads((ROOT / "graphify-out/graph.json").read_text(encoding="utf-8"))

def norm(source_id: str) -> str:
    return source_id.lower().replace("-", "_")

nodes = []
domain_clusters = {}
for entry in allowlist:
    node_id = norm(entry["source_id"])
    nodes.append({
        "id": node_id,
        "source_id": entry["source_id"],
        "title": entry["title"],
        "domain": entry["domain"],
        "version": entry["version"],
        "status": entry["status"],
        "file_path": entry["file_path"],
    })
    domain_clusters.setdefault(entry["domain"], []).append(node_id)

edges = []
for entry in allowlist:
    if entry.get("supersedes"):
        edges.append({
            "source": norm(entry["source_id"]),
            "target": norm(entry["supersedes"]),
            "relation": "supersedes",
            "confidence": "EXTRACTED",
            "evidence": "data/allowlist.json 'supersedes' field (ground truth registry, not inferred)",
        })

# Carry over the hERG contradiction found by graphify's semantic-extraction subagent,
# which read the full text of LIT-007.md, IR-006.md, and CT-004.md.
gid_to_norm = {n["id"]: norm(n["original_source_id"]) for n in graph["nodes"] if n.get("original_source_id")}
herg_concept = next((n for n in graph["nodes"] if n["id"] == "herg_cardiac_safety_signal"), None)
contradiction_annotation = None
if herg_concept:
    contradicts_edges = [
        l for l in graph["links"]
        if l.get("relation") == "contradicts" and l["source"] in gid_to_norm and l["target"] in gid_to_norm
    ]
    for l in contradicts_edges:
        edges.append({
            "source": gid_to_norm[l["source"]],
            "target": gid_to_norm[l["target"]],
            "relation": "contradicts",
            "confidence": l["confidence"],
            "confidence_score": l["confidence_score"],
            "evidence": "graphify semantic extraction, read in full: LIT-007.md vs IR-006.md/CT-004.md",
        })
    contradiction_annotation = {
        "topic": "hERG cardiac safety signal",
        "documents": sorted({gid_to_norm[l["source"]] for l in contradicts_edges} | {gid_to_norm[l["target"]] for l in contradicts_edges}),
        "summary": herg_concept["rationale"],
    }

output = {
    "nodes": nodes,
    "edges": edges,
    "domain_clusters": domain_clusters,
    "contradiction_annotations": [contradiction_annotation] if contradiction_annotation else [],
    "source": {
        "allowlist": "data/allowlist.json",
        "corpus": "data/corpus/",
        "graphify_graph": "graphify-out/graph.json",
    },
}

out_path = ROOT / "docs/knowledge_graph.json"
out_path.write_text(json.dumps(output, indent=2, ensure_ascii=False), encoding="utf-8")
print(f"Wrote {out_path}: {len(nodes)} nodes, {len(edges)} edges, {len(domain_clusters)} domain clusters")
