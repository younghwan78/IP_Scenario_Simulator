"""Generate interactive HTML views using ELK.js for orthogonal layout.

Produces standalone HTML files with:
  - Top-to-bottom vertical layout
  - Orthogonal (right-angle) edge routing
  - Color-coded hierarchy groups (Sensor, ISP, CODEC, DPU)
  - Zoom/pan support

Three view levels:
  - Top:    hierarchy_group blocks only
  - Level1: IP-level detail within hierarchy_groups
  - Level2: Module-level detail within IPs
"""
import json
import os
import sys

sys.path.insert(0, '.')
from src.view.plantuml_view import (
    _safe_id, _build_groups, _mod_color, _mod_type_short, _ip_label,
    HIERARCHY_ORDER, HIERARCHY_COLORS, IP_GROUP_COLORS, _load_data
)
from src.model.hw_nodes import SensorNode
from src.model.scenario import ConnectionType


# ═══════════════════════════════════════════════════════════════════
#  Shared: edge builder
# ═══════════════════════════════════════════════════════════════════

def _build_cross_edges(elk, meta, scenario, ip_settings):
    """Build cross-IP edges (OTF / M2M) and add to elk['edges']."""
    eidx = 0
    for src_id, dst_id, edge_data in scenario.graph.edges(data=True):
        conn_type = edge_data.get('conn_type', ConnectionType.M2M)
        port_pairs = edge_data.get('port_pairs', [])

        if conn_type == ConnectionType.OTF:
            lbl = "OTF"
            if port_pairs and port_pairs[0][0] != 'output':
                pairs = [f"{sp}→{dp}" for sp, dp in port_pairs]
                lbl = "OTF: " + ", ".join(pairs)
            eid = f"eo_{eidx}"
            lw = max(len(lbl) * 6, 40)
            elk["edges"].append({
                "id": eid, "sources": [src_id], "targets": [dst_id],
                "labels": [{"text": lbl, "width": lw, "height": 14}]
            })
            meta[eid] = {"type": "otf", "label": lbl}
            eidx += 1
        else:
            src_s = ip_settings.get(src_id, {})
            dst_s = ip_settings.get(dst_id, {})
            src_out = {o.get('port', ''): o for o in src_s.get('outputs', [])}
            dst_in = {i.get('port', ''): i for i in dst_s.get('inputs', [])}

            if port_pairs and port_pairs[0][0] != 'output':
                for sp, dp in port_pairs:
                    info = src_out.get(sp) or dst_in.get(dp) or {}
                    parts = [f"{sp}→{dp}"]
                    sz = info.get('size', [])
                    if len(sz) == 4 and sz[2] > 0:
                        parts.append(f"{sz[2]}×{sz[3]}")
                    if info.get('format'):
                        parts.append(info['format'])
                    if info.get('bitwidth'):
                        parts.append(f"{info['bitwidth']}bit")
                    if info.get('comp') == 'enable':
                        parts.append("COMP")
                    lbl = " | ".join(parts)
                    eid = f"em_{eidx}"
                    lw = max(len(lbl) * 6, 60)
                    elk["edges"].append({
                        "id": eid, "sources": [src_id], "targets": [dst_id],
                        "labels": [{"text": lbl, "width": lw, "height": 14}]
                    })
                    meta[eid] = {"type": "m2m", "label": lbl}
                    eidx += 1
            else:
                eid = f"em_{eidx}"
                elk["edges"].append({
                    "id": eid, "sources": [src_id], "targets": [dst_id],
                    "labels": [{"text": "M2M", "width": 30, "height": 14}]
                })
                meta[eid] = {"type": "m2m", "label": "M2M"}
                eidx += 1


def _make_elk_root():
    """Create root ELK graph node with standard layout options."""
    return {
        "id": "root",
        "layoutOptions": {
            "elk.algorithm": "layered",
            "elk.direction": "DOWN",
            "elk.edgeRouting": "ORTHOGONAL",
            "elk.spacing.nodeNode": "20",
            "elk.layered.spacing.nodeNodeBetweenLayers": "30",
            "elk.padding": "[top=20,left=20,bottom=20,right=20]",
            "elk.hierarchyHandling": "INCLUDE_CHILDREN"
        },
        "children": [],
        "edges": []
    }


