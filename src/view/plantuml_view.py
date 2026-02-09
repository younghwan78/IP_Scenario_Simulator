"""Generate multi-level PlantUML scenario flow diagrams grouped by hierarchy_group.

Generates 3 levels:
  - Top:    hierarchy_group blocks only (Sensor, ISP, CODEC, DPU, CPU, MEMORY)
  - Level1: IP-level detail within hierarchy_groups
  - Level2: Module-level detail within IPs

M2M paths are shown as individual memory (cylinder) shapes.
OTF paths are shown as thick arrows.
"""
import yaml
import sys
import os
sys.path.insert(0, '.')
from main import load_hw_config, create_hw_node, load_scenario_config, create_scenario, apply_scenario_settings
from src.model.hw_nodes import IPNode, SensorNode
from src.model.scenario import ConnectionType


# ── Shared constants ────────────────────────────────────────────────

HIERARCHY_COLORS = {
    "Sensor":  "#E3F2FD",
    "ISP":     "#E8F5E9",
    "CODEC":   "#FFF3E0",
    "DPU":     "#F3E5F5",
    "CPU":     "#ECEFF1",
    "MEMORY":  "#FFF9C4",
    "Other":   "#FAFAFA",
}

IP_GROUP_COLORS = {
    "CSIS":  "#BBDEFB",
    "BYRP":  "#C8E6C9",
    "RGBP":  "#C8E6C9",
    "YUVSC": "#C8E6C9",
    "MSNR":  "#A5D6A7",
    "MTNR1": "#A5D6A7",
    "MCSC":  "#81C784",
    "MFC":   "#FFE0B2",
    "DPU":   "#E1BEE7",
}

HIERARCHY_ORDER = ["Sensor", "ISP", "CODEC", "DPU", "CPU", "MEMORY", "Other"]


def _safe_id(name):
    return name.replace("-", "_").replace(".", "_")


def _skinparam():
    return """!theme plain
skinparam backgroundColor #FEFEFE
skinparam packageStyle rectangle
skinparam defaultTextAlignment center
skinparam linetype ortho
skinparam padding 6
skinparam rectangle {
    RoundCorner 10
    FontSize 11
}
skinparam database {
    FontSize 9
    BackgroundColor #FFF3E0
    BorderColor #E65100
}
skinparam package {
    FontSize 13
    FontStyle bold
}
"""



def _load_data(hw_path, sc_path):
    hw_config = load_hw_config(hw_path)
    hw_list = hw_config if isinstance(hw_config, list) else hw_config.get('hardware', [])
    hw_nodes = [create_hw_node(cfg) for cfg in hw_list]
    hw_registry = {n.name: n for n in hw_nodes}

    sc_config = load_scenario_config(sc_path)
    apply_scenario_settings(hw_registry, sc_config)
    scenario = create_scenario(sc_config)

    # Also keep raw hw config for module info
    hw_raw = {item['name']: item for item in hw_list}

    return hw_registry, scenario, hw_raw


def _get_hierarchy(hw, hw_name):
    if isinstance(hw, SensorNode):
        return "Sensor"
    elif isinstance(hw, IPNode):
        return hw.hierarchy_group if hw.hierarchy_group else "Other"
    else:
        cls = hw.__class__.__name__
        if 'Processor' in cls:
            return "CPU"
        elif 'Memory' in cls:
            return "MEMORY"
        return "Other"


def _get_ip_group(hw, hw_name):
    if isinstance(hw, IPNode):
        return hw.ip_group if hw.ip_group else hw_name
    return hw_name


def _build_groups(scenario, hw_registry):
    """Build task groupings by hierarchy_group and ip_group."""
    groups = {}
    task_hw = {}
    task_hier = {}
    task_ipg = {}

    for task in scenario.get_tasks():
        hw = hw_registry.get(task.mapped_hw)
        hw_name = task.mapped_hw
        task_hw[task.task_id] = hw_name
        hier = _get_hierarchy(hw, hw_name) if hw else "Other"
        ipg = _get_ip_group(hw, hw_name) if hw else hw_name
        task_hier[task.task_id] = hier
        task_ipg[task.task_id] = ipg
        groups.setdefault(hier, []).append(task.task_id)

    return groups, task_hw, task_hier, task_ipg


