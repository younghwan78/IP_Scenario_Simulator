"""
HW Info Database - CSV-based hardware configuration loader.

Loads project-specific hardware information from:
- {project}_info.csv: Per-IP power/PPC/voltage domain/DVFS group info
- {project}_dvfs.csv: DVFS tables (clock speed & voltage per level/ASV group)

Matching Rules:
    hw.yaml 'name' ↔ info.csv 'Name'  (1:1 direct match)
    info.csv 'DVFS' → dvfs.csv table   (DVFS table lookup)
    info.csv 'VDD'  → voltage domain    (same VDD → highest voltage)
"""

from __future__ import annotations
import csv
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple


# ============================================================
# Data Classes
# ============================================================

@dataclass
class IPInfo:
    """Single IP mode info from info.csv.
    
    Attributes:
        name: IP name (must match hw.yaml 'name')
        mode: Operating mode (Normal, tDMSC, FHD, etc.)
        unit_power: Power coefficient [mW/MP@30fps]
        idc: Idle power coefficient
        ppc: Pixels Per Clock
        vdd: Voltage domain name (VDD_CAM, VDD_INT, etc.)
        dvfs_group: DVFS table reference name (CSIS, CAM, INT, etc.)
    """
    name: str
    mode: str
    unit_power: float
    idc: float
    ppc: int
    vdd: str
    dvfs_group: str


@dataclass
class DVFSLevel:
    """Single DVFS level entry.
    
    Attributes:
        level: DVFS level number (0 = highest speed)
        speed: Clock speed in MHz
        voltages: ASV group → voltage in mV (key: ASV group index)
    """
    level: int
    speed: float
    voltages: Dict[int, float] = field(default_factory=dict)


@dataclass
class DVFSTable:
    """DVFS table for an IP group.
    
    Attributes:
        name: Group name (MIF, INT, CAM, INTCAM, CPU, etc.)
        levels: List of DVFSLevel sorted by level (ascending)
    """
    name: str
    levels: List[DVFSLevel] = field(default_factory=list)
    
    def get_level(self, level_num: int) -> Optional[DVFSLevel]:
        """Get a specific DVFS level."""
        for lvl in self.levels:
            if lvl.level == level_num:
                return lvl
        return None
    
    def find_min_level_for_speed(self, required_speed: float) -> Optional[DVFSLevel]:
        """Find the minimum DVFS level whose speed >= required_speed.
        
        Levels are sorted so level 0 = highest speed.
        We want the HIGHEST level number (lowest speed) that still meets the requirement.
        
        Args:
            required_speed: Required clock speed in MHz
            
        Returns:
            DVFSLevel with speed >= required_speed, or None if impossible
        """
        # Filter valid levels (speed > 0 and speed >= required)
        candidates = [lvl for lvl in self.levels 
                       if lvl.speed > 0 and lvl.speed >= required_speed]
        if not candidates:
            return None
        # Return the one with lowest speed (highest level number) among candidates
        return min(candidates, key=lambda l: l.speed)
    
    def get_voltage(self, level: DVFSLevel, asv_group: int) -> float:
        """Get voltage for a specific level and ASV group.
        
        Args:
            level: DVFSLevel
            asv_group: ASV group index (0-8, default 4)
            
        Returns:
            Voltage in mV
        """
        return level.voltages.get(asv_group, 0.0)