def _render_html(title, elk, meta, output_path):
    """Render ELK graph + metadata into HTML file using shared template."""
    html = _HTML_TEMPLATE.replace('/*__TITLE__*/', title)
    html = html.replace('/*__GRAPH__*/', json.dumps(elk))
    html = html.replace('/*__META__*/', json.dumps(meta))

    with open(output_path, 'w', encoding='utf-8') as f:
        f.write(html)


# ═══════════════════════════════════════════════════════════════════
#  Top View: hierarchy_group blocks only
# ═══════════════════════════════════════════════════════════════════

def generate_top_html(hw_registry, scenario, output_path):
    groups, task_hw, task_hier, task_ipg = _build_groups(scenario, hw_registry)
    ip_settings = getattr(scenario, '_ip_settings', {})
    meta = {}

    elk = _make_elk_root()

    for grp in HIERARCHY_ORDER:
        if grp not in groups:
            continue
        grp_id = f"grp_{grp}"

        # Gather IP names listed inside this group
        ip_names = list(dict.fromkeys(task_hw[tid] for tid in groups[grp]))
        ip_list = ", ".join(ip_names)
        label = f"{grp}\\n({ip_list})"

        # Calculate node size based on text
        lw = max(len(grp) * 10, len(ip_list) * 6, 120)
        elk["children"].append({"id": grp_id, "width": lw, "height": 60})
        meta[grp_id] = {"type": "group_box", "label": label,
                        "color": HIERARCHY_COLORS.get(grp, "#FAFAFA")}

    # Map task IDs to their group IDs for edge source/target remapping
    task_to_grp = {}
    for grp, tids in groups.items():
        grp_id = f"grp_{grp}"
        for tid in tids:
            task_to_grp[tid] = grp_id

    # Build edges between groups (deduplicate same-group pairs)
    eidx = 0
    seen_otf = set()
    for src_id, dst_id, edge_data in scenario.graph.edges(data=True):
        src_g = task_to_grp.get(src_id, src_id)
        dst_g = task_to_grp.get(dst_id, dst_id)
        if src_g == dst_g:
            continue  # skip intra-group edges

        conn_type = edge_data.get('conn_type', ConnectionType.M2M)
        port_pairs = edge_data.get('port_pairs', [])

        if conn_type == ConnectionType.OTF:
            key = (src_g, dst_g)
            if key in seen_otf:
                continue
            seen_otf.add(key)
            eid = f"eo_{eidx}"
            elk["edges"].append({
                "id": eid, "sources": [src_g], "targets": [dst_g],
                "labels": [{"text": "OTF", "width": 40, "height": 14}]
            })
            meta[eid] = {"type": "otf", "label": "OTF"}
            eidx += 1
        else:
            src_s = ip_settings.get(src_id, {})
            dst_s = ip_settings.get(dst_id, {})
            src_out = {o.get('port', ''): o for o in src_s.get('outputs', [])}
            dst_in = {i.get('port', ''): i for i in dst_s.get('inputs', [])}

            if port_pairs and port_pairs[0][0] != 'output':
                for sp, dp in port_pairs:
                    info = src_out.get(sp) or dst_in.get(dp) or {}
                    parts = [f"{sp}→{dp}"]
                    sz = info.get('size', [])
                    if len(sz) == 4 and sz[2] > 0:
                        parts.append(f"{sz[2]}×{sz[3]}")
                    if info.get('format'):
                        parts.append(info['format'])
                    if info.get('bitwidth'):
                        parts.append(f"{info['bitwidth']}bit")
                    if info.get('comp') == 'enable':
                        parts.append("COMP")
                    lbl = " | ".join(parts)
                    eid = f"em_{eidx}"
                    lw = max(len(lbl) * 6, 60)
                    elk["edges"].append({
                        "id": eid, "sources": [src_g], "targets": [dst_g],
                        "labels": [{"text": lbl, "width": lw, "height": 14}]
                    })
                    meta[eid] = {"type": "m2m", "label": lbl}
                    eidx += 1
            else:
                eid = f"em_{eidx}"
                elk["edges"].append({
                    "id": eid, "sources": [src_g], "targets": [dst_g],
                    "labels": [{"text": "M2M", "width": 30, "height": 14}]
                })
                meta[eid] = {"type": "m2m", "label": "M2M"}
                eidx += 1

    _render_html("Top View (Hierarchy Groups)", elk, meta, output_path)
    print(f"Top HTML -> {output_path}")


