"""
CSV → Scenario YAML Generator.

Reads 2~3 CSV files and generates compact or normal scenario YAML:
  1) *_meta.csv     — scenario metadata (name, sensor, config paths, parameters)
  2) *_ports.csv    — IP port definitions with integrated edge info
  3) *_sw_tasks.csv — (optional) SW task definitions

Usage:
    from src.model.csv_to_scenario import generate_from_csvs
    generate_from_csvs('prefix', output='scenario.yaml', compact=True)

CSV comment lines starting with '#' are skipped.
"""

from __future__ import annotations

import csv
import logging
import os
from collections import OrderedDict
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import yaml

logger = logging.getLogger(__name__)


# ── YAML Ordered Dumper ─────────────────────────────────────────
# Ensures dict keys are written in insertion order (not sorted).

class _OrderedDumper(yaml.Dumper):
    pass


def _dict_representer(dumper, data):
    return dumper.represent_mapping(
        yaml.resolver.BaseResolver.DEFAULT_MAPPING_TAG, data.items()
    )


_OrderedDumper.add_representer(OrderedDict, _dict_representer)
_OrderedDumper.add_representer(dict, _dict_representer)


# Also handle plain lists to use flow style for size arrays [0,0,w,h]
def _list_representer(dumper, data):
    # Use flow style for short numeric lists (like size arrays)
    if len(data) <= 6 and all(isinstance(x, (int, float)) for x in data):
        return dumper.represent_sequence(
            yaml.resolver.BaseResolver.DEFAULT_SEQUENCE_TAG,
            data, flow_style=True
        )
    return dumper.represent_sequence(
        yaml.resolver.BaseResolver.DEFAULT_SEQUENCE_TAG, data
    )


_OrderedDumper.add_representer(list, _list_representer)


# ── CSV Loading ─────────────────────────────────────────────────

def _read_csv(path: str) -> List[Dict[str, str]]:
    """Read CSV file, skipping '#' comment lines. Returns list of row dicts."""
    rows = []
    with open(path, 'r', encoding='utf-8-sig') as f:
        # Filter out comment lines before passing to csv.DictReader
        lines = [line for line in f if not line.strip().startswith('#')]
    
    if not lines:
        return []
    
    # Re-parse filtered lines
    import io
    reader = csv.DictReader(io.StringIO(''.join(lines)))
    for row in reader:
        # Strip whitespace from keys and values
        cleaned = {k.strip(): v.strip() if v else '' for k, v in row.items() if k}
        rows.append(cleaned)
    return rows


def _val(row: dict, key: str, default: Any = '') -> str:
    """Get value from row, returning default if empty."""
    v = row.get(key, '')
    return v if v else default


def _int_or_none(val: str) -> Optional[int]:
    """Parse int or return None if empty."""
    if not val:
        return None
    try:
        return int(float(val))
    except (ValueError, TypeError):
        return None


def _float_or_none(val: str) -> Optional[float]:
    """Parse float or return None if empty."""
    if not val:
        return None
    try:
        return float(val)
    except (ValueError, TypeError):
        return None


# ── Meta CSV ────────────────────────────────────────────────────

# Default values for meta fields
_META_DEFAULTS = {
    'num_frames': 1,
    'asv_group': 4,
    'sw_margin': 0.25,
    'h_blank_margin': 0.05,
    'bw_power': 80,
    'vBat': 4.0,
    'pmic_efficiency': 0.85,
    'bw_margin': 1.25,
    'mem_util': 0.55,
    'mif_channel_width': 16,
}

_META_INT_FIELDS = {'num_frames', 'asv_group', 'mif_channel_width'}
_META_FLOAT_FIELDS = {'sw_margin', 'h_blank_margin', 'bw_power', 'vBat',
                       'pmic_efficiency', 'bw_margin', 'mem_util'}


