"""Generate multi-level PlantUML scenario flow diagrams grouped by hierarchy_group.

Generates 3 levels:
  - Top:    hierarchy_group blocks only (Sensor, ISP, CODEC, DPU, CPU, MEMORY)
  - Level1: IP-level detail within hierarchy_groups
  - Level2: Module-level detail within IPs

M2M paths are shown as individual memory (cylinder) shapes.
OTF paths are shown as thick arrows.
"""
import sys
import os
from src.model.hw_nodes import IPNode, SensorNode
from src.model.scenario import ConnectionType
from src.model.bw import comp_enabled


# ── Shared constants ────────────────────────────────────────────────


def _darken_hex(hex_color: str, factor: float = 0.15) -> str:
    """Darken a hex color by a given factor (0.0–1.0).

    factor=0.15 means 15% darker (multiply each channel by 0.85).
    """
    h = hex_color.lstrip('#')
    # Handle malformed hex gracefully
    if len(h) < 6:
        h = h.ljust(6, '0')
    r, g, b = int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16)
    mult = 1.0 - factor
    r, g, b = int(r * mult), int(g * mult), int(b * mult)
    return f"#{r:02X}{g:02X}{b:02X}"


HIERARCHY_COLORS = {
    "Sensor":  "#E3F2FD",
    "ISP":     "#E8F5E9",
    "CODEC":   "#FFF3E0",
    "VPS":     "#E5D754",
    "DPU":     "#F3E5F5",
    "NPU":     "#F35FBC",
    "GPU":     "#4A65ED",
    "CPU":     "#ECEFF1",
    "MEMORY":  "#FFF9C4",
    "Display": "#8246F0",
    "Other":   "#FAFAFA",
}
HIERARCHY_BORDER_COLORS = {k: _darken_hex(v) for k, v in HIERARCHY_COLORS.items()}

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
    "VPS":   "#E5D754",
    "NPU":   "#F35FBC",
}
IP_GROUP_BORDER_COLORS = {k: _darken_hex(v) for k, v in IP_GROUP_COLORS.items()}

HIERARCHY_ORDER = ["Sensor", "ISP", "VPS", "CODEC", "DPU", "CPU", "MEMORY", "Display", "Other"]

# Default fallback colors for unknown groups
_DEFAULT_HIERARCHY_COLOR = "#FAFAFA"
_DEFAULT_HIERARCHY_BORDER = _darken_hex(_DEFAULT_HIERARCHY_COLOR)
_DEFAULT_IP_GROUP_COLOR = "#E0E0E0"
_DEFAULT_IP_GROUP_BORDER = _darken_hex(_DEFAULT_IP_GROUP_COLOR)

# Track already-warned groups to avoid duplicate warnings
_warned_hierarchy = set()
_warned_ip_group = set()


def _get_effective_hierarchy_order(groups):
    """Return HIERARCHY_ORDER with any unknown groups appended at the end.

    This prevents ELK layout errors caused by tasks in unknown hierarchy
    groups being silently dropped (their nodes are never rendered, but
    edges still reference them).
    """
    unknown = [g for g in groups if g not in HIERARCHY_ORDER]
    for g in unknown:
        if g not in _warned_hierarchy:
            _warned_hierarchy.add(g)
            print(f"[WARNING] Unknown hierarchy_group '{g}' is not in HIERARCHY_ORDER. "
                  f"Using default color '{_DEFAULT_HIERARCHY_COLOR}'.")
    return list(HIERARCHY_ORDER) + unknown


def _get_hierarchy_color(grp):
    """Return hierarchy fill color, with warning for unknown groups."""
    color = HIERARCHY_COLORS.get(grp)
    if color is None:
        if grp not in _warned_hierarchy:
            _warned_hierarchy.add(grp)
            print(f"[WARNING] Unknown hierarchy_group '{grp}' is not in HIERARCHY_COLORS. "
                  f"Using default color '{_DEFAULT_HIERARCHY_COLOR}'.")
        return _DEFAULT_HIERARCHY_COLOR
    return color


def _get_hierarchy_border(grp):
    """Return hierarchy border color (15% darker than fill)."""
    return HIERARCHY_BORDER_COLORS.get(grp, _DEFAULT_HIERARCHY_BORDER)