# ═══════════════════════════════════════════════════════════════════
#  Level 1: IP-level detail
# ═══════════════════════════════════════════════════════════════════

def generate_level1_html(hw_registry, scenario, output_path):
    groups, task_hw, task_hier, task_ipg = _build_groups(scenario, hw_registry)
    ip_settings = getattr(scenario, '_ip_settings', {})
    meta = {}

    elk = _make_elk_root()

    for grp in HIERARCHY_ORDER:
        if grp not in groups:
            continue
        grp_id = f"grp_{grp}"
        grp_node = {
            "id": grp_id,
            "layoutOptions": {
                "elk.padding": "[top=30,left=12,bottom=12,right=12]",
                "elk.algorithm": "layered",
                "elk.direction": "DOWN",
                "elk.edgeRouting": "ORTHOGONAL",
                "elk.spacing.nodeNode": "15",
                "elk.layered.spacing.nodeNodeBetweenLayers": "20"
            },
            "children": [], "edges": []
        }
        meta[grp_id] = {"type": "group", "label": grp,
                        "color": HIERARCHY_COLORS.get(grp, "#FAFAFA")}

        for tid in groups[grp]:
            hw_name = task_hw[tid]
            hw = hw_registry.get(hw_name)
            ipg = task_ipg[tid]
            ip_bg = IP_GROUP_COLORS.get(ipg, "#E0E0E0")

            # Build label with resolution info
            lbl = hw_name
            ts = ip_settings.get(tid, {})
            inputs = ts.get('inputs', [])
            if inputs:
                sz = inputs[0].get('size', [])
                if len(sz) == 4 and sz[2] > 0:
                    lbl = f"{hw_name}\\n{sz[2]}x{sz[3]}"
            elif isinstance(hw, SensorNode):
                lbl = f"{hw_name}\\n{hw.frame_width}x{hw.frame_height}@{hw.fps:.0f}fps"

            w = max(len(hw_name) * 8 + 20, 100)
            grp_node["children"].append({"id": tid, "width": w, "height": 40})
            meta[tid] = {"type": "leaf", "label": lbl, "color": ip_bg}

        elk["children"].append(grp_node)

    # Cross-IP edges
    _build_cross_edges(elk, meta, scenario, ip_settings)

    _render_html("Level 1 View (IP Detail)", elk, meta, output_path)
    print(f"Level 1 HTML -> {output_path}")


# ═══════════════════════════════════════════════════════════════════
#  Level 2: Module-level detail
# ═══════════════════════════════════════════════════════════════════