# ── Edge emitters ───────────────────────────────────────────────

def _emit_edges_top(lines, scenario, task_hier):
    """Emit edges at top level with M2M detail (size/format/bitwidth/comp)."""
    m2m_idx = 0
    seen_otf = set()

    for src_id, dst_id, edge_data in scenario.graph.edges(data=True):
        src_h = task_hier.get(src_id, "Other")
        dst_h = task_hier.get(dst_id, "Other")
        conn_type = edge_data.get('conn_type', ConnectionType.M2M)
        port_pairs = edge_data.get('port_pairs', [])

        src_alias = f"pkg_{src_h}"
        dst_alias = f"pkg_{dst_h}"

        if conn_type == ConnectionType.OTF:
            otf_key = (src_h, dst_h)
            if otf_key not in seen_otf:
                seen_otf.add(otf_key)
                lines.append(f'{src_alias} ==> {dst_alias} : OTF')
        else:
            ip_settings = getattr(scenario, '_ip_settings', {})
            src_settings = ip_settings.get(src_id, {})
            dst_settings = ip_settings.get(dst_id, {})
            src_outputs = {o.get('port', ''): o for o in src_settings.get('outputs', [])}
            dst_inputs = {i.get('port', ''): i for i in dst_settings.get('inputs', [])}

            if port_pairs and port_pairs[0][0] != 'output':
                for sp, dp in port_pairs:
                    mem_id = f"mem_{m2m_idx}"
                    detail = _m2m_detail_label(sp, dp, src_outputs.get(sp), dst_inputs.get(dp))
                    lines.append(f'database "{detail}" as {mem_id}')
                    lines.append(f'{src_alias} --> {mem_id}')
                    lines.append(f'{mem_id} --> {dst_alias}')
                    m2m_idx += 1
            else:
                mem_id = f"mem_{m2m_idx}"
                lines.append(f'database "M2M" as {mem_id}')
                lines.append(f'{src_alias} --> {mem_id}')
                lines.append(f'{mem_id} --> {dst_alias}')
                m2m_idx += 1


def _m2m_detail_label(src_port, dst_port, src_info, dst_info):
    """Build a detailed label for an M2M memory cylinder."""
    info = src_info or dst_info or {}
    parts = [f"<b>{src_port} -> {dst_port}</b>"]

    size = info.get('size', [])
    if len(size) == 4 and size[2] > 0:
        parts.append(f"{size[2]}x{size[3]}")

    fmt = info.get('format', '')
    if fmt:
        parts.append(fmt)

    bw = info.get('bitwidth', 0)
    if bw:
        parts.append(f"{bw}bit")

    comp = info.get('comp', '')
    if comp == 'enable':
        parts.append("<color:red>COMP</color>")

    return "\\n".join(parts)


def _emit_edges_level1(lines, scenario, task_hw):
    """Emit edges at IP level (task to task)."""
    m2m_idx = 0
    for src_id, dst_id, edge_data in scenario.graph.edges(data=True):
        conn_type = edge_data.get('conn_type', ConnectionType.M2M)
        port_pairs = edge_data.get('port_pairs', [])

        if conn_type == ConnectionType.OTF:
            port_label = " : OTF"
            if port_pairs and port_pairs[0][0] != 'output':
                if len(port_pairs) == 1:
                    port_label = f" : OTF\\n{port_pairs[0][0]}->{port_pairs[0][1]}"
                else:
                    pair_strs = [f"{sp}->{dp}" for sp, dp in port_pairs]
                    port_label = " : OTF\\n" + "\\n".join(pair_strs)
            lines.append(f'{_safe_id(src_id)} ==> {_safe_id(dst_id)}{port_label}')
        else:
            if port_pairs and port_pairs[0][0] != 'output':
                for sp, dp in port_pairs:
                    mem_id = f"mem_{m2m_idx}"
                    lines.append(f'database "{sp}\\n->{dp}" as {mem_id}')
                    lines.append(f'{_safe_id(src_id)} --> {mem_id}')
                    lines.append(f'{mem_id} --> {_safe_id(dst_id)}')
                    m2m_idx += 1
            else:
                mem_id = f"mem_{m2m_idx}"
                lines.append(f'database "M2M" as {mem_id}')
                lines.append(f'{_safe_id(src_id)} --> {mem_id}')
                lines.append(f'{mem_id} --> {_safe_id(dst_id)}')
                m2m_idx += 1


