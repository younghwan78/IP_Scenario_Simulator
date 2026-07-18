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
    _safe_id, _build_groups, _mod_color, _mod_type_short, _load_data,
    _get_effective_hierarchy_order, _get_hierarchy_color, _get_ip_group_color,
    _get_hierarchy_border, _get_ip_group_border, _darken_hex,
    _is_sw_edge,
)
from src.model.hw_nodes import SensorNode, IPNode
from src.model.scenario import ConnectionType
from src.model.bw import comp_enabled


# ═══════════════════════════════════════════════════════════════════
#  Shared: size / badge helpers
# ═══════════════════════════════════════════════════════════════════

def _extract_wh_ip(size):
    """Extract (w, h) from [x, y, w, h] or [w, h] size list."""
    if len(size) == 4 and size[2] > 0:
        return size[2], size[3]
    if len(size) == 2 and size[0] > 0:
        return size[0], size[1]
    return None, None


def _get_ip_size_info(tid, hw, ip_settings):
    """Compute primary input/output size and S/C badge flags for an IP.

    Returns:
        in_str   : "WxH" string for primary input  (or None)
        out_str  : "WxH" string for representative output (or None)
        badges   : list of badge strings — can contain 'S', 'C', both, or neither
    """
    ts = ip_settings.get(tid, {})
    inputs  = ts.get('inputs', [])
    outputs = ts.get('outputs', [])

    # ── Primary INPUT: largest by pixel count ───────────────────────
    in_w = in_h = None
    best_px = -1
    for inp in inputs:
        w, h = _extract_wh_ip(inp.get('size', []))
        if w and h and w * h > best_px:
            in_w, in_h, best_px = w, h, w * h

    # ── Primary OUTPUT: prefer COUTFIFO; fallback = largest ─────────
    out_w = out_h = None
    # 1st pass: COUTFIFO
    for out in outputs:
        if 'COUTFIFO' in out.get('port', ''):
            w, h = _extract_wh_ip(out.get('size', []))
            if w and h:
                out_w, out_h = w, h
                break
    # 2nd pass: largest pixel count (skip STAT ports which are unrelated)
    if out_w is None:
        best_px = -1
        for out in outputs:
            if out.get('format') == 'STAT':
                continue  # skip statistical outputs
            w, h = _extract_wh_ip(out.get('size', []))
            if w and h and w * h > best_px:
                out_w, out_h, best_px = w, h, w * h

    in_str  = f"{in_w}x{in_h}"  if in_w else None
    out_str = f"{out_w}x{out_h}" if out_w else None

    # ── Badge detection ─────────────────────────────────────────────
    badges = []
    if in_w and out_w:
        # S badge: output resolution differs from input
        if (in_w != out_w) or (in_h != out_h):
            badges.append('S')
        # C badge: HW supports crop AND output < input (aspect may differ)
        hw_supports_crop = isinstance(hw, IPNode) and getattr(hw, 'supports_crop', False)
        if hw_supports_crop and out_w * out_h < in_w * in_h:
            badges.append('C')

    return in_str, out_str, badges


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
            is_sw = _is_sw_edge(scenario, src_id, dst_id)
            edge_type = "sw" if is_sw else "m2m"
            src_s = ip_settings.get(src_id, {})
            dst_s = ip_settings.get(dst_id, {})
            src_out = {o.get('port', ''): o for o in src_s.get('outputs', [])}
            dst_in = {i.get('port', ''): i for i in dst_s.get('inputs', [])}

            if port_pairs and port_pairs[0][0] != 'output':
                for sp, dp in port_pairs:
                    info = src_out.get(sp) or dst_in.get(dp) or {}
                    lines = [f"{sp}→{dp}"]
                    sz = info.get('size', [])
                    if len(sz) == 4 and sz[2] > 0:
                        lines.append(f"{sz[2]}×{sz[3]}")
                    if info.get('format'):
                        lines.append(info['format'])
                    if info.get('bitwidth'):
                        lines.append(f"{info['bitwidth']}bit")
                    if comp_enabled(info.get('comp')):
                        lines.append("COMP")
                    lbl = "\\n".join(lines)
                    buf_id = f"buf_{eidx}"
                    bw = max(len(l) * 7 for l in lines) + 24
                    bh = len(lines) * 13 + 10
                    elk["children"].append({"id": buf_id, "width": bw, "height": bh})
                    meta[buf_id] = {"type": "buffer", "label": lbl}
                    elk["edges"].append({"id": f"eb_{eidx}a", "sources": [src_id], "targets": [buf_id]})
                    elk["edges"].append({"id": f"eb_{eidx}b", "sources": [buf_id], "targets": [dst_id]})
                    meta[f"eb_{eidx}a"] = {"type": edge_type}
                    meta[f"eb_{eidx}b"] = {"type": edge_type}
                    eidx += 1
            else:
                eid = f"em_{eidx}"
                lbl = "SW" if is_sw else "M2M"
                elk["edges"].append({
                    "id": eid, "sources": [src_id], "targets": [dst_id],
                    "labels": [{"text": lbl, "width": 30, "height": 14}]
                })
                meta[eid] = {"type": edge_type, "label": lbl}
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

    for grp in _get_effective_hierarchy_order(groups):
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
                        "color": _get_hierarchy_color(grp),
                        "border": _get_hierarchy_border(grp)}

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
            is_sw = _is_sw_edge(scenario, src_id, dst_id)
            edge_type = "sw" if is_sw else "m2m"
            src_s = ip_settings.get(src_id, {})
            dst_s = ip_settings.get(dst_id, {})
            src_out = {o.get('port', ''): o for o in src_s.get('outputs', [])}
            dst_in = {i.get('port', ''): i for i in dst_s.get('inputs', [])}

            if port_pairs and port_pairs[0][0] != 'output':
                for sp, dp in port_pairs:
                    info = src_out.get(sp) or dst_in.get(dp) or {}
                    lines = [f"{sp}→{dp}"]
                    sz = info.get('size', [])
                    if len(sz) == 4 and sz[2] > 0:
                        lines.append(f"{sz[2]}×{sz[3]}")
                    if info.get('format'):
                        lines.append(info['format'])
                    if info.get('bitwidth'):
                        lines.append(f"{info['bitwidth']}bit")
                    if comp_enabled(info.get('comp')):
                        lines.append("COMP")
                    lbl = "\\n".join(lines)
                    buf_id = f"buf_{eidx}"
                    bw = max(len(l) * 7 for l in lines) + 24
                    bh = len(lines) * 13 + 10
                    elk["children"].append({"id": buf_id, "width": bw, "height": bh})
                    meta[buf_id] = {"type": "buffer", "label": lbl}
                    elk["edges"].append({"id": f"eb_{eidx}a", "sources": [src_g], "targets": [buf_id]})
                    elk["edges"].append({"id": f"eb_{eidx}b", "sources": [buf_id], "targets": [dst_g]})
                    meta[f"eb_{eidx}a"] = {"type": edge_type}
                    meta[f"eb_{eidx}b"] = {"type": edge_type}
                    eidx += 1
            else:
                eid = f"em_{eidx}"
                lbl = "SW" if is_sw else "M2M"
                elk["edges"].append({
                    "id": eid, "sources": [src_g], "targets": [dst_g],
                    "labels": [{"text": lbl, "width": 30, "height": 14}]
                })
                meta[eid] = {"type": edge_type, "label": lbl}
                eidx += 1

    _render_html("Top View (Hierarchy Groups)", elk, meta, output_path)
    print(f"Top HTML -> {output_path}")