def generate_level2_html(hw_registry, scenario, hw_raw, output_path):
    groups, task_hw, task_hier, task_ipg = _build_groups(scenario, hw_registry)
    ip_settings = getattr(scenario, '_ip_settings', {})
    meta = {}

    elk = _make_elk_root()

    # Build hierarchy groups
    for grp in HIERARCHY_ORDER:
        if grp not in groups:
            continue
        grp_id = f"grp_{grp}"
        grp_node = {
            "id": grp_id,
            "layoutOptions": {
                "elk.padding": "[top=30,left=12,bottom=12,right=12]",
                "elk.algorithm": "layered",
                "elk.direction": "DOWN",
                "elk.edgeRouting": "ORTHOGONAL",
                "elk.spacing.nodeNode": "15",
                "elk.layered.spacing.nodeNodeBetweenLayers": "20"
            },
            "children": [], "edges": []
        }
        meta[grp_id] = {"type": "group", "label": grp,
                        "color": HIERARCHY_COLORS.get(grp, "#FAFAFA")}

        for tid in groups[grp]:
            hw_name = task_hw[tid]
            hw = hw_registry.get(hw_name)
            raw = hw_raw.get(hw_name, {})
            modules = raw.get('modules', [])
            ipg = task_ipg[tid]
            ip_bg = IP_GROUP_COLORS.get(ipg, "#E0E0E0")

            if modules:
                ip_node = {
                    "id": tid,
                    "layoutOptions": {
                        "elk.padding": "[top=24,left=8,bottom=8,right=8]",
                        "elk.algorithm": "layered",
                        "elk.direction": "DOWN",
                        "elk.edgeRouting": "ORTHOGONAL",
                        "elk.spacing.nodeNode": "6",
                        "elk.layered.spacing.nodeNodeBetweenLayers": "8"
                    },
                    "children": [], "edges": []
                }
                meta[tid] = {"type": "ip", "label": hw_name, "color": ip_bg}

                for mod in modules:
                    mn = mod.get('name', '?')
                    mt = mod.get('type', 'Generic')
                    mid = f"{tid}_{_safe_id(mn)}"
                    short = _mod_type_short(mt)
                    w = max(len(mn) * 7 + 16, 65)
                    ip_node["children"].append({"id": mid, "width": w, "height": 30})
                    meta[mid] = {"type": "mod", "label": mn, "sub": short,
                                 "color": _mod_color(mt)}

                for ie in raw.get('edges', []):
                    s = f"{tid}_{_safe_id(ie.get('src', ''))}"
                    d = f"{tid}_{_safe_id(ie.get('dst', ''))}"
                    eid = f"ie_{s}_{d}"
                    ip_node["edges"].append({
                        "id": eid, "sources": [s], "targets": [d]})
                    meta[eid] = {"type": "int"}

                grp_node["children"].append(ip_node)
            else:
                lbl = hw_name
                if isinstance(hw, SensorNode):
                    lbl = f"{hw_name}\\n{hw.frame_width}x{hw.frame_height}@{hw.fps:.0f}fps"
                w = max(len(hw_name) * 8 + 20, 100)
                grp_node["children"].append({"id": tid, "width": w, "height": 40})
                meta[tid] = {"type": "leaf", "label": lbl, "color": ip_bg}

        elk["children"].append(grp_node)

    # Cross-IP edges
    _build_cross_edges(elk, meta, scenario, ip_settings)

    _render_html("Level 2 View (Module Detail)", elk, meta, output_path)
    print(f"Level 2 HTML -> {output_path}")


# ═══════════════════════════════════════════════════════════════════
#  HTML Template (shared by all levels)
# ═══════════════════════════════════════════════════════════════════