def _get_ip_group_color(ipg):
    """Return IP group fill color, with warning for unknown groups."""
    color = IP_GROUP_COLORS.get(ipg)
    if color is None:
        if ipg not in _warned_ip_group:
            _warned_ip_group.add(ipg)
            print(f"[WARNING] Unknown ip_group '{ipg}' is not in IP_GROUP_COLORS. "
                  f"Using default color '{_DEFAULT_IP_GROUP_COLOR}'.")
        return _DEFAULT_IP_GROUP_COLOR
    return color


def _get_ip_group_border(ipg):
    """Return IP group border color (15% darker than fill)."""
    return IP_GROUP_BORDER_COLORS.get(ipg, _DEFAULT_IP_GROUP_BORDER)


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
    sys.path.insert(0, '.')
    from main import load_hw_config, create_hw_node, load_scenario_config, create_scenario, apply_scenario_settings
    hw_config = load_hw_config(hw_path)
    hw_list = hw_config if isinstance(hw_config, list) else hw_config.get('hardware', [])
    # Skip non-node entries (e.g. project-level '- llc: {...}' config)
    hw_list = [cfg for cfg in hw_list if 'name' in cfg]
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

# SW edge styling for PlantUML
_SW_LINE_COLOR = "#F9A825"   # amber/golden — contrasts with M2M red (#E65100) and OTF blue
_SW_DB_COLOR   = "#FFF8E1"   # light warm yellow background for SW cylinders


def _is_sw_edge(scenario, src_id, dst_id):
    """Return True if either endpoint of an edge is a SW (CPU) task."""
    src_task = scenario.get_task(src_id)
    dst_task = scenario.get_task(dst_id)
    return (src_task and src_task.is_sw_task) or (dst_task and dst_task.is_sw_task)

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
            is_sw = _is_sw_edge(scenario, src_id, dst_id)
            edge_label = "SW" if is_sw else "M2M"
            arrow = f'-[{_SW_LINE_COLOR},dashed]->' if is_sw else '-->'
            ip_settings = getattr(scenario, '_ip_settings', {})
            src_settings = ip_settings.get(src_id, {})
            dst_settings = ip_settings.get(dst_id, {})
            src_outputs = {o.get('port', ''): o for o in src_settings.get('outputs', [])}
            dst_inputs = {i.get('port', ''): i for i in dst_settings.get('inputs', [])}

            if port_pairs and port_pairs[0][0] != 'output':
                for sp, dp in port_pairs:
                    mem_id = f"mem_{m2m_idx}"
                    detail = _m2m_detail_label(sp, dp, src_outputs.get(sp), dst_inputs.get(dp))
                    if is_sw:
                        lines.append(f'database "{detail}" as {mem_id} {_SW_DB_COLOR}')
                    else:
                        lines.append(f'database "{detail}" as {mem_id}')
                    lines.append(f'{src_alias} {arrow} {mem_id}')
                    lines.append(f'{mem_id} {arrow} {dst_alias}')
                    m2m_idx += 1
            else:
                mem_id = f"mem_{m2m_idx}"
                if is_sw:
                    lines.append(f'database "{edge_label}" as {mem_id} {_SW_DB_COLOR}')
                else:
                    lines.append(f'database "{edge_label}" as {mem_id}')
                lines.append(f'{src_alias} {arrow} {mem_id}')
                lines.append(f'{mem_id} {arrow} {dst_alias}')
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
    if comp_enabled(comp):
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
            is_sw = _is_sw_edge(scenario, src_id, dst_id)
            edge_label = "SW" if is_sw else "M2M"
            arrow = f'-[{_SW_LINE_COLOR},dashed]->' if is_sw else '-->'
            if port_pairs and port_pairs[0][0] != 'output':
                for sp, dp in port_pairs:
                    mem_id = f"mem_{m2m_idx}"
                    if is_sw:
                        lines.append(f'database "{sp}\\n->{dp}" as {mem_id} {_SW_DB_COLOR}')
                    else:
                        lines.append(f'database "{sp}\\n->{dp}" as {mem_id}')
                    lines.append(f'{_safe_id(src_id)} {arrow} {mem_id}')
                    lines.append(f'{mem_id} {arrow} {_safe_id(dst_id)}')
                    m2m_idx += 1
            else:
                mem_id = f"mem_{m2m_idx}"
                if is_sw:
                    lines.append(f'database "{edge_label}" as {mem_id} {_SW_DB_COLOR}')
                else:
                    lines.append(f'database "{edge_label}" as {mem_id}')
                lines.append(f'{_safe_id(src_id)} {arrow} {mem_id}')
                lines.append(f'{mem_id} {arrow} {_safe_id(dst_id)}')
                m2m_idx += 1