def load_meta_csv(path: str) -> dict:
    """Load scenario meta CSV. Returns scenario-level dict."""
    rows = _read_csv(path)
    if not rows:
        raise ValueError(f"Meta CSV '{path}' is empty or has no data rows.")
    
    row = rows[0]  # Only first data row
    
    meta = {}
    meta['name'] = _val(row, 'name', 'Unnamed')
    
    writer = _val(row, 'writer')
    if writer:
        meta['writer'] = writer
    
    # Numeric params with defaults
    for field, default in _META_DEFAULTS.items():
        raw = _val(row, field)
        if raw:
            if field in _META_INT_FIELDS:
                meta[field] = int(float(raw))
            else:
                meta[field] = float(raw)
        else:
            meta[field] = default
    
    # Config paths
    config_paths = {}
    for key in ('hw_config', 'sensor_config', 'hw_info', 'hw_dvfs'):
        v = _val(row, key)
        if v:
            config_paths[key] = v
    if config_paths:
        meta['config_paths'] = config_paths
    
    # Sensor
    sensor_hw = _val(row, 'sensor_hw')
    sensor_mode = _val(row, 'sensor_mode')
    if sensor_hw:
        meta['sensor'] = {'hw': sensor_hw}
        if sensor_mode:
            meta['sensor']['mode'] = sensor_mode
    
    # Sensor task
    sensor_task_id = _val(row, 'sensor_task_id', 't_sensor')
    sensor_task_desc = _val(row, 'sensor_task_desc', '')
    meta['_sensor_task'] = {
        'id': sensor_task_id,
        'hw': sensor_hw,
    }
    if sensor_task_desc:
        meta['_sensor_task']['description'] = sensor_task_desc
    
    return meta


# ── Ports CSV ───────────────────────────────────────────────────

def load_ports_csv(path: str) -> List[dict]:
    """Load ports+edges CSV. Returns list of row dicts with parsed values."""
    rows = _read_csv(path)
    parsed = []
    for row in rows:
        p = {
            'order': int(_val(row, 'order', '0')),
            'hw': _val(row, 'hw'),
            'port': _val(row, 'port'),
            'dir': _val(row, 'dir'),
            'width': _int_or_none(_val(row, 'width')),
            'height': _int_or_none(_val(row, 'height')),
            'format': _val(row, 'format') or None,
            'bitwidth': _int_or_none(_val(row, 'bitwidth')),
            'comp': _val(row, 'comp') or None,
            'comp_ratio': _float_or_none(_val(row, 'comp_ratio')),
            'src_task': _val(row, 'src_task') or None,
            'src_port': _val(row, 'src_port') or None,
            'edge_type': _val(row, 'edge_type') or None,
            'task_id': _val(row, 'task_id') or None,
            'task_desc': _val(row, 'task_desc') or None,
        }
        parsed.append(p)
    return parsed


# ── SW Tasks CSV ────────────────────────────────────────────────

def load_sw_tasks_csv(path: str) -> List[dict]:
    """Load SW tasks CSV. Returns list of sw task dicts."""
    rows = _read_csv(path)
    tasks = []
    for row in rows:
        after_raw = _val(row, 'after_task', '')
        # Parse after_task — supports "task_id:port" notation
        if ':' in after_raw:
            after_task, after_port = after_raw.split(':', 1)
        else:
            after_task = after_raw
            after_port = None
        
        t = {
            'id': _val(row, 'id'),
            'name': _val(row, 'name'),
            'group': _val(row, 'group') or None,
            'processor': _val(row, 'processor', 'CPU'),
            'duration_ms': float(_val(row, 'duration_ms', '0')),
            'latency_ms': float(_val(row, 'latency_ms', '0')),
            'after_task': after_task,
            'after_port': after_port,
            'edge_type': _val(row, 'edge_type', 'M2M'),
        }
        tasks.append(t)
    return tasks


# ── Build Scenario Dict ────────────────────────────────────────

def _group_ports_by_order(ports: List[dict]) -> List[Tuple[int, str, List[dict]]]:
    """Group port rows by order number. Returns [(order, hw, [rows])]."""
    groups = OrderedDict()
    for p in ports:
        key = p['order']
        if key not in groups:
            groups[key] = (p['hw'], [])
        groups[key][1].append(p)
    return [(order, hw, rows) for order, (hw, rows) in groups.items()]