_HTML_TEMPLATE = r'''<!DOCTYPE html>
<html lang="ko"><head><meta charset="UTF-8">
<meta name="viewport" content="width=device-width,initial-scale=1.0">
<title>/*__TITLE__*/</title>
<style>
*{box-sizing:border-box;margin:0;padding:0}
body{background:#F0F2F5;font-family:'Segoe UI','Malgun Gothic',sans-serif;overflow:auto}
#wrap{padding:16px;text-align:center}
h1{font-size:20px;color:#333;margin-bottom:12px}
#info{font-size:12px;color:#888;margin-bottom:8px}
svg{background:white;border-radius:8px;box-shadow:0 2px 12px rgba(0,0,0,.1);cursor:grab}
svg:active{cursor:grabbing}
</style></head><body>
<div id="wrap">
<h1>/*__TITLE__*/</h1>
<div id="info">Scroll to zoom · Drag to pan</div>
<div id="canvas"></div>
</div>
<script src="https://cdn.jsdelivr.net/npm/elkjs@0.9.3/lib/elk.bundled.js"></script>
<script>
const G=/*__GRAPH__*/;
const M=/*__META__*/;
const NS='http://www.w3.org/2000/svg';
/* Map of node id -> absolute position {x,y} built during first pass */
const NP={};

function ce(t,a){const e=document.createElementNS(NS,t);if(a)Object.entries(a).forEach(([k,v])=>e.setAttribute(k,v));return e}

async function main(){
  const elk=new ELK();
  const layout=await elk.layout(G);
  const pad=30;
  const svg=ce('svg',{width:layout.width+pad*2,height:layout.height+pad*2});
  const gMain=ce('g',{transform:`translate(${pad},${pad})`});
  svg.appendChild(gMain);
  /* Pass 1: collect absolute positions for all nodes */
  collectPositions(layout,0,0);
  /* Pass 2: draw nodes and edges */
  drawNode(gMain,layout,0,0);
  // zoom/pan
  let scale=1,tx=0,ty=0;
  function updateTx(){gMain.setAttribute('transform',`translate(${tx+pad},${ty+pad}) scale(${scale})`)}
  svg.addEventListener('wheel',e=>{e.preventDefault();const d=e.deltaY>0?0.9:1.1;const ns=Math.max(0.2,Math.min(5,scale*d));const r=ns/scale;tx=e.offsetX-(e.offsetX-tx)*r;ty=e.offsetY-(e.offsetY-ty)*r;scale=ns;updateTx()});
  let drag=false,sx,sy;
  svg.addEventListener('mousedown',e=>{drag=true;sx=e.clientX-tx;sy=e.clientY-ty});
  svg.addEventListener('mousemove',e=>{if(!drag)return;tx=e.clientX-sx;ty=e.clientY-sy;updateTx()});
  svg.addEventListener('mouseup',()=>drag=false);
  svg.addEventListener('mouseleave',()=>drag=false);
  document.getElementById('canvas').appendChild(svg);
}

/* Recursively collect absolute positions of every node */
function collectPositions(node,ox,oy){
  const x=(node.x||0)+ox, y=(node.y||0)+oy;
  NP[node.id]={x,y};
  (node.children||[]).forEach(c=>collectPositions(c,x,y));
}

function drawNode(g,node,ox,oy){
  const x=(node.x||0)+ox, y=(node.y||0)+oy;
  const m=M[node.id];
  if(node.width&&m){
    // rect
    const isGrpBox=m.type==='group_box';
    const rr=m.type==='group'?8:m.type==='ip'?6:isGrpBox?10:3;
    const sw=m.type==='group'||isGrpBox?1.5:1;
    const fo=m.type==='group'?'0.55':isGrpBox?'0.7':'1';
    const sc=m.type==='m2m'?'#E65100':'#999';
    const r=ce('rect',{x,y,width:node.width,height:node.height,rx:rr,ry:rr,
      fill:m.color||'#fff','fill-opacity':fo,stroke:sc,'stroke-width':sw});
    g.appendChild(r);
    // label
    const lines=(m.label||'').split('\\n');
    if(m.type==='group'||m.type==='ip'){
      const fs=m.type==='group'?13:11;
      const t=ce('text',{x:x+8,y:y+16,'font-size':fs,'font-weight':'bold',fill:'#333'});
      t.textContent=lines[0];g.appendChild(t);
    }else if(m.type==='group_box'){
      // Group box: centered bold name + smaller IP list
      const t=ce('text',{x:x+node.width/2,y:y+22,'text-anchor':'middle','font-size':14,'font-weight':'bold',fill:'#333'});
      t.textContent=lines[0];g.appendChild(t);
      if(lines.length>1){
        const t2=ce('text',{x:x+node.width/2,y:y+40,'text-anchor':'middle','font-size':9,fill:'#666'});
        t2.textContent=lines[1];g.appendChild(t2);
      }
    }else if(m.type==='mod'){
      const t=ce('text',{x:x+node.width/2,y:y+12,'text-anchor':'middle','font-size':9,fill:'#333'});
      t.textContent=m.label;g.appendChild(t);
      if(m.sub){
        const t2=ce('text',{x:x+node.width/2,y:y+23,'text-anchor':'middle','font-size':8,fill:'#666'});
        t2.textContent=m.sub;g.appendChild(t2);
      }
    }else{
      lines.forEach((ln,i)=>{
        const t=ce('text',{x:x+node.width/2,y:y+node.height/2+4+(i-(lines.length-1)/2)*14,
          'text-anchor':'middle','font-size':11,fill:'#333'});
        if(i===0)t.setAttribute('font-weight','bold');
        t.textContent=ln;g.appendChild(t);
      });
    }
  }
  (node.children||[]).forEach(c=>drawNode(g,c,x,y));
  /* Use the edge's container property (set by ELK.js) to determine the
     correct offset. With INCLUDE_CHILDREN, ELK may reassign edges to
     their LCA container, making coordinates relative to that container
     rather than the node whose edges array holds them. */
  (node.edges||[]).forEach(e=>{
    const cid=e.container||node.id;
    const cp=NP[cid]||{x:0,y:0};
    drawEdge(g,e,cp.x,cp.y);
  });
}

function drawEdge(g,edge,ox,oy){
  if(!edge.sections)return;
  const m=M[edge.id]||{};
  const isOtf=m.type==='otf';
  const isM2m=m.type==='m2m';
  const color=isOtf?'#1565C0':isM2m?'#E65100':'#999';
  const sw=isOtf?2.5:isM2m?2:1;
  const dash=isM2m?'6,3':'';

  edge.sections.forEach(s=>{
    const pts=[s.startPoint,...(s.bendPoints||[]),s.endPoint];
    const d=pts.map((p,i)=>`${i?'L':'M'}${p.x+ox},${p.y+oy}`).join(' ');
    const attrs={d,stroke:color,'stroke-width':sw,fill:'none'};
    if(dash)attrs['stroke-dasharray']=dash;
    g.appendChild(ce('path',attrs));
    // arrow
    const L=pts[pts.length-1],P=pts[pts.length-2];
    const a=Math.atan2(L.y-P.y,L.x-P.x),sz=6;
    const ax=L.x+ox,ay=L.y+oy;
    g.appendChild(ce('polygon',{
      points:`${ax},${ay} ${ax-sz*Math.cos(a-.45)},${ay-sz*Math.sin(a-.45)} ${ax-sz*Math.cos(a+.45)},${ay-sz*Math.sin(a+.45)}`,
      fill:color}));
  });
  // edge label
  if(edge.labels&&edge.labels.length){
    const lb=edge.labels[0];
    if(lb.x!==undefined){
      const bg=ce('rect',{x:lb.x+ox-2,y:lb.y+oy-1,width:lb.width+4,height:lb.height+2,
        rx:3,fill:'white','fill-opacity':'0.85',stroke:color,'stroke-width':0.5});
      g.appendChild(bg);
      const t=ce('text',{x:lb.x+ox+lb.width/2,y:lb.y+oy+11,
        'text-anchor':'middle','font-size':9,fill:color,'font-weight':'bold'});
      t.textContent=m.label||'';g.appendChild(t);
    }
  }
}

main();
</script></body></html>
'''


# ── Main ─────────────────────────────────────────────────────────

if __name__ == "__main__":
    hw_path = "hw_config/projectA_hw.yaml"
    sc_path = "scenario_config/projectA_FHD30_recording_scenario.yaml"
    out_dir = "output"
    os.makedirs(out_dir, exist_ok=True)

    hw_registry, scenario, hw_raw = _load_data(hw_path, sc_path)

    generate_top_html(hw_registry, scenario, f"{out_dir}/scenario_top.html")
    generate_level1_html(hw_registry, scenario, f"{out_dir}/scenario_level1.html")
    generate_level2_html(hw_registry, scenario, hw_raw, f"{out_dir}/scenario_level2.html")
    print("\nAll 3 HTML diagrams generated!")