# ═══════════════════════════════════════════════════════════════════
#  Top View: hierarchy_group blocks only
# ═══════════════════════════════════════════════════════════════════

def generate_top_view(hw_registry, scenario, output_path):
    groups, task_hw, task_hier, task_ipg = _build_groups(scenario, hw_registry)
    lines = ["@startuml", _skinparam(), "title Top View (Hierarchy Groups)\\n", ""]

    for grp in HIERARCHY_ORDER:
        if grp not in groups:
            continue
        bg = HIERARCHY_COLORS.get(grp, "#FAFAFA")
        task_ids = groups[grp]
        ip_names = [task_hw[tid] for tid in task_ids]
        ip_list = ", ".join(ip_names)
        lines.append(f'package "{grp}" as pkg_{grp} {bg} {{')
        lines.append(f'    note as N_{grp}')
        lines.append(f'        {ip_list}')
        lines.append(f'    end note')
        lines.append("}")
        lines.append("")

    lines.append("' === Connections ===")
    _emit_edges_top(lines, scenario, task_hier)
    lines.append("")
    lines.append("@enduml")

    with open(output_path, 'w', encoding='utf-8') as f:
        f.write("\n".join(lines))
    print(f"Top view -> {output_path}")


# ═══════════════════════════════════════════════════════════════════
#  Level 1: IP-level detail
# ═══════════════════════════════════════════════════════════════════

def generate_level1(hw_registry, scenario, output_path):
    groups, task_hw, task_hier, task_ipg = _build_groups(scenario, hw_registry)
    lines = ["@startuml", _skinparam(), "title Level 1 View (IP Detail)\\n", ""]

    for grp in HIERARCHY_ORDER:
        if grp not in groups:
            continue
        bg = HIERARCHY_COLORS.get(grp, "#FAFAFA")
        task_ids = groups[grp]
        lines.append(f'package "{grp}" as pkg_{grp} {bg} {{')

        # Sub-group by ip_group
        ip_subgroups = {}
        for tid in task_ids:
            ipg = task_ipg[tid]
            ip_subgroups.setdefault(ipg, []).append(tid)

        for ipg, tids in ip_subgroups.items():
            ip_bg = IP_GROUP_COLORS.get(ipg, "#E0E0E0")
            if len(tids) > 1:
                lines.append(f'    package "{ipg}" as ipg_{_safe_id(ipg)} {ip_bg} {{')
                for tid in tids:
                    hw_name = task_hw[tid]
                    hw = hw_registry.get(hw_name)
                    label = _ip_label(hw_name, hw, scenario, tid)
                    lines.append(f'        rectangle "{label}" as {_safe_id(tid)}')
                lines.append("    }")
            else:
                tid = tids[0]
                hw_name = task_hw[tid]
                hw = hw_registry.get(hw_name)
                label = _ip_label(hw_name, hw, scenario, tid)
                lines.append(f'    rectangle "{label}" as {_safe_id(tid)} {ip_bg}')

        lines.append("}")
        lines.append("")

    lines.append("' === Connections ===")
    _emit_edges_level1(lines, scenario, task_hw)
    lines.append("")
    lines.append("@enduml")

    with open(output_path, 'w', encoding='utf-8') as f:
        f.write("\n".join(lines))
    print(f"Level 1 -> {output_path}")


# ═══════════════════════════════════════════════════════════════════
#  Level 2: Module-level detail
# ═══════════════════════════════════════════════════════════════════

