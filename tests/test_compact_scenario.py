"""
Tests for compact YAML pre-processor.

Validates:
  - is_compact flag detection
  - Auto task generation
  - Size inheritance
  - Default mode
  - Full expand round-trip equivalence with original
"""

import pytest
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from src.model.compact_scenario import is_compact, expand_compact


# ============================================================
# is_compact detection
# ============================================================

class TestIsCompact:
    def test_compact_true(self):
        assert is_compact({'compact': True, 'name': 'test'}) is True

    def test_compact_false(self):
        assert is_compact({'compact': False, 'name': 'test'}) is False

    def test_compact_missing(self):
        assert is_compact({'name': 'test'}) is False

    def test_compact_truthy_string_not_accepted(self):
        """Only boolean True triggers compact mode."""
        assert is_compact({'compact': 'true'}) is False


# ============================================================
# Auto task generation
# ============================================================

class TestAutoTaskGeneration:
    def test_auto_task_from_hw_name(self):
        config = {
            'compact': True,
            'ip_blocks': [
                {
                    'ip_settings': {
                        'hw': 'CSIS_LINK',
                        'inputs': [{'port': 'DC_PHY', 'size': [0, 0, 4000, 2252]}],
                        'outputs': [{'port': 'LINK'}],
                    },
                    'edges': [],
                }
            ]
        }
        result = expand_compact(config)
        block = result['ip_blocks'][0]
        assert 'tasks' in block
        assert block['tasks'][0]['id'] == 't_csis_link'
        assert block['tasks'][0]['hw'] == 'CSIS_LINK'

    def test_explicit_tasks_preserved(self):
        config = {
            'compact': True,
            'ip_blocks': [
                {
                    'ip_settings': {
                        'hw': 'CSIS',
                        'inputs': [{'port': 'NFI', 'size': [0, 0, 100, 100]}],
                        'outputs': [],
                    },
                    'tasks': [{'id': 't_my_csis', 'hw': 'CSIS', 'description': 'Custom'}],
                    'edges': [],
                }
            ]
        }
        result = expand_compact(config)
        block = result['ip_blocks'][0]
        assert block['tasks'][0]['id'] == 't_my_csis'

    def test_sw_tasks_no_auto_hw_task(self):
        """Blocks with only sw_tasks should NOT get auto-generated HW task."""
        config = {
            'compact': True,
            'ip_blocks': [
                {
                    'sw_tasks': [{'id': 't_sw1', 'processor': 'CPU', 'duration_ms': 1.0}],
                    'edges': [],
                }
            ]
        }
        result = expand_compact(config)
        block = result['ip_blocks'][0]
        assert 'tasks' not in block


# ============================================================
# Default mode
# ============================================================

class TestDefaultMode:
    def test_mode_defaults_to_normal(self):
        config = {
            'compact': True,
            'ip_blocks': [
                {
                    'ip_settings': {
                        'hw': 'RGBP',
                        'inputs': [{'port': 'CINFIFO', 'size': [0, 0, 100, 100]}],
                        'outputs': [],
                    },
                    'edges': [],
                }
            ]
        }
        result = expand_compact(config)
        assert result['ip_blocks'][0]['ip_settings']['mode'] == 'Normal'

    def test_explicit_mode_preserved(self):
        config = {
            'compact': True,
            'ip_blocks': [
                {
                    'ip_settings': {
                        'hw': 'BYRP',
                        'mode': 'tDMSC',
                        'inputs': [{'port': 'RDMA', 'size': [0, 0, 100, 100]}],
                        'outputs': [],
                    },
                    'edges': [],
                }
            ]
        }
        result = expand_compact(config)
        assert result['ip_blocks'][0]['ip_settings']['mode'] == 'tDMSC'


# ============================================================
# Size inheritance
# ============================================================