# ═══════════════════════════════════════════════════════════════════
#  Level 1: IP-level detail
# ═══════════════════════════════════════════════════════════════════

def _build_port_detail(tid, hw_name, hw, ip_settings, scenario=None):
    """Build structured detail dict for tooltip from ip_settings."""
    ts = ip_settings.get(tid, {})
    mode = ts.get('mode', 'Normal')
    detail = {"hw": hw_name, "mode": mode, "inputs": [], "outputs": []}

    # Sensor nodes: show sensor specs instead of ports
    if isinstance(hw, SensorNode):
        detail["inputs"] = [{"port": "Sensor",
                             "size": f"{hw.frame_width}x{hw.frame_height}",
                             "format": f"@{hw.fps:.0f}fps", "bitwidth": "", "comp": ""}]
        return detail

    for port_cfg in ts.get('inputs', []):
        p = {"port": port_cfg.get('port', '?')}
        sz = port_cfg.get('size', [])
        p["size"] = f"{sz[2]}x{sz[3]}" if len(sz) == 4 and sz[2] > 0 else "-"
        p["format"] = port_cfg.get('format', '')
        bw = port_cfg.get('bitwidth')
        p["bitwidth"] = f"{bw}b" if bw else ''
        comp = port_cfg.get('comp', '')
        ratio = port_cfg.get('comp_ratio', '')
        p["comp"] = f"{comp}({ratio})" if comp_enabled(comp) and ratio else comp
        detail["inputs"].append(p)

    for port_cfg in ts.get('outputs', []):
        p = {"port": port_cfg.get('port', '?')}
        sz = port_cfg.get('size', [])
        p["size"] = f"{sz[2]}x{sz[3]}" if len(sz) == 4 and sz[2] > 0 else "-"
        p["format"] = port_cfg.get('format', '')
        bw = port_cfg.get('bitwidth')
        p["bitwidth"] = f"{bw}b" if bw else ''
        comp = port_cfg.get('comp', '')
        ratio = port_cfg.get('comp_ratio', '')
        p["comp"] = f"{comp}({ratio})" if comp_enabled(comp) and ratio else comp
        detail["outputs"].append(p)

    # SW tasks: show processor info
    if scenario:
        task = scenario.get_task(tid)
        if task and task.is_sw_task:
            detail["inputs"] = [{"port": "CPU", "size": "-",
                                 "format": f"{task.duration_ms}ms", "bitwidth": "", "comp": ""}]
            detail["outputs"] = []

    return detail


def generate_level1_html(hw_registry, scenario, output_path):
    groups, task_hw, task_hier, task_ipg = _build_groups(scenario, hw_registry)
    ip_settings = getattr(scenario, '_ip_settings', {})
    meta = {}

    elk = _make_elk_root()

    for grp in _get_effective_hierarchy_order(groups):
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
                        "color": _get_hierarchy_color(grp),
                        "border": _get_hierarchy_border(grp)}

        for tid in groups[grp]:
            hw_name = task_hw[tid]
            hw = hw_registry.get(hw_name)
            ipg = task_ipg[tid]
            ip_bg = _get_ip_group_color(ipg)

            # Build label with resolution info (input + output)
            lbl = hw_name
            in_str, out_str, badges = _get_ip_size_info(tid, hw, ip_settings)

            if in_str:
                if out_str and out_str != in_str:
                    lbl = f"{hw_name}\\n{in_str}\u2192{out_str}"
                else:
                    lbl = f"{hw_name}\\n{in_str}"
            elif isinstance(hw, SensorNode):
                lbl = f"{hw_name}\\n{hw.frame_width}x{hw.frame_height}@{hw.fps:.0f}fps"

            # Taller node only when in→out differ (extra line)
            size_changed = in_str and out_str and out_str != in_str
            h = 54 if size_changed else 40
            w = max(len(hw_name) * 8 + 20, 110)
            if size_changed:
                w = max(w, (len(in_str) + len(out_str) + 4) * 6 + 20)
            elif in_str:
                w = max(w, len(in_str) * 7 + 20)
            grp_node["children"].append({"id": tid, "width": w, "height": h})
            ip_bd = _get_ip_group_border(ipg)
            # Build detail info for tooltip popup
            detail = _build_port_detail(tid, hw_name, hw, ip_settings, scenario)
            meta[tid] = {"type": "leaf", "label": lbl, "color": ip_bg,
                         "border": ip_bd, "detail": detail, "badges": badges}

        elk["children"].append(grp_node)

    # Cross-IP edges
    _build_cross_edges(elk, meta, scenario, ip_settings)

    _render_html("Level 1 View (IP Detail)", elk, meta, output_path)
    print(f"Level 1 HTML -> {output_path}")