# ═══════════════════════════════════════════════════════════════════
#  Top View: hierarchy_group blocks only
# ═══════════════════════════════════════════════════════════════════

def generate_top_view(hw_registry, scenario, output_path):
    groups, task_hw, task_hier, task_ipg = _build_groups(scenario, hw_registry)
    lines = ["@startuml", _skinparam(), "title Top View (Hierarchy Groups)\\n", ""]

    for grp in _get_effective_hierarchy_order(groups):
        if grp not in groups:
            continue
        bg = _get_hierarchy_color(grp)
        task_ids = groups[grp]
        ip_names = [task_hw[tid] for tid in task_ids]
        ip_list = ", ".join(ip_names)
        lines.append(f'package "{grp}" as pkg_{grp} {bg} {{')
        lines.append(f'    note as N_{grp}')
        lines.append(f'        {ip_list}')
        lines.append('    end note')
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

    for grp in _get_effective_hierarchy_order(groups):
        if grp not in groups:
            continue
        bg = _get_hierarchy_color(grp)
        bd = _get_hierarchy_border(grp)
        task_ids = groups[grp]
        lines.append(f'package "{grp}" as pkg_{grp} {bg}/{bd} {{')

        # Sub-group by ip_group
        ip_subgroups = {}
        for tid in task_ids:
            ipg = task_ipg[tid]
            ip_subgroups.setdefault(ipg, []).append(tid)

        for ipg, tids in ip_subgroups.items():
            ip_bg = _get_ip_group_color(ipg)
            ip_bd = _get_ip_group_border(ipg)
            if len(tids) > 1:
                lines.append(f'    package "{ipg}" as ipg_{_safe_id(ipg)} {ip_bg}/{ip_bd} {{')
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
                lines.append(f'    rectangle "{label}" as {_safe_id(tid)} {ip_bg}/{ip_bd}')

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
#  Level 2: I/O Module detail (CIN / COUT / DMA only)
# ═══════════════════════════════════════════════════════════════════

# Module types shown in Level 2 (I/O interfaces only)
_IO_MODULE_TYPES = {'CIN', 'COUT', 'DMA', 'DMA_READ', 'DMA_WRITE'}
_INPUT_TYPES_P = {'CIN', 'DMA_READ'}
_OUTPUT_TYPES_P = {'COUT', 'DMA_WRITE'}


def _classify_dma_dir(mod):
    """Classify a DMA module as input or output."""
    mt = mod.get('type', 'Generic')
    if mt in _INPUT_TYPES_P:
        return 'input'
    if mt in _OUTPUT_TYPES_P:
        return 'output'
    if mt == 'DMA':
        return 'input' if mod.get('direction', '').lower() == 'read' else 'output'
    return 'output'


def _get_used_ports(tid, ip_settings):
    """Get port names used in ip_settings for a task."""
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


def _get_port_comp_puml(tid, port_name, ip_settings):
    """Check if a port has SBWC/compression or LLC enabled."""
    settings = ip_settings.get(tid, {})
    for port_list in (settings.get('inputs', []), settings.get('outputs', [])):
        for p in port_list:
            if p.get('port', '') == port_name:
                comp = p.get('comp', 'disable')
                llc = p.get('llc', 'disable')
                return comp == 'enable', llc == 'enable'
    return False, False


def _l2_puml_color(mod, tid, ip_settings):
    """Determine module color for Level 2 PlantUML."""
    mt = mod.get('type', 'Generic')
    mn = mod.get('name', '')
    direction = _classify_dma_dir(mod)
    has_comp, has_llc = _get_port_comp_puml(tid, mn, ip_settings)

    if mt == 'CIN':
        return '#CEEAD6'
    if mt == 'COUT':
        return '#E8DAEF'
    if has_comp and has_llc:
        return '#F8BBD0'
    if has_comp:
        return '#FFE0B2'
    if has_llc:
        return '#E1BEE7'
    if direction == 'input':
        return '#D2E3FC'
    return '#FEEFC3'