class TestSizeInheritance:
    def test_input_inherits_previous_output(self):
        config = {
            'compact': True,
            'ip_blocks': [
                {
                    'ip_settings': {
                        'hw': 'CSIS_LINK',
                        'inputs': [{'port': 'DC_PHY', 'size': [0, 0, 4000, 2252]}],
                        'outputs': [{'port': 'LINK'}],
                    },
                    'edges': [],
                },
                {
                    'ip_settings': {
                        'hw': 'CSIS',
                        'inputs': [{'port': 'NFI_DEC'}],  # no size → inherit
                        'outputs': [{'port': 'COUTFIFO'}],
                    },
                    'edges': [],
                },
            ]
        }
        result = expand_compact(config)
        csis_input = result['ip_blocks'][1]['ip_settings']['inputs'][0]
        assert csis_input['size'] == [0, 0, 4000, 2252]

    def test_output_defaults_to_input_size(self):
        config = {
            'compact': True,
            'ip_blocks': [
                {
                    'ip_settings': {
                        'hw': 'RGBP',
                        'inputs': [{'port': 'CINFIFO', 'size': [0, 0, 1920, 1080]}],
                        'outputs': [{'port': 'COUTFIFO'}],  # no size → same as input
                    },
                    'edges': [],
                }
            ]
        }
        result = expand_compact(config)
        outp = result['ip_blocks'][0]['ip_settings']['outputs'][0]
        assert outp['size'] == [0, 0, 1920, 1080]

    def test_explicit_size_not_overridden(self):
        config = {
            'compact': True,
            'ip_blocks': [
                {
                    'ip_settings': {
                        'hw': 'CSIS_LINK',
                        'inputs': [{'port': 'DC_PHY', 'size': [0, 0, 4000, 2252]}],
                        'outputs': [{'port': 'LINK', 'size': [0, 0, 2000, 1126]}],
                    },
                    'edges': [],
                }
            ]
        }
        result = expand_compact(config)
        outp = result['ip_blocks'][0]['ip_settings']['outputs'][0]
        assert outp['size'] == [0, 0, 2000, 1126]  # explicit size preserved

    def test_coutfifo_preferred_for_flowing_size(self):
        """When multiple outputs exist, COUTFIFO should be preferred
        for the flowing size that next block inherits."""
        config = {
            'compact': True,
            'ip_blocks': [
                {
                    'ip_settings': {
                        'hw': 'CSIS',
                        'inputs': [{'port': 'NFI_DEC', 'size': [0, 0, 4000, 2252]}],
                        'outputs': [
                            {'port': 'COUTFIFO'},  # 4000x2252 (from input)
                            {'port': 'CSIS_WDMA', 'size': [0, 0, 100, 100]},
                        ],
                    },
                    'edges': [],
                },
                {
                    'ip_settings': {
                        'hw': 'PREP',
                        'inputs': [{'port': 'CINFIFO'}],  # should inherit COUTFIFO size
                        'outputs': [],
                    },
                    'edges': [],
                },
            ]
        }
        result = expand_compact(config)
        prep_input = result['ip_blocks'][1]['ip_settings']['inputs'][0]
        assert prep_input['size'] == [0, 0, 4000, 2252]

    def test_chain_of_three_blocks(self):
        """Size flows through: A(4000x2252) → B(inherit) → C(inherit)."""
        config = {
            'compact': True,
            'ip_blocks': [
                {
                    'ip_settings': {
                        'hw': 'A',
                        'inputs': [{'port': 'IN', 'size': [0, 0, 4000, 2252]}],
                        'outputs': [{'port': 'COUTFIFO'}],
                    },
                    'edges': [],
                },
                {
                    'ip_settings': {
                        'hw': 'B',
                        'inputs': [{'port': 'CINFIFO'}],
                        'outputs': [{'port': 'COUTFIFO'}],
                    },
                    'edges': [],
                },
                {
                    'ip_settings': {
                        'hw': 'C',
                        'inputs': [{'port': 'CINFIFO'}],
                        'outputs': [{'port': 'OUT'}],
                    },
                    'edges': [],
                },
            ]
        }
        result = expand_compact(config)
        c_in = result['ip_blocks'][2]['ip_settings']['inputs'][0]
        c_out = result['ip_blocks'][2]['ip_settings']['outputs'][0]
        assert c_in['size'] == [0, 0, 4000, 2252]
        assert c_out['size'] == [0, 0, 4000, 2252]


# ============================================================
# compact flag removal
# ============================================================

class TestCompactFlagRemoval:
    def test_compact_key_removed(self):
        config = {
            'compact': True,
            'name': 'test',
            'ip_blocks': []
        }
        result = expand_compact(config)
        assert 'compact' not in result

    def test_original_not_modified(self):
        config = {
            'compact': True,
            'ip_blocks': [
                {
                    'ip_settings': {
                        'hw': 'X',
                        'inputs': [{'port': 'IN', 'size': [0, 0, 100, 100]}],
                        'outputs': [],
                    },
                    'edges': [],
                }
            ]
        }
        import copy
        original = copy.deepcopy(config)
        expand_compact(config)
        assert config == original  # original untouched


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
