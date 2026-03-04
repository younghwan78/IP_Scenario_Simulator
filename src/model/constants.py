"""
Shared constants for format-related calculations.

BPP_MAP: Bytes-Per-Pixel multiplier for each color format.
Used by simulator, exploration engine, report generator, and visualizer
for bandwidth and transfer size calculations.
"""

# Bytes-Per-Pixel map: format name → pixel size multiplier
BPP_MAP = {
    # YUV family
    "NV12": 1.5,
    "NV21": 1.5,
    "YUV420": 1.5,
    "YUV422": 2.0,
    "NV16": 2.0,
    "YUYV": 2.0,
    "UYVY": 2.0,
    "YUV444": 3.0,
    "Y": 1.0,
    "Y8": 1.0,
    "GREY": 1.0,
    "UV8": 2.0,
    # RGB family
    "RGB": 3.0,
    "RGB888": 3.0,
    "BGR": 3.0,
    "ARGB": 4.0,
    "RGBA": 4.0,
    "BGRA": 4.0,
    "ABGR": 4.0,
    # RAW / Bayer
    "RAW": 1.0,
    "RAW8": 1.0,
    "RAW10": 1.25,
    "RAW12": 1.5,
    "RAW14": 1.75,
    "RAW16": 2.0,
    "BAYER": 1.0,
    "BAYER_PACKED": 1.0,
    "BAYER_UNPACKED": 2.0,
    # Packed / special
    "P010": 2.0,
    "P210": 3.2,
    "STAT": 1.0,
}

# Default BPP for unknown/unrecognized formats
BPP_DEFAULT = 1.0
