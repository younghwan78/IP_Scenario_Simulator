"""
Compact YAML Pre-processor for Scenario Configuration.

Expands compact syntax (compact: true) into the full ip_blocks format
that create_scenario_from_blocks() expects.

Compact rules:
  1) tasks 생략 → id="t_{hw.lower()}", hw=ip_settings.hw, description=hw
  2) inputs[].size 생략 → 이전 block의 primary output size 상속
  3) outputs[].size 생략 → 해당 block의 primary input size 사용
  4) mode 생략 → "Normal"
  5) compact: true 키 제거 후 반환

Note: 이 모듈은 순수 dict→dict 변환이며, scenario model에 의존하지 않음.
"""

from __future__ import annotations

import copy
import logging
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)


# ── Public API ──────────────────────────────────────────────────

def is_compact(config: dict) -> bool:
    """Check if config uses compact syntax."""
    return config.get('compact', False) is True


def expand_compact(config: dict) -> dict:
    """Expand compact syntax to full ip_blocks format.

    The returned dict is compatible with create_scenario_from_blocks().
    The original config is not modified.

    Args:
        config: Raw scenario config dict with compact: true.

    Returns:
        Expanded config dict (compact key removed).
    """
    config = copy.deepcopy(config)

    ip_blocks = config.get('ip_blocks', [])

    # Track the "current flowing size" — the primary output size
    # of the immediately preceding block. Used for size inheritance.
    prev_primary_output_size: Optional[List] = None

    for block in ip_blocks:
        ip_settings = block.get('ip_settings', {})
        hw_name = ip_settings.get('hw', '')

        # ── (1) Auto-generate tasks if omitted ───────────────────
        if 'tasks' not in block and 'sw_tasks' not in block:
            auto_task_id = f"t_{hw_name.lower()}"
            block['tasks'] = [{
                'id': auto_task_id,
                'hw': hw_name,
                'description': hw_name,
            }]

        # ── (2) Default mode ─────────────────────────────────────
        if ip_settings and 'mode' not in ip_settings:
            ip_settings['mode'] = 'Normal'

        # ── (3) Resolve input sizes (inheritance) ────────────────
        inputs = ip_settings.get('inputs', [])
        for inp in inputs:
            if 'size' not in inp:
                if prev_primary_output_size is not None:
                    inp['size'] = list(prev_primary_output_size)
                else:
                    logger.warning(
                        f"[compact] {hw_name}: input port "
                        f"'{inp.get('port', '?')}' has no size and "
                        f"no previous block to inherit from."
                    )

        # ── (4) Determine this block's primary input size ────────
        primary_input_size = _get_primary_input_size(inputs)

        # ── (5) Resolve output sizes (default = primary input) ───
        outputs = ip_settings.get('outputs', [])
        for outp in outputs:
            if 'size' not in outp:
                if primary_input_size is not None:
                    outp['size'] = list(primary_input_size)

        # ── (6) Update flowing size for next block ───────────────
        # Primary output size = the largest output (by pixel count),
        # preferring COUTFIFO-type ports if present.
        if outputs:
            prev_primary_output_size = _get_primary_output_size(
                outputs, fallback=primary_input_size
            )
        elif primary_input_size is not None:
            # No outputs (passthrough) → carry input size forward
            prev_primary_output_size = primary_input_size

    # ── Remove compact flag ──────────────────────────────────────
    config.pop('compact', None)

    return config


# ── Internal helpers ────────────────────────────────────────────

def _get_primary_input_size(
    inputs: List[Dict[str, Any]],
) -> Optional[List]:
    """Get the primary (largest by pixel count) input size.

    Args:
        inputs: List of input port dicts.

    Returns:
        [x, y, w, h] list or None.
    """
    if not inputs:
        return None

    best_size = None
    best_pixels = -1

    for inp in inputs:
        size = inp.get('size')
        if not size:
            continue
        w, h = _extract_wh(size)
        pixels = w * h
        if pixels > best_pixels:
            best_pixels = pixels
            best_size = size

    return list(best_size) if best_size else None


def _get_primary_output_size(
    outputs: List[Dict[str, Any]],
    fallback: Optional[List] = None,
) -> Optional[List]:
    """Get the primary output size.

    Prefers COUTFIFO/COUT-type ports. Falls back to largest by pixels.

    Args:
        outputs: List of output port dicts.
        fallback: Fallback size if no outputs have size.

    Returns:
        [x, y, w, h] list or None.
    """
    if not outputs:
        return fallback

    # First pass: look for COUTFIFO or *COUTFIFO* port (OTF output)
    for outp in outputs:
        port = outp.get('port', '')
        if 'COUTFIFO' in port or 'coutfifo' in port.lower():
            size = outp.get('size')
            if size:
                return list(size)

    # Second pass: largest output by pixel count
    best_size = None
    best_pixels = -1
    for outp in outputs:
        size = outp.get('size')
        if not size:
            continue
        w, h = _extract_wh(size)
        pixels = w * h
        if pixels > best_pixels:
            best_pixels = pixels
            best_size = size

    if best_size:
        return list(best_size)

    return list(fallback) if fallback else None


def _extract_wh(size: List) -> Tuple[int, int]:
    """Extract (width, height) from size list.

    Supports both [x, y, w, h] and [w, h] formats.
    """
    if len(size) == 4:
        return size[2], size[3]
    elif len(size) == 2:
        return size[0], size[1]
    return 0, 0