def _build_port_dict(p: dict, include_size: bool = True) -> dict:
    """Build a port dict for YAML output."""
    port = {'port': p['port']}
    
    if include_size and p['width'] is not None and p['height'] is not None:
        port['size'] = [0, 0, p['width'], p['height']]
    
    if p['format']:
        port['format'] = p['format']
    if p['bitwidth'] is not None:
        port['bitwidth'] = p['bitwidth']
    if p['comp']:
        port['comp'] = p['comp']
    if p['comp_ratio'] is not None:
        port['comp_ratio'] = p['comp_ratio']
    
    return port


def _get_primary_output_size(outputs: List[dict]) -> Optional[Tuple[int, int]]:
    """Get primary output size (prefers COUTFIFO, then largest by pixels)."""
    # First: look for COUTFIFO
    for o in outputs:
        if 'COUTFIFO' in (o.get('port') or '').upper():
            if o['width'] is not None:
                return (o['width'], o['height'])
    # Second: largest by pixels
    best, best_px = None, -1
    for o in outputs:
        if o['width'] is not None and o['height'] is not None:
            px = o['width'] * o['height']
            if px > best_px:
                best_px = px
                best = (o['width'], o['height'])
    return best


def _get_primary_input_size(inputs: List[dict]) -> Optional[Tuple[int, int]]:
    """Get primary (largest) input size."""
    best, best_px = None, -1
    for i in inputs:
        if i['width'] is not None and i['height'] is not None:
            px = i['width'] * i['height']
            if px > best_px:
                best_px = px
                best = (i['width'], i['height'])
    return best