def _emit_edges_level2(lines, scenario, task_hw, hw_raw):
    """Emit edges at Level 2, connecting to module aliases where possible."""
    ip_settings = getattr(scenario, '_ip_settings', {})

    # Build module alias map: (tid, mod_name) -> puml alias
    mod_alias_map = {}
    for tid, hw_name in task_hw.items():
        raw = hw_raw.get(hw_name, {})
        for mod in raw.get('modules', []):
            mn = mod.get('name', '')
            mod_alias_map[(tid, mn)] = f"{_safe_id(tid)}_{_safe_id(mn)}"

    m2m_idx = 0
    for src_id, dst_id, edge_data in scenario.graph.edges(data=True):
        conn_type = edge_data.get('conn_type', ConnectionType.M2M)
        port_pairs = edge_data.get('port_pairs', [])

        if conn_type == ConnectionType.OTF:
            if port_pairs and port_pairs[0][0] != 'output':
                for sp, dp in port_pairs:
                    src_alias = mod_alias_map.get((src_id, sp), _safe_id(src_id))
                    dst_alias = mod_alias_map.get((dst_id, dp), _safe_id(dst_id))
                    lines.append(f'{src_alias} ==> {dst_alias} : OTF\\n{sp}->{dp}')
            else:
                lines.append(f'{_safe_id(src_id)} ==> {_safe_id(dst_id)} : OTF')
        else:
            is_sw = _is_sw_edge(scenario, src_id, dst_id)
            edge_label = "SW" if is_sw else "M2M"
            arrow = f'-[{_SW_LINE_COLOR},dashed]->' if is_sw else '-->'
            src_s = ip_settings.get(src_id, {})
            dst_s = ip_settings.get(dst_id, {})
            src_out = {o.get('port', ''): o for o in src_s.get('outputs', [])}
            dst_in = {i.get('port', ''): i for i in dst_s.get('inputs', [])}

            if port_pairs and port_pairs[0][0] != 'output':
                for sp, dp in port_pairs:
                    src_alias = mod_alias_map.get((src_id, sp), _safe_id(src_id))
                    dst_alias = mod_alias_map.get((dst_id, dp), _safe_id(dst_id))
                    mem_id = f"mem_{m2m_idx}"
                    detail = _m2m_detail_label(sp, dp, src_out.get(sp), dst_in.get(dp))
                    if is_sw:
                        lines.append(f'database "{detail}" as {mem_id} {_SW_DB_COLOR}')
                    else:
                        lines.append(f'database "{detail}" as {mem_id}')
                    lines.append(f'{src_alias} {arrow} {mem_id}')
                    lines.append(f'{mem_id} {arrow} {dst_alias}')
                    m2m_idx += 1
            else:
                mem_id = f"mem_{m2m_idx}"
                if is_sw:
                    lines.append(f'database "{edge_label}" as {mem_id} {_SW_DB_COLOR}')
                else:
                    lines.append(f'database "{edge_label}" as {mem_id}')
                lines.append(f'{_safe_id(src_id)} {arrow} {mem_id}')
                lines.append(f'{mem_id} {arrow} {_safe_id(dst_id)}')
                m2m_idx += 1


