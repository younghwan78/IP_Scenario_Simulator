"""
Power Analyzer for simulation results.

Analyzes static/dynamic power and energy consumption.
"""

from typing import Any, Dict, List

from .simulator import BaseAnalyzer, SimulationResults, TaskResult


class PowerAnalyzer(BaseAnalyzer):
    """
    Analyzes power consumption from simulation results.
    
    Metrics:
    - Total energy consumption
    - Per-HW power breakdown
    - Average power during simulation
    """
    
    def analyze(self, results: SimulationResults) -> Dict[str, Any]:
        """
        Analyze power metrics.
        
        Args:
            results: SimulationResults from simulation
            
        Returns:
            Power report dictionary
        """
        report = {
            'scenario_name': results.scenario_name,
            'total_time_sec': results.total_time,
            'total_energy_mj': 0.0,
            'average_power_mw': 0.0,
            'per_hw_energy': {},
            'per_task_energy': {}
        }
        
        if results.total_time <= 0:
            return report
        
        # Aggregate energy by HW and task
        hw_energy: Dict[str, float] = {}
        
        for task_result in results.task_results:
            hw = task_result.hw_name
            energy = task_result.power_consumed
            
            report['total_energy_mj'] += energy
            report['per_task_energy'][task_result.task_id] = energy
            
            if hw not in hw_energy:
                hw_energy[hw] = 0.0
            hw_energy[hw] += energy
        
        report['per_hw_energy'] = hw_energy
        
        # Average power = total energy / time
        report['average_power_mw'] = report['total_energy_mj'] / results.total_time
        
        return report
    
    def format_report(self, report: Dict[str, Any]) -> str:
        """Format report as readable string."""
        lines = [
            f"=== Power Analysis: {report['scenario_name']} ===",
            f"Total Simulation Time: {report['total_time_sec']*1000:.3f} ms",
            f"Total Energy: {report['total_energy_mj']:.3f} mJ",
            f"Average Power: {report['average_power_mw']:.3f} mW",
            "",
            "Energy by Hardware:"
        ]
        
        for hw, energy in report['per_hw_energy'].items():
            pct = (energy / report['total_energy_mj'] * 100) if report['total_energy_mj'] > 0 else 0
            lines.append(f"  {hw}: {energy:.3f} mJ ({pct:.1f}%)")
        
        return "\n".join(lines)