def build_scenario_dict(meta: dict, ports: List[dict],
                        sw_tasks: Optional[List[dict]] = None,
                        compact: bool = True) -> dict:
    """
    Build complete scenario YAML dict from parsed CSV data.
    
    Args:
        meta: Parsed meta CSV dict.
        ports: Parsed ports CSV list.
        sw_tasks: Optional parsed SW tasks list.
        compact: If True, generate compact format (omit inheritable fields).
    
    Returns:
        Scenario dict ready for YAML dump.
    """
    scenario = OrderedDict()
    
    # ── Header ──────────────────────────────────────────────────
    if compact:
        scenario['compact'] = True
    
    scenario['name'] = meta['name']
    if 'writer' in meta:
        scenario['writer'] = meta['writer']
    
    # Numeric params
    for field in ('num_frames', 'asv_group', 'sw_margin', 'h_blank_margin',
                  'bw_power', 'vBat', 'pmic_efficiency', 'bw_margin',
                  'mem_util', 'mif_channel_width'):
        if field in meta:
            scenario[field] = meta[field]
    
    # Config paths
    if 'config_paths' in meta:
        scenario['config_paths'] = meta['config_paths']
    
    # Sensor
    if 'sensor' in meta:
        scenario['sensor'] = meta['sensor']
    
    # Sensor task
    sensor_task = meta.get('_sensor_task', {})
    if sensor_task:
        task_entry = OrderedDict()
        task_entry['id'] = sensor_task['id']
        task_entry['hw'] = sensor_task.get('hw', '')
        if sensor_task.get('description'):
            task_entry['description'] = sensor_task['description']
        scenario['tasks'] = [task_entry]
    
    # ── IP Blocks ───────────────────────────────────────────────
    ip_blocks = []
    groups = _group_ports_by_order(ports)
    
    # Track flowing size for compact mode
    prev_primary_output_size = None
    
    # Build index of which order group each task belongs to
    # (for sw_task insertion)
    task_to_order = {}
    for order, hw, rows in groups:
        # Determine task_id for this group
        first_row = rows[0]
        task_id = first_row['task_id'] or f"t_{hw.lower()}"
        task_to_order[task_id] = order
    
    # Pre-process SW tasks: build lookup by after_task
    sw_task_lookup = {}  # after_task_id -> [sw_task_dicts]
    if sw_tasks:
        for st in sw_tasks:
            key = st['after_task']
            if key not in sw_task_lookup:
                sw_task_lookup[key] = []
            sw_task_lookup[key].append(st)
            # Also register sw_task IDs for task_to_order
            task_to_order[st['id']] = -1  # SW tasks don't have order
    
    def _resolve_all_sizes(group_rows, prev_size):
        """Resolve all sizes for a group (for normal mode output)."""
        inputs = [r for r in group_rows if r['dir'] == 'in']
        outputs = [r for r in group_rows if r['dir'] == 'out']
        
        # Resolve input sizes
        for inp in inputs:
            if inp['width'] is None and prev_size is not None:
                inp['width'] = prev_size[0]
                inp['height'] = prev_size[1]
        
        # Get primary input size
        primary_in = _get_primary_input_size(inputs)
        
        # Resolve output sizes
        for outp in outputs:
            if outp['width'] is None and primary_in is not None:
                outp['width'] = primary_in[0]
                outp['height'] = primary_in[1]
        
        return inputs, outputs
    
    for order, hw, rows in groups:
        block = OrderedDict()
        
        inputs = [r for r in rows if r['dir'] == 'in']
        outputs = [r for r in rows if r['dir'] == 'out']
        
        # ── ip_settings ─────────────────────────────────────────
        ip_settings = OrderedDict()
        ip_settings['hw'] = hw
        
        if not compact:
            ip_settings['mode'] = 'Normal'
        
        # Resolve sizes for normal mode
        if not compact:
            _resolve_all_sizes(rows, prev_primary_output_size)
        
        # Build inputs list
        ip_inputs = []
        for inp in inputs:
            if compact:
                # In compact mode: omit size if it matches prev output
                can_inherit = (
                    prev_primary_output_size is not None and
                    inp['width'] is not None and
                    inp['width'] == prev_primary_output_size[0] and
                    inp['height'] == prev_primary_output_size[1]
                )
                ip_inputs.append(_build_port_dict(inp, include_size=not can_inherit))
            else:
                ip_inputs.append(_build_port_dict(inp, include_size=True))
        
        if ip_inputs:
            ip_settings['inputs'] = ip_inputs
        
        # Get primary input size (resolved)
        if not compact:
            primary_input = _get_primary_input_size(inputs)
        else:
            # For compact, resolve inputs first to know primary input
            resolved_inputs = []
            for inp in inputs:
                if inp['width'] is None and prev_primary_output_size is not None:
                    resolved_inputs.append({
                        **inp,
                        'width': prev_primary_output_size[0],
                        'height': prev_primary_output_size[1],
                    })
                else:
                    resolved_inputs.append(inp)
            primary_input = _get_primary_input_size(resolved_inputs)
        
        # Build outputs list
        ip_outputs = []
        for outp in outputs:
            if compact:
                # Omit size if it matches primary input
                can_default = (
                    primary_input is not None and
                    outp['width'] is not None and
                    outp['width'] == primary_input[0] and
                    outp['height'] == primary_input[1]
                )
                ip_outputs.append(_build_port_dict(outp, include_size=not can_default))
            else:
                ip_outputs.append(_build_port_dict(outp, include_size=True))
        
        if ip_outputs:
            ip_settings['outputs'] = ip_outputs
        
        block['ip_settings'] = ip_settings
        
        # ── tasks ───────────────────────────────────────────────
        first_row = rows[0]
        task_id = first_row['task_id'] or f"t_{hw.lower()}"
        task_desc = first_row['task_desc'] or hw
        
        auto_id = f"t_{hw.lower()}"
        is_auto_task = (task_id == auto_id and task_desc == hw)
        
        if not compact or not is_auto_task:
            task_entry = OrderedDict()
            task_entry['id'] = task_id
            task_entry['hw'] = hw
            if task_desc and task_desc != hw:
                task_entry['description'] = task_desc
            elif not compact:
                task_entry['description'] = hw
            block['tasks'] = [task_entry]
        
        # ── edges ───────────────────────────────────────────────
        edges = []
        for inp in inputs:
            if inp['src_task']:
                edge = OrderedDict()
                edge['src'] = inp['src_task']
                
                edge_type = inp['edge_type'] or 'M2M'
                
                if inp['src_port']:
                    edge['src_port'] = inp['src_port']
                
                edge['dst'] = task_id
                
                # Include dst_port for M2M always, for OTF only if src_port specified
                if edge_type == 'M2M':
                    edge['dst_port'] = inp['port']
                elif inp['src_port']:
                    edge['dst_port'] = inp['port']
                
                edge['type'] = edge_type
                edges.append(edge)
        
        if edges:
            block['edges'] = edges
        
        ip_blocks.append(block)
        
        # ── Update flowing size ─────────────────────────────────
        # Resolve output sizes for tracking
        resolved_outputs = []
        for outp in outputs:
            if outp['width'] is not None:
                resolved_outputs.append(outp)
            elif primary_input:
                resolved_outputs.append({
                    **outp, 'width': primary_input[0], 'height': primary_input[1]
                })
        
        new_primary = _get_primary_output_size(resolved_outputs)
        if new_primary:
            prev_primary_output_size = new_primary
        elif primary_input:
            prev_primary_output_size = primary_input
        
        # ── Insert SW tasks after this block ────────────────────
        _insert_sw_task_blocks(ip_blocks, task_id, sw_task_lookup, compact)
    
    # Also insert SW tasks that depend on other SW tasks
    # (handled recursively in _insert_sw_task_blocks)
    
    scenario['ip_blocks'] = ip_blocks
    
    return scenario