def generate_level2(hw_registry, scenario, hw_raw, output_path):
    """Level 2: Show only used CIN/COUT/DMA modules per IP.

    - Only modules referenced in ip_settings are shown
    - Input modules (CIN, RDMA) listed first, output (COUT, WDMA) last
    - Edges connect to specific module aliases
    """
    groups, task_hw, task_hier, task_ipg = _build_groups(scenario, hw_registry)
    ip_settings = getattr(scenario, '_ip_settings', {})
    lines = ["@startuml", _skinparam()]
    lines.append("top to bottom direction")
    lines.append("skinparam rectangle {")
    lines.append("    FontSize 9")
    lines.append("}")
    lines.append("skinparam package {")
    lines.append("    padding 10")
    lines.append("}")
    lines.append("title Level 2 View (I/O Modules)\\n")
    lines.append("")

    grp_tids = {}

    for grp in _get_effective_hierarchy_order(groups):
        if grp not in groups:
            continue
        bg = _get_hierarchy_color(grp)
        bd = _get_hierarchy_border(grp)
        task_ids = groups[grp]
        lines.append(f'package "{grp}" as pkg_{grp} {bg}/{bd} {{')

        ip_subgroups = {}
        for tid in task_ids:
            ipg = task_ipg[tid]
            ip_subgroups.setdefault(ipg, []).append(tid)

        ordered_tids = []
        for ipg, tids in ip_subgroups.items():
            ip_bg = _get_ip_group_color(ipg)

            for tid in tids:
                hw_name = task_hw[tid]
                hw = hw_registry.get(hw_name)
                raw = hw_raw.get(hw_name, {})
                all_modules = raw.get('modules', [])
                # Filter to I/O modules only
                io_modules = [m for m in all_modules
                              if m.get('type', 'Generic') in _IO_MODULE_TYPES]
                # Further filter to used modules
                used_ports = _get_used_ports(tid, ip_settings)
                if used_ports:
                    io_modules = [m for m in io_modules
                                  if m.get('name', '') in used_ports]
                ordered_tids.append(tid)

                if io_modules:
                    # Separate input/output modules
                    input_mods = [m for m in io_modules
                                  if _classify_dma_dir(m) == 'input']
                    output_mods = [m for m in io_modules
                                   if _classify_dma_dir(m) == 'output']

                    ip_bd = _get_ip_group_border(ipg)
                    lines.append(f'    package "{hw_name} (BLK_{ipg})" as {_safe_id(tid)} {ip_bg}/{ip_bd} {{')

                    # Input modules first
                    for mod in input_mods:
                        mod_name = mod.get('name', '?')
                        mod_type = mod.get('type', 'Generic')
                        mod_id = f"{_safe_id(tid)}_{_safe_id(mod_name)}"
                        mod_clr = _l2_puml_color(mod, tid, ip_settings)
                        short_type = _mod_type_short(mod_type)
                        lines.append(f'        rectangle "{mod_name}\\n<size:8>{short_type}</size>" as {mod_id} {mod_clr}')

                    # Output modules last
                    for mod in output_mods:
                        mod_name = mod.get('name', '?')
                        mod_type = mod.get('type', 'Generic')
                        mod_id = f"{_safe_id(tid)}_{_safe_id(mod_name)}"
                        mod_clr = _l2_puml_color(mod, tid, ip_settings)
                        short_type = _mod_type_short(mod_type)
                        lines.append(f'        rectangle "{mod_name}\\n<size:8>{short_type}</size>" as {mod_id} {mod_clr}')

                    # Intra-IP edges between I/O modules
                    io_names = {m.get('name') for m in io_modules}
                    edges = raw.get('edges', [])
                    has_internal = False
                    for edge in edges:
                        s_name = edge.get('src', '')
                        d_name = edge.get('dst', '')
                        if s_name in io_names and d_name in io_names:
                            e_src = f"{_safe_id(tid)}_{_safe_id(s_name)}"
                            e_dst = f"{_safe_id(tid)}_{_safe_id(d_name)}"
                            lines.append(f'        {e_src} --> {e_dst}')
                            has_internal = True

                    # If no internal edges, create implicit input→output
                    if not has_internal and input_mods and output_mods:
                        for im in input_mods:
                            for om in output_mods:
                                e_src = f"{_safe_id(tid)}_{_safe_id(im.get('name', ''))}"
                                e_dst = f"{_safe_id(tid)}_{_safe_id(om.get('name', ''))}"
                                lines.append(f'        {e_src} --> {e_dst}')

                    lines.append("    }")
                else:
                    label = _ip_label(hw_name, hw, scenario, tid)
                    ip_bd = _get_ip_group_border(ipg)
                    lines.append(f'    rectangle "{label}" as {_safe_id(tid)} {ip_bg}/{ip_bd}')

        grp_tids[grp] = ordered_tids
        lines.append("}")
        lines.append("")

    # Hidden links between hierarchy groups
    lines.append("' === Hidden links for vertical ordering ===")
    active_grps = [g for g in HIERARCHY_ORDER if g in grp_tids]
    for i in range(len(active_grps) - 1):
        lines.append(f'pkg_{active_grps[i]} -[hidden]down-> pkg_{active_grps[i+1]}')

    lines.append("")
    lines.append("' === Connections (Task Topology) ===")
    _emit_edges_level2(lines, scenario, task_hw, hw_raw)
    lines.append("")
    lines.append("@enduml")

    with open(output_path, 'w', encoding='utf-8') as f:
        f.write("\n".join(lines))
    print(f"Level 2 -> {output_path}")