# ═══════════════════════════════════════════════════════════════════
#  Level 2: I/O Module detail (CIN / COUT / DMA only)
# ═══════════════════════════════════════════════════════════════════

# Module types shown in Level 2 (I/O interfaces only)
_IO_MODULE_TYPES = {'CIN', 'COUT', 'DMA', 'DMA_READ', 'DMA_WRITE'}

# Input-side module types (placed at top)
_INPUT_TYPES = {'CIN', 'DMA_READ'}
# Output-side module types (placed at bottom)
_OUTPUT_TYPES = {'COUT', 'DMA_WRITE'}


def _classify_dma_direction(mod):
    """Classify a DMA module as input (read) or output (write)."""
    mt = mod.get('type', 'Generic')
    if mt in _INPUT_TYPES:
        return 'input'
    if mt in _OUTPUT_TYPES:
        return 'output'
    if mt == 'DMA':
        d = mod.get('direction', '').lower()
        return 'input' if d == 'read' else 'output'
    return 'output'


def _get_used_port_names(tid, ip_settings):
    """Get the set of port names used in ip_settings for a task."""
    settings = ip_settings.get(tid, {})
    used = set()
    for inp in settings.get('inputs', []):
        p = inp.get('port', '')
        if p:
            used.add(p)
    for out in settings.get('outputs', []):
        p = out.get('port', '')
        if p:
            used.add(p)
    return used


def _get_port_comp_info(tid, port_name, ip_settings):
    """Check if a port has SBWC/compression or LLC enabled in ip_settings."""
    settings = ip_settings.get(tid, {})
    for port_list in (settings.get('inputs', []), settings.get('outputs', [])):
        for p in port_list:
            if p.get('port', '') == port_name:
                comp = p.get('comp', 'disable')
                llc = p.get('llc', 'disable')
                return comp == 'enable', llc == 'enable'
    return False, False


def _build_l2_module_detail(mod, tid, ip_settings, is_disabled):
    """Build detail dict for Level 2 module tooltip."""
    mn = mod.get('name', '')
    mt = mod.get('type', 'Generic')
    direction = _classify_dma_direction(mod)
    status = 'Disabled' if is_disabled else 'Enabled'

    # Find port config from ip_settings
    settings = ip_settings.get(tid, {})
    port_cfg = None
    for port_list in (settings.get('inputs', []), settings.get('outputs', [])):
        for p in port_list:
            if p.get('port', '') == mn:
                port_cfg = p
                break
        if port_cfg:
            break

    detail = {'name': mn, 'status': status}

    if mt in ('DMA', 'DMA_READ', 'DMA_WRITE'):
        detail['mod_type'] = 'dma'
        detail['direction'] = 'Read' if direction == 'input' else 'Write'
        bw = mod.get('max_bandwidth')
        detail['bandwidth'] = f"{bw / 1e9:.1f} GB/s" if bw else '-'
        mo = mod.get('multiple_outstanding')
        detail['mo'] = str(mo) if mo else '-'
        has_comp, has_llc = _get_port_comp_info(tid, mn, ip_settings)
        detail['sbwc'] = 'Enabled' if has_comp else 'Disabled'
        detail['llc'] = 'Enabled' if has_llc else 'Disabled'
        if port_cfg:
            sz = port_cfg.get('size', [])
            detail['size'] = f"{sz[2]}x{sz[3]}" if len(sz) == 4 and sz[2] > 0 else '-'
            detail['format'] = port_cfg.get('format', '-') or '-'
            bw_val = port_cfg.get('bitwidth')
            detail['bitwidth'] = f"{bw_val}b" if bw_val else '-'
            comp = port_cfg.get('comp', '')
            ratio = port_cfg.get('comp_ratio', '')
            detail['comp'] = f"{comp}({ratio})" if comp_enabled(comp) and ratio else (comp or '-')
        else:
            detail['size'] = '-'
            detail['format'] = '-'
            detail['bitwidth'] = '-'
            detail['comp'] = '-'
    else:
        # CIN or COUT
        detail['mod_type'] = 'cin' if mt == 'CIN' else 'cout'
        if port_cfg:
            sz = port_cfg.get('size', [])
            detail['size'] = f"{sz[2]}x{sz[3]}" if len(sz) == 4 and sz[2] > 0 else '-'
        else:
            detail['size'] = '-'

    return detail