def _insert_sw_task_blocks(ip_blocks: list, after_task_id: str,
                           sw_task_lookup: dict, compact: bool):
    """Insert SW task blocks after the block containing after_task_id."""
    pending = sw_task_lookup.pop(after_task_id, [])
    
    for st in pending:
        block = OrderedDict()
        
        sw_entry = OrderedDict()
        sw_entry['id'] = st['id']
        sw_entry['name'] = st['name']
        if st['group']:
            sw_entry['group'] = st['group']
        sw_entry['processor'] = st['processor']
        sw_entry['duration_ms'] = st['duration_ms']
        if st['latency_ms'] > 0:
            sw_entry['latency_ms'] = st['latency_ms']
        
        block['sw_tasks'] = [sw_entry]
        
        # Edge from after_task to this SW task
        edge = OrderedDict()
        edge['src'] = st['after_task']
        if st['after_port']:
            edge['src_port'] = st['after_port']
        edge['dst'] = st['id']
        edge['type'] = st['edge_type']
        
        block['edges'] = [edge]
        
        ip_blocks.append(block)
        
        # Recursively insert SW tasks that depend on this one
        _insert_sw_task_blocks(ip_blocks, st['id'], sw_task_lookup, compact)


# ── YAML Generation ─────────────────────────────────────────────

def generate_yaml(scenario_dict: dict, output_path: str) -> str:
    """
    Write scenario dict to YAML file.
    
    Args:
        scenario_dict: Complete scenario dict.
        output_path: Output YAML file path.
    
    Returns:
        The output file path.
    """
    # Add header comment
    is_compact = scenario_dict.get('compact', False)
    name = scenario_dict.get('name', 'Generated')
    
    header_lines = [
        f"# {name} Scenario ({'Compact' if is_compact else 'Normal'} Version)",
        "# Auto-generated from CSV by csv_to_scenario",
        "",
    ]
    header = '\n'.join(header_lines)
    
    yaml_content = yaml.dump(
        scenario_dict,
        Dumper=_OrderedDumper,
        default_flow_style=False,
        allow_unicode=True,
        sort_keys=False,
        width=120,
    )
    
    os.makedirs(os.path.dirname(output_path) or '.', exist_ok=True)
    with open(output_path, 'w', encoding='utf-8') as f:
        f.write(header)
        f.write(yaml_content)
    
    logger.info(f"Generated scenario YAML: {output_path}")
    return output_path


# ── Auto-Discovery ──────────────────────────────────────────────