@dataclass
class HWInfoDB:
    """Complete HW info database for a project.
    
    Attributes:
        project_name: Project identifier (from info.csv header)
        ip_infos: name → list of IPInfo (multiple modes per IP)
        dvfs_tables: dvfs_group_name → DVFSTable
    """
    project_name: str = ""
    ip_infos: Dict[str, List[IPInfo]] = field(default_factory=dict)
    dvfs_tables: Dict[str, DVFSTable] = field(default_factory=dict)
    
    def get_ip_info(self, name: str, mode: str = "Normal") -> Optional[IPInfo]:
        """Get IPInfo for a specific IP name and mode.
        
        Args:
            name: IP name (matching hw.yaml 'name')
            mode: Operating mode (default: "Normal")
            
        Returns:
            IPInfo or None if not found
        """
        infos = self.ip_infos.get(name, [])
        for info in infos:
            if info.mode == mode:
                return info
        # Fallback: return first mode if requested mode not found
        return infos[0] if infos else None
    
    def get_ip_modes(self, name: str) -> List[str]:
        """Get all available modes for an IP."""
        return [info.mode for info in self.ip_infos.get(name, [])]
    
    def get_dvfs_table(self, dvfs_group: str) -> Optional[DVFSTable]:
        """Get DVFS table by group name."""
        return self.dvfs_tables.get(dvfs_group)
    
    def get_all_ip_names(self) -> List[str]:
        """Get all IP names in the database."""
        return list(self.ip_infos.keys())
    
    def validate_against_hw(self, hw_registry: Dict[str, Any]) -> List[str]:
        """Validate that hw_registry IPs exist in info.csv and their DVFS tables exist.
        
        Checks:
        1. Each IP in hw_registry (IPNode type) must have a matching entry in info.csv
        2. Each IP's DVFS group (from info.csv) must have a corresponding dvfs.csv table
        
        Args:
            hw_registry: Dict of hw_name → HWNode
            
        Returns:
            List of error messages (empty = valid)
        """
        from .hw_nodes import IPNode, ProcessorNode, MemoryNode
        
        errors = []
        
        for hw_name, hw_node in hw_registry.items():
            # Only validate IPNode types (not Sensor, Display, Processor, Memory)
            if not isinstance(hw_node, IPNode):
                # ProcessorNode and MemoryNode also need info.csv matching
                if isinstance(hw_node, (ProcessorNode, MemoryNode)):
                    if hw_name not in self.ip_infos:
                        # Warning only, not error for non-IP nodes
                        continue
                continue
            
            # Check 1: IP name exists in info.csv
            if hw_name not in self.ip_infos:
                errors.append(
                    f"IP '{hw_name}' in hw.yaml has no matching entry in info.csv. "
                    f"Available names: {', '.join(self.get_all_ip_names())}"
                )
                continue
            
            # Check 2: DVFS group exists in dvfs.csv
            ip_info = self.ip_infos[hw_name][0]  # Use first mode for DVFS group
            dvfs_group = ip_info.dvfs_group
            if dvfs_group and dvfs_group not in self.dvfs_tables:
                errors.append(
                    f"IP '{hw_name}' references DVFS group '{dvfs_group}' "
                    f"but no such table found in dvfs.csv. "
                    f"Available tables: {', '.join(self.dvfs_tables.keys())}"
                )
        
        return errors


# ============================================================
# CSV Parsers
# ============================================================

def load_info_csv(path: str) -> Tuple[str, Dict[str, List[IPInfo]]]:
    """Parse {project}_info.csv file.
    
    CSV format:
        Row 1: Project,{name},,,,,,
        Row 2: Name,Mode,Unit Power,IDC,PPC,VDD,DVFS  (header)
        Row 3+: data rows (IP per line, multiple rows per IP for multiple modes)
    
    Args:
        path: Path to info.csv file
        
    Returns:
        Tuple of (project_name, dict of ip_name → list of IPInfo)
        
    Raises:
        FileNotFoundError: If file doesn't exist
        ValueError: If file format is invalid
    """
    filepath = Path(path)
    if not filepath.exists():
        raise FileNotFoundError(f"Info CSV not found: {path}")
    
    project_name = ""
    ip_infos: Dict[str, List[IPInfo]] = {}
    
    with open(filepath, 'r', encoding='utf-8-sig') as f:
        reader = csv.reader(f)
        rows = list(reader)
    
    if len(rows) < 3:
        raise ValueError(f"Info CSV too short (need at least 3 rows): {path}")
    
    # Row 0: Project name
    project_row = rows[0]
    if project_row and project_row[0].strip().lower() == 'project':
        project_name = project_row[1].strip() if len(project_row) > 1 else ""
    
    # Row 1: Header (skip, we know the format)
    # Row 2+: Data
    for row_idx, row in enumerate(rows[2:], start=3):
        # Skip empty rows
        if not row or not row[0].strip():
            continue
        
        try:
            name = row[0].strip()
            mode = row[1].strip() if len(row) > 1 else "Normal"
            unit_power = float(row[2].strip()) if len(row) > 2 and row[2].strip() else 0.0
            idc = float(row[3].strip()) if len(row) > 3 and row[3].strip() else 0.0
            ppc = int(row[4].strip()) if len(row) > 4 and row[4].strip() else 1
            vdd = row[5].strip() if len(row) > 5 else ""
            dvfs_group = row[6].strip() if len(row) > 6 else ""
            
            ip_info = IPInfo(
                name=name,
                mode=mode,
                unit_power=unit_power,
                idc=idc,
                ppc=ppc,
                vdd=vdd,
                dvfs_group=dvfs_group
            )
            
            if name not in ip_infos:
                ip_infos[name] = []
            ip_infos[name].append(ip_info)
            
        except (ValueError, IndexError) as e:
            raise ValueError(f"Error parsing info.csv row {row_idx}: {row} - {e}")
    
    return project_name, ip_infos