# Level 2 module color scheme:
#   RDMA (read DMA)      : light green
#   WDMA (write DMA)     : light blue
#   RDMA/WDMA + SBWC     : orange tint
#   RDMA/WDMA + LLC      : purple tint
#   RDMA/WDMA + SBWC+LLC : pink tint
#   CIN                  : light gray-blue (input)
#   COUT                 : light gray-green (output)
#   Disabled module      : light gray
def _l2_mod_color(mod, tid, ip_settings, is_disabled=False):
    """Determine module color for Level 2 based on type, direction and SBWC/LLC."""
    if is_disabled:
        return '#E8E8E8'   # light gray for disabled modules

    mt = mod.get('type', 'Generic')
    mn = mod.get('name', '')
    direction = _classify_dma_direction(mod)
    has_comp, has_llc = _get_port_comp_info(tid, mn, ip_settings)

    if mt == 'CIN':
        return '#CEEAD6'   # pastel green (input FIFO)
    if mt == 'COUT':
        return '#E8DAEF'   # pastel purple (output FIFO)

    # DMA types
    if has_comp and has_llc:
        return '#F8BBD0'   # pink — SBWC + LLC
    if has_comp:
        return '#FFE0B2'   # orange — SBWC/compression
    if has_llc:
        return '#E1BEE7'   # purple — LLC
    if direction == 'input':
        return '#D2E3FC'   # pastel blue — RDMA
    return '#FEEFC3'       # pastel orange — WDMA


def _build_cross_edges_level2(elk, meta, scenario, ip_settings, hw_raw, task_hw):
    """Build cross-IP edges, routing to specific module nodes where possible."""
    eidx = 0

    # Build a map: (task_id, module_name) -> ELK node id
    # All I/O modules are now rendered in Level 2 (enabled + disabled)
    mod_node_map = {}
    for tid, hw_name in task_hw.items():
        raw = hw_raw.get(hw_name, {})
        for mod in raw.get('modules', []):
            mn = mod.get('name', '')
            mt = mod.get('type', 'Generic')
            if mt in _IO_MODULE_TYPES:
                mod_node_map[(tid, mn)] = f"{tid}_{_safe_id(mn)}"

    for src_id, dst_id, edge_data in scenario.graph.edges(data=True):
        conn_type = edge_data.get('conn_type', ConnectionType.M2M)
        port_pairs = edge_data.get('port_pairs', [])

        if conn_type == ConnectionType.OTF:
            # OTF: connect src_port module → dst_port module if possible
            if port_pairs and port_pairs[0][0] != 'output':
                for sp, dp in port_pairs:
                    src_node = mod_node_map.get((src_id, sp), src_id)
                    dst_node = mod_node_map.get((dst_id, dp), dst_id)
                    lbl = f"OTF: {sp}→{dp}"
                    eid = f"eo_{eidx}"
                    lw = max(len(lbl) * 6, 40)
                    elk["edges"].append({
                        "id": eid, "sources": [src_node], "targets": [dst_node],
                        "labels": [{"text": lbl, "width": lw, "height": 14}]
                    })
                    meta[eid] = {"type": "otf", "label": lbl}
                    eidx += 1
            else:
                eid = f"eo_{eidx}"
                elk["edges"].append({
                    "id": eid, "sources": [src_id], "targets": [dst_id],
                    "labels": [{"text": "OTF", "width": 30, "height": 14}]
                })
                meta[eid] = {"type": "otf", "label": "OTF"}
                eidx += 1
        else:
            is_sw = _is_sw_edge(scenario, src_id, dst_id)
            edge_type = "sw" if is_sw else "m2m"
            # M2M: connect src_port (WDMA) → dst_port (RDMA)
            src_s = ip_settings.get(src_id, {})
            dst_s = ip_settings.get(dst_id, {})
            src_out = {o.get('port', ''): o for o in src_s.get('outputs', [])}
            dst_in = {i.get('port', ''): i for i in dst_s.get('inputs', [])}

            if port_pairs and port_pairs[0][0] != 'output':
                for sp, dp in port_pairs:
                    src_node = mod_node_map.get((src_id, sp), src_id)
                    dst_node = mod_node_map.get((dst_id, dp), dst_id)
                    info = src_out.get(sp) or dst_in.get(dp) or {}
                    lines = [f"{sp}→{dp}"]
                    sz = info.get('size', [])
                    if len(sz) == 4 and sz[2] > 0:
                        lines.append(f"{sz[2]}×{sz[3]}")
                    if info.get('format'):
                        lines.append(info['format'])
                    if info.get('bitwidth'):
                        lines.append(f"{info['bitwidth']}bit")
                    if comp_enabled(info.get('comp')):
                        lines.append("COMP")
                    lbl = "\\n".join(lines)
                    buf_id = f"buf_{eidx}"
                    bw = max(len(l) * 7 for l in lines) + 24
                    bh = len(lines) * 13 + 10
                    elk["children"].append({"id": buf_id, "width": bw, "height": bh})
                    meta[buf_id] = {"type": "buffer", "label": lbl}
                    elk["edges"].append({"id": f"eb_{eidx}a", "sources": [src_node], "targets": [buf_id]})
                    elk["edges"].append({"id": f"eb_{eidx}b", "sources": [buf_id], "targets": [dst_node]})
                    meta[f"eb_{eidx}a"] = {"type": edge_type}
                    meta[f"eb_{eidx}b"] = {"type": edge_type}
                    eidx += 1
            else:
                eid = f"em_{eidx}"
                lbl = "SW" if is_sw else "M2M"
                elk["edges"].append({
                    "id": eid, "sources": [src_id], "targets": [dst_id],
                    "labels": [{"text": lbl, "width": 30, "height": 14}]
                })
                meta[eid] = {"type": edge_type, "label": lbl}
                eidx += 1


