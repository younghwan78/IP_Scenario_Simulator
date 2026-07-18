"""
Shared DMA bandwidth / BW-power calculation.

Single source of truth for the BW formula used by the simulation report,
the BW timeline chart, and the exploration engine:

    raw_bw (MB/s) = comp_ratio × fps × W × H × (bitwidth/8) × BPP_MAP[fmt] × r_w_rate / 1e6

LLC (Last Level Cache) model — LLC hits replace DRAM accesses:

    hit        = port llc_hit_ratio → scenario default → HW default (priority)
    dram_bw    = raw_bw × (1 − hit)      ← reported as 'bw_mbs' (DRAM-effective)
    llc_bw     = raw_bw × hit
    bw_power   = dram_bw × bw_power_coeff/1000 + llc_bw × llc_power_coeff/1000

'bw_mbs' is the DRAM-effective BW, so every consumer (totals, MIF level
determination, BW chart, power summation) automatically reflects the LLC
saving. 'raw_bw_mbs' / 'llc_bw_mbs' carry the split for display.

Backward compatibility: when no hit ratio is available anywhere but a
legacy 'llc_weight' is given, the old power-weight model is used
(bw_power = raw_bw × coeff × weight, no DRAM BW reduction).

Compression ratio is applied when 'comp' is enabled — any value other than
'disable'/empty counts as enabled (covers both 'enable' and specific types
like 'SBWC'/'AFBC' set by exploration feature overrides).
"""

from __future__ import annotations
from typing import Any, Dict

from .constants import BPP_MAP, BPP_DEFAULT

# Default LLC access power coefficient [mW/GB/s] — much cheaper than DRAM
DEFAULT_LLC_POWER_COEFF = 8.0


def is_dma_port_name(port_name: str) -> bool:
    """Name-based heuristic: RDMA/WDMA ports generate memory BW."""
    upper = port_name.upper()
    return 'RDMA' in upper or 'WDMA' in upper


def comp_enabled(comp: Any) -> bool:
    """Whether a port's compression setting counts as enabled."""
    return bool(comp) and comp != 'disable'


def llc_enabled(port_info: dict) -> bool:
    """Whether a port's LLC setting counts as enabled.

    Accepts both the canonical 'llc_enable' key and the legacy 'llc' key
    (scenario loaders normalize 'llc' → 'llc_enable', but direct dicts may
    still carry either).
    """
    val = port_info.get('llc_enable', port_info.get('llc', 'disable'))
    return val == 'enable'


def port_buffer_bytes(port_info: dict) -> float:
    """Frame buffer footprint of a port in bytes (for LLC capacity checks).

    footprint = W × H × (bitwidth/8) × BPP[fmt] × comp_ratio
    """
    sz = port_info.get('size', [])
    if len(sz) < 4 or sz[2] <= 0 or sz[3] <= 0:
        return 0.0
    width, height = sz[2], sz[3]
    bitwidth = port_info.get('bitwidth', 8)
    bpp = BPP_MAP.get(port_info.get('format', ''), BPP_DEFAULT)
    comp = port_info.get('comp', 'disable')
    comp_ratio = port_info.get('comp_ratio', 1.0) if comp_enabled(comp) else 1.0
    return width * height * (bitwidth / 8) * bpp * comp_ratio


def calc_port_bw(port_info: dict, fps: float,
                 bw_power_coeff: float = 80.0,
                 vBat: float = 4.0,
                 pmic_eff: float = 0.85,
                 llc_power_coeff: float = DEFAULT_LLC_POWER_COEFF,
                 llc_default_hit_ratio: float = 0.0) -> Dict[str, Any]:
    """Calculate BW and BW power for a single DMA port.

    Args:
        port_info: Port dict from ip_settings (size, format, bitwidth,
                   comp, comp_ratio, llc_enable, llc_hit_ratio, llc_weight,
                   r_w_rate ...)
        fps: Scenario frame rate
        bw_power_coeff: DRAM BW power coefficient [mW/GB/s]
        vBat: Battery voltage [V] (for mA conversion)
        pmic_eff: PMIC efficiency (for mA conversion)
        llc_power_coeff: LLC access power coefficient [mW/GB/s]
        llc_default_hit_ratio: Fallback hit ratio when the port doesn't
                   specify one (scenario/HW project default)

    Returns:
        Dict with bw_mbs (DRAM-effective) / raw_bw_mbs / llc_bw_mbs /
        bw_power_mw / bw_power_ma plus resolved port parameters.
    """
    sz = port_info.get('size', [])
    if len(sz) < 4 or sz[2] <= 0 or sz[3] <= 0:
        return {'bw_mbs': 0.0, 'raw_bw_mbs': 0.0, 'llc_bw_mbs': 0.0,
                'bw_power_mw': 0.0, 'bw_power_ma': 0.0,
                'width': 0, 'height': 0}

    width, height = sz[2], sz[3]
    bitwidth = port_info.get('bitwidth', 8)
    fmt = port_info.get('format', '')
    bpp = BPP_MAP.get(fmt, BPP_DEFAULT)
    r_w_rate = port_info.get('r_w_rate', 1.0)

    comp = port_info.get('comp', 'disable')
    comp_ratio = port_info.get('comp_ratio', 1.0) if comp_enabled(comp) else 1.0

    raw_bw_mbs = comp_ratio * fps * width * height * (bitwidth / 8) * bpp * r_w_rate / 1e6

    llc_on = llc_enabled(port_info)
    llc_state = 'enable' if llc_on else 'disable'
    hit_ratio = 0.0
    llc_bw_mbs = 0.0
    dram_bw_mbs = raw_bw_mbs

    if llc_on:
        hit_ratio = port_info.get('llc_hit_ratio', None)
        if hit_ratio is None:
            hit_ratio = llc_default_hit_ratio
        hit_ratio = max(0.0, min(1.0, float(hit_ratio)))

        if hit_ratio > 0:
            # Hit-ratio model: LLC hits bypass DRAM
            llc_bw_mbs = raw_bw_mbs * hit_ratio
            dram_bw_mbs = raw_bw_mbs * (1.0 - hit_ratio)
            bw_power_mw = (dram_bw_mbs * bw_power_coeff / 1000
                           + llc_bw_mbs * llc_power_coeff / 1000)
        else:
            # Legacy power-weight model (no DRAM BW reduction)
            llc_weight = port_info.get('llc_weight', 1.0)
            bw_power_mw = raw_bw_mbs * bw_power_coeff / 1000 * llc_weight
    else:
        bw_power_mw = raw_bw_mbs * bw_power_coeff / 1000

    bw_power_ma = bw_power_mw / vBat / pmic_eff if (vBat > 0 and pmic_eff > 0) else 0.0

    return {
        'bw_mbs': dram_bw_mbs,        # DRAM-effective BW (drives MIF/totals)
        'raw_bw_mbs': raw_bw_mbs,     # before LLC reduction
        'llc_bw_mbs': llc_bw_mbs,     # served by LLC
        'bw_power_mw': bw_power_mw,
        'bw_power_ma': bw_power_ma,
        'width': width,
        'height': height,
        'bitwidth': bitwidth,
        'format': fmt,
        'comp': comp,
        'comp_ratio': comp_ratio,
        'llc_enable': llc_state,
        'llc_hit_ratio': hit_ratio,
        'r_w_rate': r_w_rate,
    }
