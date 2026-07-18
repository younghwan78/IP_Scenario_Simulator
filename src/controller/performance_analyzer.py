"""
Performance Analyzer for simulation results.

Analyzes throughput, FPS, and hardware utilization.
"""

from typing import Any, Dict, List

from .simulator import BaseAnalyzer, SimulationResults

class PerformanceAnalyzer(BaseAnalyzer):
    """
    Analyzes performance metrics from simulation results.

    Metrics:
    - Throughput (frames/tasks per second)
    - Hardware utilization
    - Bottleneck identification
    """

    def analyze(self, results: SimulationResults) -> Dict[str, Any]:
        """
        Analyze performance metrics.

        Args:
            results: SimulationResults from simulation

        Returns:
            Performance report dictionary
        """
        report = {
            'scenario_name': results.scenario_name,
            'total_time_sec': results.total_time,
            'total_tasks': len(results.task_results),
            'throughput': {},
            'utilization': {},
            'bottleneck': None
        }

        if results.total_time <= 0:
            return report

        # Calculate task throughput
        report['throughput']['tasks_per_sec'] = len(results.task_results) / results.total_time

        # Calculate per-HW metrics
        hw_times: Dict[str, List[float]] = {}
        hw_durations: Dict[str, float] = {}

        for task_result in results.task_results:
            hw = task_result.hw_name
            if hw not in hw_times:
                hw_times[hw] = []
                hw_durations[hw] = 0.0
            hw_times[hw].append(task_result.duration)
            hw_durations[hw] += task_result.duration

        # Utilization = total active time / total simulation time
        for hw, duration in hw_durations.items():
            report['utilization'][hw] = min(1.0, duration / results.total_time)

        # Identify bottleneck (HW with highest utilization)
        if report['utilization']:
            bottleneck_hw = max(report['utilization'].items(), key=lambda x: x[1])
            report['bottleneck'] = {
                'hw_name': bottleneck_hw[0],
                'utilization': bottleneck_hw[1]
            }

        # Calculate FPS from actual simulated frame count
        if results.num_frames > 0:
            report['throughput']['estimated_fps'] = results.num_frames / results.total_time

        # Pixel throughput if pixel-based tasks
        total_pixels = 0
        for task_result in results.task_results:
            pixels = task_result.workload.get('pixels', 0)
            total_pixels += pixels

        if total_pixels > 0:
            report['throughput']['pixels_per_sec'] = total_pixels / results.total_time

        return report

    def format_report(self, report: Dict[str, Any]) -> str:
        """Format report as readable string."""
        lines = [
            f"=== Performance Analysis: {report['scenario_name']} ===",
            f"Total Simulation Time: {report['total_time_sec']*1000:.3f} ms",
            f"Total Tasks Executed: {report['total_tasks']}",
            "",
            "Throughput:",
            f"  Tasks/sec: {report['throughput'].get('tasks_per_sec', 0):.2f}",
        ]

        if 'estimated_fps' in report['throughput']:
            lines.append(f"  Estimated FPS: {report['throughput']['estimated_fps']:.2f}")
        if 'pixels_per_sec' in report['throughput']:
            lines.append(f"  Pixels/sec: {report['throughput']['pixels_per_sec']/1e6:.2f} MP/s")

        lines.append("")
        lines.append("Hardware Utilization:")
        for hw, util in report['utilization'].items():
            lines.append(f"  {hw}: {util*100:.1f}%")

        if report['bottleneck']:
            lines.append("")
            lines.append(f"Bottleneck: {report['bottleneck']['hw_name']} "
                        f"({report['bottleneck']['utilization']*100:.1f}% utilized)")

        return "\n".join(lines)