# ═══════════════════════════════════════════════════════════════════
#  Level 3: Full module detail (all modules + intra-IP edges)
# ═══════════════════════════════════════════════════════════════════

def _emit_edges_level3(lines, scenario, task_hw):
    """Emit edges at Level 3 with M2M detail (size/format/bitwidth/comp)."""
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
            is_sw = _is_sw_edge(scenario, src_id, dst_id)
            edge_label = "SW" if is_sw else "M2M"
            arrow = f'-[{_SW_LINE_COLOR},dashed]->' if is_sw else '-->'
            ip_settings = getattr(scenario, '_ip_settings', {})
            src_settings = ip_settings.get(src_id, {})
            dst_settings = ip_settings.get(dst_id, {})
            src_outputs = {o.get('port', ''): o for o in src_settings.get('outputs', [])}
            dst_inputs = {i.get('port', ''): i for i in dst_settings.get('inputs', [])}

            if port_pairs and port_pairs[0][0] != 'output':
                for sp, dp in port_pairs:
                    mem_id = f"mem_{m2m_idx}"
                    detail = _m2m_detail_label(sp, dp, src_outputs.get(sp), dst_inputs.get(dp))
                    if is_sw:
                        lines.append(f'database "{detail}" as {mem_id} {_SW_DB_COLOR}')
                    else:
                        lines.append(f'database "{detail}" as {mem_id}')
                    lines.append(f'{_safe_id(src_id)} {arrow} {mem_id}')
                    lines.append(f'{mem_id} {arrow} {_safe_id(dst_id)}')
                    m2m_idx += 1
            else:
                mem_id = f"mem_{m2m_idx}"
                if is_sw:
                    lines.append(f'database "{edge_label}" as {mem_id} {_SW_DB_COLOR}')
                else:
                    lines.append(f'database "{edge_label}" as {mem_id}')
                lines.append(f'{_safe_id(src_id)} {arrow} {mem_id}')
                lines.append(f'{mem_id} {arrow} {_safe_id(dst_id)}')
                m2m_idx += 1


def generate_level3(hw_registry, scenario, hw_raw, output_path):
    """Level 3: Show all modules and intra-IP edges."""
    groups, task_hw, task_hier, task_ipg = _build_groups(scenario, hw_registry)
    lines = ["@startuml", _skinparam()]
    lines.append("top to bottom direction")
    lines.append("skinparam rectangle {")
    lines.append("    FontSize 9")
    lines.append("}")
    lines.append("skinparam package {")
    lines.append("    padding 10")
    lines.append("}")
    lines.append("title Level 3 View (Full Module Detail)\\n")
    lines.append("")

    grp_tids = {}

    for grp in _get_effective_hierarchy_order(groups):
        if grp not in groups:
            continue
        bg = _get_hierarchy_color(grp)
        bd = _get_hierarchy_border(grp)
        task_ids = groups[grp]
        lines.append(f'package "{grp}" as pkg_{grp} {bg}/{bd} {{')

        ip_subgroups = {}
        for tid in task_ids:
            ipg = task_ipg[tid]
            ip_subgroups.setdefault(ipg, []).append(tid)

        ordered_tids = []
        for ipg, tids in ip_subgroups.items():
            ip_bg = _get_ip_group_color(ipg)

            ip_bd = _get_ip_group_border(ipg)

            for tid in tids:
                hw_name = task_hw[tid]
                hw = hw_registry.get(hw_name)
                raw = hw_raw.get(hw_name, {})
                modules = raw.get('modules', [])
                ordered_tids.append(tid)

                if modules:
                    lines.append(f'    package "{hw_name}" as {_safe_id(tid)} {ip_bg}/{ip_bd} {{')
                    for mod in modules:
                        mod_name = mod.get('name', '?')
                        mod_type = mod.get('type', 'Generic')
                        mod_id = f"{_safe_id(tid)}_{_safe_id(mod_name)}"
                        mod_color = _mod_color(mod_type)
                        short_type = _mod_type_short(mod_type)
                        lines.append(f'        rectangle "{mod_name}\\n<size:8>{short_type}</size>" as {mod_id} {mod_color}')
                    edges = raw.get('edges', [])
                    for edge in edges:
                        e_src = f"{_safe_id(tid)}_{_safe_id(edge.get('src', ''))}"
                        e_dst = f"{_safe_id(tid)}_{_safe_id(edge.get('dst', ''))}"
                        lines.append(f'        {e_src} --> {e_dst}')
                    lines.append("    }")
                else:
                    label = _ip_label(hw_name, hw, scenario, tid)
                    lines.append(f'    rectangle "{label}" as {_safe_id(tid)} {ip_bg}/{ip_bd}')

        grp_tids[grp] = ordered_tids
        lines.append("}")
        lines.append("")

    lines.append("' === Hidden links for vertical ordering ===")
    active_grps = [g for g in HIERARCHY_ORDER if g in grp_tids]
    for i in range(len(active_grps) - 1):
        lines.append(f'pkg_{active_grps[i]} -[hidden]down-> pkg_{active_grps[i+1]}')

    lines.append("")
    lines.append("' === Connections (Task Topology) ===")
    _emit_edges_level3(lines, scenario, task_hw)
    lines.append("")
    lines.append("@enduml")

    with open(output_path, 'w', encoding='utf-8') as f:
        f.write("\n".join(lines))
    print(f"Level 3 -> {output_path}")


