"""
Timing Analyzer for simulation results.

Analyzes latency, critical path, and timing bottlenecks.
"""

from typing import Any, Dict, List, Optional, Tuple

from .simulator import BaseAnalyzer, SimulationResults, TaskResult


class TimingAnalyzer(BaseAnalyzer):
    """
    Analyzes timing metrics from simulation results.

    Metrics:
    - End-to-end latency
    - Critical path identification
    - Per-task timing breakdown
    - Slack analysis
    """

    def analyze(self, results: SimulationResults) -> Dict[str, Any]:
        """
        Analyze timing metrics.

        Args:
            results: SimulationResults from simulation

        Returns:
            Timing report dictionary
        """
        report = {
            'scenario_name': results.scenario_name,
            'total_latency_sec': results.total_time,
            'total_latency_ms': results.total_time * 1000,
            'task_timings': [],
            'critical_path': [],
            'earliest_start': None,
            'latest_end': None
        }

        if not results.task_results:
            return report

        # Collect all task timings
        task_timings = []
        earliest_start = float('inf')
        latest_end = 0.0

        for task_result in results.task_results:
            timing = {
                'task_id': task_result.task_id,
                'hw_name': task_result.hw_name,
                'start_time_ms': task_result.start_time * 1000,
                'end_time_ms': task_result.end_time * 1000,
                'duration_ms': task_result.duration * 1000
            }
            task_timings.append(timing)

            earliest_start = min(earliest_start, task_result.start_time)
            latest_end = max(latest_end, task_result.end_time)

        report['task_timings'] = sorted(task_timings, key=lambda x: x['start_time_ms'])
        report['earliest_start'] = earliest_start * 1000
        report['latest_end'] = latest_end * 1000

        # Identify critical path (tasks on the longest path)
        # Simple heuristic: tasks that end at or near the total end time
        critical_threshold = latest_end * 0.95  # Within 5% of end
        critical_path = [
            t['task_id'] for t in task_timings
            if t['end_time_ms'] / 1000 >= critical_threshold
        ]
        report['critical_path'] = critical_path

        return report

    def format_report(self, report: Dict[str, Any]) -> str:
        """Format report as readable string."""
        lines = [
            f"=== Timing Analysis: {report['scenario_name']} ===",
            f"Total End-to-End Latency: {report['total_latency_ms']:.3f} ms",
            "",
            "Task Timing Breakdown:"
        ]

        for timing in report['task_timings']:
            lines.append(
                f"  {timing['task_id']:20s} | {timing['hw_name']:15s} | "
                f"Start: {timing['start_time_ms']:8.3f} ms | "
                f"End: {timing['end_time_ms']:8.3f} ms | "
                f"Duration: {timing['duration_ms']:8.3f} ms"
            )

        if report['critical_path']:
            lines.append("")
            lines.append(f"Critical Path Tasks: {', '.join(report['critical_path'])}")

        return "\n".join(lines)