def generate_level2_html(hw_registry, scenario, hw_raw, output_path):
    """Level 2: Show only used CIN/COUT/DMA modules per IP for inter-IP connectivity.

    Improvements over basic I/O view:
    - Only modules referenced in ip_settings are shown (actually used)
    - Input modules (CIN, RDMA) placed at top; output modules (COUT, WDMA) at bottom
    - Cross-IP edges connect directly to module nodes, not IP packages
    """
    groups, task_hw, task_hier, task_ipg = _build_groups(scenario, hw_registry)
    ip_settings = getattr(scenario, '_ip_settings', {})
    meta = {}

    elk = _make_elk_root()

    for grp in _get_effective_hierarchy_order(groups):
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
                        "color": _get_hierarchy_color(grp),
                        "border": _get_hierarchy_border(grp)}

        for tid in groups[grp]:
            hw_name = task_hw[tid]
            hw = hw_registry.get(hw_name)
            raw = hw_raw.get(hw_name, {})
            all_modules = raw.get('modules', [])
            # Filter to I/O modules only
            io_modules = [m for m in all_modules
                          if m.get('type', 'Generic') in _IO_MODULE_TYPES]
            # Determine which modules are actually used in ip_settings
            used_ports = _get_used_port_names(tid, ip_settings)
            ipg = task_ipg[tid]
            ip_bg = _get_ip_group_color(ipg)

            if io_modules:
                # Separate into input-side and output-side modules
                input_mods = [m for m in io_modules
                              if _classify_dma_direction(m) == 'input']
                output_mods = [m for m in io_modules
                               if _classify_dma_direction(m) == 'output']

                ip_node = {
                    "id": tid,
                    "layoutOptions": {
                        "elk.padding": "[top=24,left=8,bottom=8,right=8]",
                        "elk.algorithm": "layered",
                        "elk.direction": "DOWN",
                        "elk.edgeRouting": "ORTHOGONAL",
                        "elk.spacing.nodeNode": "6",
                        "elk.layered.spacing.nodeNodeBetweenLayers": "10"
                    },
                    "children": [], "edges": []
                }
                ip_label = f"{hw_name} (BLK_{ipg})"
                ip_bd = _get_ip_group_border(ipg)
                meta[tid] = {"type": "ip", "label": ip_label, "color": ip_bg,
                             "border": ip_bd}

                # Add input modules first (with FIRST layer constraint)
                for mod in input_mods:
                    mn = mod.get('name', '?')
                    mt = mod.get('type', 'Generic')
                    mid = f"{tid}_{_safe_id(mn)}"
                    short = _mod_type_short(mt)
                    is_disabled = bool(used_ports) and mn not in used_ports
                    color = _l2_mod_color(mod, tid, ip_settings, is_disabled)
                    detail = _build_l2_module_detail(mod, tid, ip_settings, is_disabled)
                    w = max(len(mn) * 7 + 16, 65)
                    ip_node["children"].append({
                        "id": mid, "width": w, "height": 30,
                        "layoutOptions": {
                            "elk.layered.layerConstraint": "FIRST"
                        }
                    })
                    meta[mid] = {"type": "mod", "label": mn, "sub": short,
                                 "color": color, "border": _darken_hex(color),
                                 "disabled": is_disabled, "detail": detail}

                # Add output modules (with LAST layer constraint)
                for mod in output_mods:
                    mn = mod.get('name', '?')
                    mt = mod.get('type', 'Generic')
                    mid = f"{tid}_{_safe_id(mn)}"
                    short = _mod_type_short(mt)
                    is_disabled = bool(used_ports) and mn not in used_ports
                    color = _l2_mod_color(mod, tid, ip_settings, is_disabled)
                    detail = _build_l2_module_detail(mod, tid, ip_settings, is_disabled)
                    w = max(len(mn) * 7 + 16, 65)
                    ip_node["children"].append({
                        "id": mid, "width": w, "height": 30,
                        "layoutOptions": {
                            "elk.layered.layerConstraint": "LAST"
                        }
                    })
                    meta[mid] = {"type": "mod", "label": mn, "sub": short,
                                 "color": color, "border": _darken_hex(color),
                                 "disabled": is_disabled, "detail": detail}

                # Add internal edges from input modules to output modules
                io_names = {m.get('name') for m in io_modules}
                for ie in raw.get('edges', []):
                    s_name = ie.get('src', '')
                    d_name = ie.get('dst', '')
                    if s_name in io_names and d_name in io_names:
                        s = f"{tid}_{_safe_id(s_name)}"
                        d = f"{tid}_{_safe_id(d_name)}"
                        eid = f"ie_{s}_{d}"
                        ip_node["edges"].append({
                            "id": eid, "sources": [s], "targets": [d]})
                        meta[eid] = {"type": "int"}

                # If no internal edges but both input and output exist,
                # create implicit edges from each input to each output
                if not ip_node["edges"] and input_mods and output_mods:
                    for im in input_mods:
                        for om in output_mods:
                            s = f"{tid}_{_safe_id(im.get('name', ''))}"
                            d = f"{tid}_{_safe_id(om.get('name', ''))}"
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
                ip_bd = _get_ip_group_border(ipg)
                meta[tid] = {"type": "leaf", "label": lbl, "color": ip_bg,
                             "border": ip_bd}

        elk["children"].append(grp_node)

    # Cross-IP edges: route to module nodes
    _build_cross_edges_level2(elk, meta, scenario, ip_settings, hw_raw, task_hw)

    _render_html("Level 2 View (I/O Modules)", elk, meta, output_path)
    print(f"Level 2 HTML -> {output_path}")