def load_dvfs_csv(path: str) -> Dict[str, DVFSTable]:
    """Parse {project}_dvfs.csv file.
    
    CSV format (repeating blocks separated by empty rows):
        Row: {ProjectName},{version},,,,,,,,,,
        Row: {GroupName},,,,,,,,,,, 
        Row: LEVEL,SPEED,ASV0,ASV1,...,ASV8
        Row: 0,4205.5,993.75,...
        ...
        (empty rows)
        Row: {NextGroupName},,,,,,,,,,,
        ...
    
    Args:
        path: Path to dvfs.csv file
        
    Returns:
        Dict of group_name → DVFSTable
        
    Raises:
        FileNotFoundError: If file doesn't exist
    """
    filepath = Path(path)
    if not filepath.exists():
        raise FileNotFoundError(f"DVFS CSV not found: {path}")
    
    with open(filepath, 'r', encoding='utf-8-sig') as f:
        reader = csv.reader(f)
        rows = list(reader)
    
    dvfs_tables: Dict[str, DVFSTable] = {}
    
    i = 0
    # Skip first row (project name/version)
    if rows and rows[0][0].strip():
        i = 1
    
    while i < len(rows):
        row = rows[i]
        
        # Skip empty rows
        if not row or not row[0].strip() or all(not cell.strip() for cell in row):
            i += 1
            continue
        
        # Check if this is a group name row (non-numeric first cell, not "LEVEL")
        first_cell = row[0].strip()
        if first_cell.upper() == 'LEVEL':
            # This shouldn't happen without a group name before it
            i += 1
            continue
        
        # Try to parse as number - if it is, it's a data row (skip)
        try:
            float(first_cell)
            i += 1
            continue
        except ValueError:
            pass
        
        # This is a group name row
        group_name = first_cell
        i += 1
        
        # Next non-empty row should be the header (LEVEL, SPEED, ASV0, ...)
        while i < len(rows):
            header_row = rows[i]
            if header_row and header_row[0].strip().upper() == 'LEVEL':
                break
            i += 1
        
        if i >= len(rows):
            break
        
        # Parse ASV column headers
        header_row = rows[i]
        asv_headers = []
        for col_idx in range(2, len(header_row)):
            h = header_row[col_idx].strip()
            if h:
                # Handle both "ASV0" format and plain "0" format
                if h.upper().startswith('ASV'):
                    try:
                        asv_headers.append(int(h[3:]))
                    except ValueError:
                        asv_headers.append(col_idx - 2)
                else:
                    try:
                        asv_headers.append(int(h))
                    except ValueError:
                        asv_headers.append(col_idx - 2)
            else:
                asv_headers.append(col_idx - 2)
        
        i += 1
        
        # Parse data rows until empty row or next group
        levels: List[DVFSLevel] = []
        while i < len(rows):
            data_row = rows[i]
            
            # Empty row = end of this table
            if not data_row or not data_row[0].strip():
                i += 1
                break
            
            try:
                level_num = int(data_row[0].strip())
                speed = float(data_row[1].strip()) if len(data_row) > 1 and data_row[1].strip() else 0.0
                
                voltages: Dict[int, float] = {}
                for col_idx, asv_idx in enumerate(asv_headers):
                    cell_idx = col_idx + 2
                    if cell_idx < len(data_row) and data_row[cell_idx].strip():
                        voltages[asv_idx] = float(data_row[cell_idx].strip())
                
                levels.append(DVFSLevel(
                    level=level_num,
                    speed=speed,
                    voltages=voltages
                ))
            except (ValueError, IndexError):
                # Non-numeric row = end of data
                break
            
            i += 1
        
        if levels:
            dvfs_tables[group_name] = DVFSTable(name=group_name, levels=levels)
    
    return dvfs_tables


def create_hw_info_db(info_path: str, dvfs_path: str) -> HWInfoDB:
    """Create HWInfoDB from CSV files.
    
    Args:
        info_path: Path to {project}_info.csv
        dvfs_path: Path to {project}_dvfs.csv
        
    Returns:
        Populated HWInfoDB instance
    """
    project_name, ip_infos = load_info_csv(info_path)
    dvfs_tables = load_dvfs_csv(dvfs_path)
    
    return HWInfoDB(
        project_name=project_name,
        ip_infos=ip_infos,
        dvfs_tables=dvfs_tables
    )