def discover_csv_files(prefix_or_path: str) -> Tuple[str, str, Optional[str]]:
    """
    Auto-discover CSV files by prefix.
    
    If prefix_or_path is a file ending with '_meta.csv', extract prefix.
    Otherwise, treat as prefix and look for {prefix}_meta.csv, etc.
    
    Args:
        prefix_or_path: File path or prefix string.
    
    Returns:
        (meta_csv, ports_csv, sw_tasks_csv or None)
    
    Raises:
        FileNotFoundError: If required CSV files are not found.
    """
    path = Path(prefix_or_path)
    
    if path.suffix == '.csv' and path.name.endswith('_meta.csv'):
        # Extract prefix from meta file path
        prefix = str(path)[:-len('_meta.csv')]
    elif path.suffix == '.csv':
        # Try removing suffix to get prefix
        prefix = str(path.with_suffix(''))
        if prefix.endswith('_ports') or prefix.endswith('_sw_tasks'):
            prefix = prefix.rsplit('_', 1)[0]
            if prefix.endswith('_sw'):
                prefix = prefix[:-3]
    else:
        prefix = str(path)
    
    meta_csv = f"{prefix}_meta.csv"
    ports_csv = f"{prefix}_ports.csv"
    sw_tasks_csv = f"{prefix}_sw_tasks.csv"
    
    if not os.path.exists(meta_csv):
        raise FileNotFoundError(f"Meta CSV not found: {meta_csv}")
    if not os.path.exists(ports_csv):
        raise FileNotFoundError(f"Ports CSV not found: {ports_csv}")
    
    sw_path = sw_tasks_csv if os.path.exists(sw_tasks_csv) else None
    
    return meta_csv, ports_csv, sw_path


# ── Public API ──────────────────────────────────────────────────

def generate_from_csvs(prefix_or_meta: str,
                       ports_csv: Optional[str] = None,
                       sw_tasks_csv: Optional[str] = None,
                       output: Optional[str] = None,
                       compact: bool = True) -> str:
    """
    Generate scenario YAML from CSV files.
    
    Can auto-discover files from prefix, or accept explicit paths.
    
    Args:
        prefix_or_meta: CSV prefix (e.g., 'FHD30_recording') or path to meta CSV.
        ports_csv: Explicit ports CSV path (auto-discovered if None).
        sw_tasks_csv: Explicit SW tasks CSV path (auto-discovered if None).
        output: Output YAML path. If None, derived from prefix.
        compact: If True, generate compact format.
    
    Returns:
        Path to generated YAML file.
    """
    # Discover or use explicit paths
    if ports_csv is None:
        meta_path, ports_path, sw_path = discover_csv_files(prefix_or_meta)
    else:
        meta_path = prefix_or_meta
        ports_path = ports_csv
        sw_path = sw_tasks_csv
    
    # Load CSVs
    print(f"Loading meta:     {meta_path}")
    meta = load_meta_csv(meta_path)
    
    print(f"Loading ports:    {ports_path}")
    port_rows = load_ports_csv(ports_path)
    
    sw_task_rows = None
    if sw_path:
        print(f"Loading sw_tasks: {sw_path}")
        sw_task_rows = load_sw_tasks_csv(sw_path)
    
    # Build scenario dict
    fmt = 'compact' if compact else 'normal'
    print(f"Building scenario ({fmt} format)...")
    scenario_dict = build_scenario_dict(meta, port_rows, sw_task_rows, compact)
    
    # Determine output path
    if output is None:
        name = meta.get('name', 'generated').replace(' ', '_')
        suffix = '_compact' if compact else ''
        output = os.path.join(
            os.path.dirname(meta_path),
            f"{name}{suffix}.yaml"
        )
    
    # Generate YAML
    result = generate_yaml(scenario_dict, output)
    
    # Summary
    n_blocks = len(scenario_dict.get('ip_blocks', []))
    n_ports = len(port_rows)
    n_sw = len(sw_task_rows) if sw_task_rows else 0
    print(f"Generated: {result}")
    print(f"  IP blocks: {n_blocks}, Ports: {n_ports}, SW tasks: {n_sw}")
    
    return result