# ═══════════════════════════════════════════════════════════════════
#  Level 3: Full module detail (all modules + intra-IP edges)
# ═══════════════════════════════════════════════════════════════════

def generate_level3_html(hw_registry, scenario, hw_raw, output_path):
    """Level 3: Show all modules and intra-IP edges."""
    groups, task_hw, task_hier, task_ipg = _build_groups(scenario, hw_registry)
    ip_settings = getattr(scenario, '_ip_settings', {})
    meta = {}

    elk = _make_elk_root()

    for grp in _get_effective_hierarchy_order(groups):
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
                        "color": _get_hierarchy_color(grp),
                        "border": _get_hierarchy_border(grp)}

        for tid in groups[grp]:
            hw_name = task_hw[tid]
            hw = hw_registry.get(hw_name)
            raw = hw_raw.get(hw_name, {})
            modules = raw.get('modules', [])
            ipg = task_ipg[tid]
            ip_bg = _get_ip_group_color(ipg)

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
                ip_bd = _get_ip_group_border(ipg)
                meta[tid] = {"type": "ip", "label": hw_name, "color": ip_bg,
                             "border": ip_bd}

                for mod in modules:
                    mn = mod.get('name', '?')
                    mt = mod.get('type', 'Generic')
                    mid = f"{tid}_{_safe_id(mn)}"
                    short = _mod_type_short(mt)
                    w = max(len(mn) * 7 + 16, 65)
                    ip_node["children"].append({"id": mid, "width": w, "height": 30})
                    mc = _mod_color(mt)
                    meta[mid] = {"type": "mod", "label": mn, "sub": short,
                                 "color": mc, "border": _darken_hex(mc)}

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
                ip_bd = _get_ip_group_border(ipg)
                meta[tid] = {"type": "leaf", "label": lbl, "color": ip_bg,
                             "border": ip_bd}

        elk["children"].append(grp_node)

    _build_cross_edges(elk, meta, scenario, ip_settings)

    _render_html("Level 3 View (Full Module Detail)", elk, meta, output_path)
    print(f"Level 3 HTML -> {output_path}")


# ═══════════════════════════════════════════════════════════════════
#  Task Topology: flat task DAG (no hierarchy grouping)
# ═══════════════════════════════════════════════════════════════════

def generate_task_topology_html(hw_registry, scenario, output_path):
    """Generate a flat task topology HTML view showing the task DAG."""
    from src.view.plantuml_view import _get_hierarchy
    ip_settings = getattr(scenario, '_ip_settings', {})
    meta = {}

    elk = _make_elk_root()

    # Add all tasks as flat nodes (no grouping)
    for task in scenario.get_tasks():
        tid = task.task_id
        hw_name = task.mapped_hw
        hw = hw_registry.get(hw_name)
        hier = _get_hierarchy(hw, hw_name) if hw else "Other"
        bg = _get_hierarchy_color(hier)

        in_str, out_str, badges = _get_ip_size_info(tid, hw, ip_settings)
        if in_str:
            if out_str and out_str != in_str:
                lbl = f"{tid}\\n({hw_name})\\n{in_str}\u2192{out_str}"
            else:
                lbl = f"{tid}\\n({hw_name})\\n{in_str}"
        else:
            lbl = f"{tid}\\n({hw_name})"

        size_changed = in_str and out_str and out_str != in_str
        h = 62 if size_changed else (50 if in_str else 45)
        w = max(len(tid) * 8 + 20, len(hw_name) * 8 + 40, 120)
        if size_changed:
            w = max(w, (len(in_str) + len(out_str) + 4) * 7 + 20)
        elif in_str:
            w = max(w, len(in_str) * 7 + 20)
        elk["children"].append({"id": tid, "width": w, "height": h})
        bd = _get_hierarchy_border(hier)
        # Build detail info for tooltip popup
        detail = _build_port_detail(tid, hw_name, hw, ip_settings, scenario)
        meta[tid] = {"type": "leaf", "label": lbl, "color": bg, "border": bd,
                     "detail": detail, "badges": badges}

    # Edges
    _build_cross_edges(elk, meta, scenario, ip_settings)

    _render_html("Task Topology", elk, meta, output_path)
    print(f"Task Topology HTML -> {output_path}")


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
#tooltip{position:fixed;max-width:480px;background:#fff;border:1px solid #bbb;
  border-radius:8px;box-shadow:0 4px 20px rgba(0,0,0,.2);padding:12px 16px;
  font-size:12px;color:#333;z-index:100;pointer-events:auto;line-height:1.5}
#tooltip h3{margin:0 0 8px;font-size:14px;color:#222;border-bottom:1px solid #eee;padding-bottom:6px}
#tooltip .port-section{margin:6px 0 4px;font-weight:bold;color:#1565C0}
#tooltip .port-section.out{color:#E65100}
#tooltip table{width:100%;border-collapse:collapse;margin:2px 0 8px}
#tooltip th,#tooltip td{text-align:left;padding:2px 8px 2px 0;font-size:11px}
#tooltip th{color:#666;font-weight:600}
#tooltip td{color:#333}
#tooltip .close-btn{position:absolute;top:6px;right:10px;cursor:pointer;
  font-size:16px;color:#999;line-height:1}
#tooltip .close-btn:hover{color:#333}
</style></head><body>
<div id="wrap">
<h1>/*__TITLE__*/</h1>
<div id="info">Scroll to zoom · Drag to pan · Arrow keys to scroll</div>
<div id="canvas"></div>
<div id="tooltip" style="display:none;"></div>
</div>
<script src="https://cdn.jsdelivr.net/npm/elkjs@0.9.3/lib/elk.bundled.js"></script>
<script>
const G=/*__GRAPH__*/;
const M=/*__META__*/;
const NS='http://www.w3.org/2000/svg';
/* Map of node id -> absolute position {x,y} built during first pass */
const NP={};
let _dragDist=0; // global: track drag distance to distinguish click vs drag