# ═══════════════════════════════════════════════════════════════════
#  Task Topology: flat task DAG (no hierarchy grouping)
# ═══════════════════════════════════════════════════════════════════

def generate_task_topology(hw_registry, scenario, output_path):
    """Generate a flat task topology diagram showing task DAG with OTF/M2M edges."""
    lines = ["@startuml", _skinparam()]
    lines.append("top to bottom direction")
    lines.append("title Task Topology\\n")
    lines.append("")

    # Emit task nodes in topological order
    for task in scenario.get_tasks():
        tid = task.task_id
        hw_name = task.mapped_hw
        hw = hw_registry.get(hw_name)
        hier = _get_hierarchy(hw, hw_name) if hw else "Other"
        bg = _get_hierarchy_color(hier)
        bd = _get_hierarchy_border(hier)
        # Label: task_id (HW_name)
        label = f"<b>{tid}</b>\\n({hw_name})"
        lines.append(f'rectangle "{label}" as {_safe_id(tid)} {bg}/{bd}')

    lines.append("")
    lines.append("' === Connections ===")

    # Emit edges — simple OTF/M2M/SW arrows (no memory cylinders for clarity)
    for src_id, dst_id, edge_data in scenario.graph.edges(data=True):
        conn_type = edge_data.get('conn_type', ConnectionType.M2M)
        if conn_type == ConnectionType.OTF:
            lines.append(f'{_safe_id(src_id)} ==> {_safe_id(dst_id)} : OTF')
        elif _is_sw_edge(scenario, src_id, dst_id):
            lines.append(f'{_safe_id(src_id)} -[{_SW_LINE_COLOR},dashed]-> {_safe_id(dst_id)} : SW')
        else:
            lines.append(f'{_safe_id(src_id)} --> {_safe_id(dst_id)} : M2M')

    lines.append("")
    lines.append("@enduml")

    with open(output_path, 'w', encoding='utf-8') as f:
        f.write("\n".join(lines))
    print(f"Task Topology -> {output_path}")


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
        from src.view.html_view import generate_top_html, generate_level1_html, generate_level2_html, generate_level3_html
        generate_top_html(hw_registry, scenario, f"{args.output_dir}/scenario_top.html")
        generate_level1_html(hw_registry, scenario, f"{args.output_dir}/scenario_level1.html")
        generate_level2_html(hw_registry, scenario, hw_raw, f"{args.output_dir}/scenario_level2.html")
        generate_level3_html(hw_registry, scenario, hw_raw, f"{args.output_dir}/scenario_level3.html")
        print("\nAll 4 HTML diagrams generated!")
    else:
        generate_top_view(hw_registry, scenario, f"{args.output_dir}/scenario_top.puml")
        generate_level1(hw_registry, scenario, f"{args.output_dir}/scenario_level1.puml")
        generate_level2(hw_registry, scenario, hw_raw, f"{args.output_dir}/scenario_level2.puml")
        generate_level3(hw_registry, scenario, hw_raw, f"{args.output_dir}/scenario_level3.puml")
        print("\nAll 4 PlantUML diagrams generated!")