def _emit_edges_level2(lines, scenario, task_hw):
    """Emit edges at Level 2 with M2M detail (size/format/bitwidth/comp)."""
    m2m_idx = 0
    for src_id, dst_id, edge_data in scenario.graph.edges(data=True):
        conn_type = edge_data.get('conn_type', ConnectionType.M2M)
        port_pairs = edge_data.get('port_pairs', [])

        if conn_type == ConnectionType.OTF:
            port_label = " : OTF"
            if port_pairs and port_pairs[0][0] != 'output':
                if len(port_pairs) == 1:
                    port_label = f" : OTF\\n{port_pairs[0][0]}->{port_pairs[0][1]}"
                else:
                    pair_strs = [f"{sp}->{dp}" for sp, dp in port_pairs]
                    port_label = " : OTF\\n" + "\\n".join(pair_strs)
            lines.append(f'{_safe_id(src_id)} ==> {_safe_id(dst_id)}{port_label}')
        else:
            ip_settings = getattr(scenario, '_ip_settings', {})
            src_settings = ip_settings.get(src_id, {})
            dst_settings = ip_settings.get(dst_id, {})
            src_outputs = {o.get('port', ''): o for o in src_settings.get('outputs', [])}
            dst_inputs = {i.get('port', ''): i for i in dst_settings.get('inputs', [])}

            if port_pairs and port_pairs[0][0] != 'output':
                for sp, dp in port_pairs:
                    mem_id = f"mem_{m2m_idx}"
                    detail = _m2m_detail_label(sp, dp, src_outputs.get(sp), dst_inputs.get(dp))
                    lines.append(f'database "{detail}" as {mem_id}')
                    lines.append(f'{_safe_id(src_id)} --> {mem_id}')
                    lines.append(f'{mem_id} --> {_safe_id(dst_id)}')
                    m2m_idx += 1
            else:
                mem_id = f"mem_{m2m_idx}"
                lines.append(f'database "M2M" as {mem_id}')
                lines.append(f'{_safe_id(src_id)} --> {mem_id}')
                lines.append(f'{mem_id} --> {_safe_id(dst_id)}')
                m2m_idx += 1


def generate_level2(hw_registry, scenario, hw_raw, output_path):
    groups, task_hw, task_hier, task_ipg = _build_groups(scenario, hw_registry)
    lines = ["@startuml", _skinparam()]
    lines.append("top to bottom direction")
    lines.append("skinparam rectangle {")
    lines.append("    FontSize 9")
    lines.append("}")
    lines.append("skinparam package {")
    lines.append("    padding 10")
    lines.append("}")
    lines.append("title Level 2 View (Module Detail)\\n")
    lines.append("")

    # Collect ordered task IDs per group for hidden links
    grp_tids = {}

    for grp in HIERARCHY_ORDER:
        if grp not in groups:
            continue
        bg = HIERARCHY_COLORS.get(grp, "#FAFAFA")
        task_ids = groups[grp]
        lines.append(f'package "{grp}" as pkg_{grp} {bg} {{')

        # Sub-group by ip_group
        ip_subgroups = {}
        for tid in task_ids:
            ipg = task_ipg[tid]
            ip_subgroups.setdefault(ipg, []).append(tid)

        ordered_tids = []
        for ipg, tids in ip_subgroups.items():
            ip_bg = IP_GROUP_COLORS.get(ipg, "#E0E0E0")

            for tid in tids:
                hw_name = task_hw[tid]
                hw = hw_registry.get(hw_name)
                raw = hw_raw.get(hw_name, {})
                modules = raw.get('modules', [])
                ordered_tids.append(tid)

                if modules:
                    # Use _safe_id(tid) as alias so edges connect to packages
                    lines.append(f'    package "{hw_name}" as {_safe_id(tid)} {ip_bg} {{')
                    for mod in modules:
                        mod_name = mod.get('name', '?')
                        mod_type = mod.get('type', 'Generic')
                        mod_id = f"{_safe_id(tid)}_{_safe_id(mod_name)}"
                        mod_color = _mod_color(mod_type)
                        short_type = _mod_type_short(mod_type)
                        lines.append(f'        rectangle "{mod_name}\\n<size:8>{short_type}</size>" as {mod_id} {mod_color}')
                    # Show intra-IP edges if defined
                    edges = raw.get('edges', [])
                    for edge in edges:
                        e_src = f"{_safe_id(tid)}_{_safe_id(edge.get('src', ''))}"
                        e_dst = f"{_safe_id(tid)}_{_safe_id(edge.get('dst', ''))}"
                        lines.append(f'        {e_src} --> {e_dst}')
                    lines.append("    }")
                else:
                    label = _ip_label(hw_name, hw, scenario, tid)
                    lines.append(f'    rectangle "{label}" as {_safe_id(tid)} {ip_bg}')

        grp_tids[grp] = ordered_tids
        lines.append("}")
        lines.append("")

    # Hidden links between hierarchy groups (top-down ordering)
    lines.append("' === Hidden links for vertical ordering ===")
    active_grps = [g for g in HIERARCHY_ORDER if g in grp_tids]
    for i in range(len(active_grps) - 1):
        lines.append(f'pkg_{active_grps[i]} -[hidden]down-> pkg_{active_grps[i+1]}')

    lines.append("")
    lines.append("' === Connections (Task Topology) ===")
    _emit_edges_level2(lines, scenario, task_hw)
    lines.append("")
    lines.append("@enduml")

    with open(output_path, 'w', encoding='utf-8') as f:
        f.write("\n".join(lines))
    print(f"Level 2 -> {output_path}")


