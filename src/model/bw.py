"""
Shared DMA bandwidth / BW-power calculation.

Single source of truth for the BW formula used by the simulation report,
the BW timeline chart, and the exploration engine:

    BW (MB/s)     = comp_ratio × fps × W × H × (bitwidth/8) × BPP_MAP[fmt] × r_w_rate / 1e6
    BW power (mW) = BW (MB/s) × bw_power_coeff (mW/GB/s) / 1000 × llc_weight
    BW power (mA) = BW power (mW) / vBat / pmic_efficiency

Compression ratio is applied when 'comp' is enabled — any value other than
'disable'/empty counts as enabled (covers both 'enable' and specific types
like 'SBWC'/'AFBC' set by exploration feature overrides).
"""

from __future__ import annotations
from typing import Any, Dict

from .constants import BPP_MAP, BPP_DEFAULT


def is_dma_port_name(port_name: str) -> bool:
    """Name-based heuristic: RDMA/WDMA ports generate memory BW."""
    upper = port_name.upper()
    return 'RDMA' in upper or 'WDMA' in upper


def comp_enabled(comp: Any) -> bool:
    """Whether a port's compression setting counts as enabled."""
    return bool(comp) and comp != 'disable'


def calc_port_bw(port_info: dict, fps: float,
                 bw_power_coeff: float = 80.0,
                 vBat: float = 4.0,
                 pmic_eff: float = 0.85) -> Dict[str, Any]:
    """Calculate BW and BW power for a single DMA port.

    Args:
        port_info: Port dict from ip_settings (size, format, bitwidth,
                   comp, comp_ratio, llc_enable, llc_weight, r_w_rate ...)
        fps: Scenario frame rate
        bw_power_coeff: BW power coefficient [mW/GB/s]
        vBat: Battery voltage [V] (for mA conversion)
        pmic_eff: PMIC efficiency (for mA conversion)

    Returns:
        Dict with bw_mbs / bw_power_mw / bw_power_ma plus the resolved
        port parameters (width, height, bitwidth, format, comp, comp_ratio,
        llc_enable, llc_hit_ratio, r_w_rate).
    """
    sz = port_info.get('size', [])
    if len(sz) < 4 or sz[2] <= 0 or sz[3] <= 0:
        return {'bw_mbs': 0.0, 'bw_power_mw': 0.0, 'bw_power_ma': 0.0,
                'width': 0, 'height': 0}

    width, height = sz[2], sz[3]
    bitwidth = port_info.get('bitwidth', 8)
    fmt = port_info.get('format', '')
    bpp = BPP_MAP.get(fmt, BPP_DEFAULT)
    r_w_rate = port_info.get('r_w_rate', 1.0)

    comp = port_info.get('comp', 'disable')
    comp_ratio = port_info.get('comp_ratio', 1.0) if comp_enabled(comp) else 1.0

    llc_enable = port_info.get('llc_enable', 'disable')
    llc_weight = port_info.get('llc_weight', 1.0) if llc_enable == 'enable' else 1.0
    llc_hit_ratio = port_info.get('llc_hit_ratio', 0.0) if llc_enable == 'enable' else 0.0

    bw_mbs = comp_ratio * fps * width * height * (bitwidth / 8) * bpp * r_w_rate / 1e6
    bw_power_mw = bw_mbs * bw_power_coeff / 1000 * llc_weight
    bw_power_ma = bw_power_mw / vBat / pmic_eff if (vBat > 0 and pmic_eff > 0) else 0.0

    return {
        'bw_mbs': bw_mbs,
        'bw_power_mw': bw_power_mw,
        'bw_power_ma': bw_power_ma,
        'width': width,
        'height': height,
        'bitwidth': bitwidth,
        'format': fmt,
        'comp': comp,
        'comp_ratio': comp_ratio,
        'llc_enable': llc_enable,
        'llc_hit_ratio': llc_hit_ratio,
        'r_w_rate': r_w_rate,
    }