function ce(t,a){const e=document.createElementNS(NS,t);if(a)Object.entries(a).forEach(([k,v])=>e.setAttribute(k,v));return e}

async function main(){
  try{
  const elk=new ELK();
  const layout=await elk.layout(G);
  const pad=30;
  const totalW=layout.width+pad*2, totalH=layout.height+pad*2;
  /* Use viewBox for scalability; clamp display size for very large diagrams */
  const dispW=Math.min(totalW,window.innerWidth-40);
  const dispH=Math.min(totalH,window.innerHeight-80);
  const svg=ce('svg',{width:dispW,height:dispH,viewBox:`0 0 ${totalW} ${totalH}`});
  svg.setAttribute('tabindex','0');
  const gMain=ce('g',{transform:`translate(${pad},${pad})`});
  svg.appendChild(gMain);
  /* Pass 1: collect absolute positions for all nodes */
  collectPositions(layout,0,0);
  /* Pass 2: draw nodes and edges */
  drawNode(gMain,layout,0,0);
  // zoom/pan
  let scale=1,tx=0,ty=0;
  function updateTx(){gMain.setAttribute('transform',`translate(${tx+pad},${ty+pad}) scale(${scale})`)}
  function svgPt(e){const p=svg.createSVGPoint();p.x=e.clientX;p.y=e.clientY;const ctm=svg.getScreenCTM();if(ctm){const inv=ctm.inverse();return p.matrixTransform(inv)}return{x:e.offsetX,y:e.offsetY}}
  svg.addEventListener('wheel',e=>{e.preventDefault();const d=e.deltaY>0?0.9:1.1;const ns=Math.max(0.2,Math.min(5,scale*d));const r=ns/scale;const pt=svgPt(e);tx=pt.x-(pt.x-tx)*r;ty=pt.y-(pt.y-ty)*r;scale=ns;updateTx()});
  let drag=false,sx,sy;
  svg.addEventListener('mousedown',e=>{drag=true;_dragDist=0;sx=e.clientX-tx;sy=e.clientY-ty;svg.focus()});
  svg.addEventListener('mousemove',e=>{if(!drag)return;_dragDist++;tx=e.clientX-sx;ty=e.clientY-sy;updateTx()});
  svg.addEventListener('mouseup',()=>drag=false);
  svg.addEventListener('mouseleave',()=>drag=false);
  // Arrow key panning
  const PAN_STEP=50;
  svg.addEventListener('keydown',e=>{const k=e.key;if(k==='ArrowLeft'){tx+=PAN_STEP;e.preventDefault()}else if(k==='ArrowRight'){tx-=PAN_STEP;e.preventDefault()}else if(k==='ArrowUp'){ty+=PAN_STEP;e.preventDefault()}else if(k==='ArrowDown'){ty-=PAN_STEP;e.preventDefault()}else return;updateTx()});
  document.getElementById('canvas').appendChild(svg);
  }catch(err){
    document.getElementById('canvas').innerHTML=
      '<p style="color:red;padding:20px">ELK layout error: '+err.message+'</p>';
    console.error('ELK layout failed:',err);
  }
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
    const isDis=!!m.disabled;
    const rr=m.type==='group'?8:m.type==='ip'?6:isGrpBox?10:3;
    const sw=m.type==='group'||isGrpBox?1.5:1;
    const fo=m.type==='group'?'0.55':isGrpBox?'0.7':isDis?'0.5':'1';
    const sc=m.border||((m.type==='m2m'||m.type==='sw')?'#E65100':'#999');
    const rAttrs={x,y,width:node.width,height:node.height,rx:rr,ry:rr,
      fill:m.color||'#fff','fill-opacity':fo,stroke:sc,'stroke-width':sw};
    if(isDis)rAttrs['stroke-dasharray']='4,2';
    const r=ce('rect',rAttrs);
    g.appendChild(r);
    // label
    const lines=(m.label||'').split('\\n');
    const txtColor=isDis?'#AAA':'#333';
    const subColor=isDis?'#CCC':'#666';
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
      const t=ce('text',{x:x+node.width/2,y:y+12,'text-anchor':'middle','font-size':9,fill:txtColor});
      t.textContent=m.label;g.appendChild(t);
      if(m.sub){
        const t2=ce('text',{x:x+node.width/2,y:y+23,'text-anchor':'middle','font-size':8,fill:subColor});
        t2.textContent=m.sub;g.appendChild(t2);
      }
    }else if(m.type==='buffer'){
      // Buffer node: rounded-rect with distinct lavender tone
      const r=ce('rect',{x,y,width:node.width,height:node.height,rx:4,ry:4,
        fill:'#EDE7F6',stroke:'#7E57C2','stroke-width':1.2,'stroke-dasharray':'4,2'});
      g.appendChild(r);
      lines.forEach((ln,i)=>{
        const t=ce('text',{x:x+node.width/2,y:y+node.height/2+3+(i-(lines.length-1)/2)*12,
          'text-anchor':'middle','font-size':9,fill:'#4A148C'});
        if(i===0)t.setAttribute('font-weight','bold');
        t.textContent=ln;g.appendChild(t);
      });
    }else{
      lines.forEach((ln,i)=>{
        const t=ce('text',{x:x+node.width/2,y:y+node.height/2+4+(i-(lines.length-1)/2)*14,
          'text-anchor':'middle','font-size':11,fill:'#333'});
        if(i===0)t.setAttribute('font-weight','bold');
        t.textContent=ln;g.appendChild(t);
      });
      // S/C badges: small superscript circles in top-right corner
      if(m.badges&&m.badges.length){
        const bColors={S:'#E65100',C:'#1565C0'};
        const bLabels={S:'S',C:'C'};
        let bx=x+node.width-6;
        const by=y+6;
        m.badges.slice().reverse().forEach(b=>{
          const bc=bColors[b]||'#666';
          const r=ce('circle',{cx:bx,cy:by,r:7,fill:bc,'fill-opacity':'0.92',stroke:'white','stroke-width':1});
          g.appendChild(r);
          const t=ce('text',{x:bx,y:by+4,'text-anchor':'middle','font-size':8,'font-weight':'bold',fill:'white'});
          t.textContent=bLabels[b]||b;g.appendChild(t);
          bx-=16;
        });
      }
    }
    // Click handler for tooltip popup (if detail exists)
    if(m.detail){
      r.style.cursor='pointer';
      r.addEventListener('click',ev=>{
        if(_dragDist>3)return; // ignore drag
        ev.stopPropagation();
        showTooltip(ev, m);
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
  const isSw=m.type==='sw';
  const color=isOtf?'#1565C0':isSw?'#F9A825':isM2m?'#E65100':'#999';
  const sw=isOtf?2.5:(isM2m||isSw)?2:1;
  const dash=(isM2m||isSw)?'6,3':'';

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

/* ── Tooltip popup ───────────────────────────────── */
const tip=document.getElementById('tooltip');
function showTooltip(ev, m){
  if(!m.detail)return;
  const d=m.detail;
  let h=`<span class="close-btn" onclick="hideTooltip()">&times;</span>`;
  // Module-level detail (DMA / CIN / COUT)
  if(d.mod_type){
    const sc=d.status==='Enabled'?'#2E7D32':'#B71C1C';
    h+=`<h3>${d.name} <span style="font-size:11px;color:${sc}">[${d.status}]</span></h3>`;
    if(d.mod_type==='dma'){
      h+='<table>';
      h+=`<tr><th>Direction</th><td>${d.direction}</td></tr>`;
      h+=`<tr><th>Bandwidth</th><td>${d.bandwidth}</td></tr>`;
      h+=`<tr><th>MO</th><td>${d.mo}</td></tr>`;
      h+=`<tr><th>Size</th><td>${d.size}</td></tr>`;
      h+=`<tr><th>Format</th><td>${d.format}</td></tr>`;
      h+=`<tr><th>Bitwidth</th><td>${d.bitwidth||'-'}</td></tr>`;
      h+=`<tr><th>Comp</th><td>${d.comp||'-'}</td></tr>`;
      h+=`<tr><th>SBWC</th><td>${d.sbwc}</td></tr>`;
      h+=`<tr><th>LLC</th><td>${d.llc}</td></tr>`;
      h+='</table>';
    } else {
      h+='<table>';
      h+=`<tr><th>Type</th><td>${d.mod_type==='cin'?'CIN (Input FIFO)':'COUT (Output FIFO)'}</td></tr>`;
      h+=`<tr><th>Size</th><td>${d.size}</td></tr>`;
      h+='</table>';
    }
  } else {
    // IP-level detail (Level 1 style)
    h+=`<h3>${d.hw||d.label||'Detail'}`;
    if(d.mode) h+=` <span style="font-size:11px;color:#1565C0;font-weight:normal">[Mode: ${d.mode}]</span>`;
    h+=`</h3>`;
    if(d.inputs&&d.inputs.length){
      h+=`<div class="port-section">▼ Inputs (${d.inputs.length})</div><table><tr><th>Port</th><th>Size</th><th>Format</th><th>Bit</th><th>Comp</th></tr>`;
      d.inputs.forEach(p=>{
        const sz=p.size||'-';
        h+=`<tr><td>${p.port}</td><td>${sz}</td><td>${p.format||'-'}</td><td>${p.bitwidth||'-'}</td><td>${p.comp||'-'}</td></tr>`;
      });
      h+='</table>';
    }
    if(d.outputs&&d.outputs.length){
      h+=`<div class="port-section out">▲ Outputs (${d.outputs.length})</div><table><tr><th>Port</th><th>Size</th><th>Format</th><th>Bit</th><th>Comp</th></tr>`;
      d.outputs.forEach(p=>{
        const sz=p.size||'-';
        h+=`<tr><td>${p.port}</td><td>${sz}</td><td>${p.format||'-'}</td><td>${p.bitwidth||'-'}</td><td>${p.comp||'-'}</td></tr>`;
      });
      h+='</table>';
    }
    if(!d.inputs?.length&&!d.outputs?.length) h+='<div style="color:#999">No port info available</div>';
  }
  tip.innerHTML=h;
  tip.style.display='block';
  // Position near click
  const mx=ev.clientX+12, my=ev.clientY+12;
  const tw=tip.offsetWidth, th2=tip.offsetHeight;
  const ww=window.innerWidth, wh=window.innerHeight;
  tip.style.left=Math.min(mx,ww-tw-16)+'px';
  tip.style.top=Math.min(my,wh-th2-16)+'px';
}
function hideTooltip(){tip.style.display='none'}
// Click outside to close
document.addEventListener('click',e=>{if(!tip.contains(e.target))hideTooltip()});
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
    generate_level3_html(hw_registry, scenario, hw_raw, f"{out_dir}/scenario_level3.html")
    print("\nAll 4 HTML diagrams generated!")