# ── Helpers ──────────────────────────────────────────────────────

def _ip_label(hw_name, hw, scenario, tid):
    label = f"<b>{hw_name}</b>"
    ip_settings = getattr(scenario, '_ip_settings', {}).get(tid, {})
    inputs = ip_settings.get('inputs', [])
    if inputs:
        size = inputs[0].get('size', [])
        if len(size) == 4 and size[2] > 0:
            label += f"\\n{size[2]}x{size[3]}"
    elif isinstance(hw, SensorNode):
        label += f"\\n{hw.frame_width}x{hw.frame_height}@{hw.fps:.0f}fps"
    return label


def _mod_color(mod_type):
    return {
        'DMA_READ':  '#BBDEFB',
        'DMA_WRITE': '#BBDEFB',
        'DMA':       '#BBDEFB',
        'Scaler':    '#C5E1A5',
        'Crop':      '#FFE082',
        'CIN':       '#B0BEC5',
        'COUT':      '#B0BEC5',
    }.get(mod_type, '#FFFFFF')


def _mod_type_short(mod_type):
    return {
        'DMA_READ':  'RDMA',
        'DMA_WRITE': 'WDMA',
        'DMA':       'DMA',
        'Scaler':    'Scaler',
        'Crop':      'Crop',
        'CIN':       'CIN',
        'COUT':      'COUT',
        'Generic':   '',
    }.get(mod_type, '')


# ── Main ─────────────────────────────────────────────────────────

if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="Generate scenario diagrams")
    parser.add_argument("--hw", default="hw_config/projectA_hw.yaml",
                        help="HW config YAML path")
    parser.add_argument("--sc", default="scenario_config/projectA_FHD30_recording_scenario.yaml",
                        help="Scenario config YAML path")
    parser.add_argument("--output-dir", "-o", default="output",
                        help="Output directory (default: output)")
    parser.add_argument("--format", "-f", choices=["puml", "html"], default="puml",
                        help="Output format: puml (PlantUML) or html (interactive ELK.js)")
    args = parser.parse_args()

    os.makedirs(args.output_dir, exist_ok=True)
    hw_registry, scenario, hw_raw = _load_data(args.hw, args.sc)

    if args.format == "html":
        from src.view.html_view import generate_top_html, generate_level1_html, generate_level2_html
        generate_top_html(hw_registry, scenario, f"{args.output_dir}/scenario_top.html")
        generate_level1_html(hw_registry, scenario, f"{args.output_dir}/scenario_level1.html")
        generate_level2_html(hw_registry, scenario, hw_raw, f"{args.output_dir}/scenario_level2.html")
        print("\nAll 3 HTML diagrams generated!")
    else:
        generate_top_view(hw_registry, scenario, f"{args.output_dir}/scenario_top.puml")
        generate_level1(hw_registry, scenario, f"{args.output_dir}/scenario_level1.puml")
        generate_level2(hw_registry, scenario, hw_raw, f"{args.output_dir}/scenario_level2.puml")
        print("\nAll 3 PlantUML diagrams generated!")
