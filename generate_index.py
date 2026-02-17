#!/usr/bin/env python3
"""
Generate index.html for GitHub Pages report listing.

Scans docs/reports/{project}/ directories for report files and generates
a styled index.html with grouped navigation.

Usage:
    python generate_index.py [--reports-dir docs/reports] [--output docs/index.html]
"""

import argparse
import os
import re
from collections import defaultdict
from datetime import datetime

# Report file pattern: {project}-{scenario}-{YYYYMMDD-HHMMSS}-{writer}_suffix.ext
REPORT_PATTERN = re.compile(
    r'^(?P<project>[^-]+)-(?P<scenario>[^-]+(?:-[^-]+)*?)'
    r'-(?P<timestamp>\d{8}-\d{6})'
    r'-(?P<writer>[^_]+)'
    r'_(?P<suffix>.+)$'
)

# Suffixes we care about (display name → file suffix)
SUFFIX_LABELS = {
    'simulation_result.html': '📊 Simulation Report',
    'simulation_result.md': '📝 Simulation (MD)',
    'exploration_result.html': '🔍 Exploration Report',
    'exploration_result.md': '📝 Exploration (MD)',
    'timing_chart.html': '⏱ Timing Chart',
    'bw_chart.html': '📈 BW Chart',
    'timing_chart.png': '🖼 Timing PNG',
    'bw_chart.png': '🖼 BW PNG',
    'top_view.html': '🏗 Top View',
    'level1_view.html': '🏗 Level 1 View',
    'level2_view.html': '🏗 Level 2 View',
    'level3_view.html': '🏗 Level 3 View',
    'task_topology_view.html': '🔗 Task Topology',
    'results.csv': '📋 CSV Results',
    'trace.json': '🔬 Perfetto Trace',
}


def parse_filename(filename: str) -> dict | None:
    """Parse report filename into components."""
    name, _ext = os.path.splitext(filename)
    # Re-attach extension to suffix for matching
    m = REPORT_PATTERN.match(filename)
    if not m:
        return None
    return {
        'project': m.group('project'),
        'scenario': m.group('scenario'),
        'timestamp': m.group('timestamp'),
        'writer': m.group('writer'),
        'suffix': m.group('suffix'),
        'filename': filename,
    }


def scan_reports(reports_dir: str) -> dict:
    """
    Scan reports directory and group files.
    
    Returns:
        {project: {scenario: {run_key: [file_info, ...]}}}
        where run_key = "YYYYMMDD-HHMMSS-writer"
    """
    grouped = defaultdict(lambda: defaultdict(lambda: defaultdict(list)))
    
    if not os.path.isdir(reports_dir):
        return grouped
    
    for project_dir in sorted(os.listdir(reports_dir)):
        project_path = os.path.join(reports_dir, project_dir)
        if not os.path.isdir(project_path):
            continue
        
        for filename in sorted(os.listdir(project_path)):
            info = parse_filename(filename)
            if info:
                run_key = f"{info['timestamp']}-{info['writer']}"
                grouped[info['project']][info['scenario']][run_key].append(info)
    
    return grouped


def format_timestamp(ts: str) -> str:
    """Format YYYYMMDD-HHMMSS to YYYY-MM-DD HH:MM:SS."""
    try:
        dt = datetime.strptime(ts, "%Y%m%d-%H%M%S")
        return dt.strftime("%Y-%m-%d %H:%M:%S")
    except ValueError:
        return ts


def generate_html(grouped: dict, reports_dir: str) -> str:
    """Generate styled index.html."""
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    
    html = []
    html.append("<!DOCTYPE html>")
    html.append("<html lang='en'>")
    html.append("<head>")
    html.append("<meta charset='UTF-8'>")
    html.append("<meta name='viewport' content='width=device-width, initial-scale=1.0'>")
    html.append("<title>IP Scenario Simulator — Reports</title>")
    html.append(f"<style>{_css()}</style>")
    html.append("</head>")
    html.append("<body>")
    html.append("<div class='container'>")
    html.append("<header>")
    html.append("<h1>📊 IP Scenario Simulator — Reports</h1>")
    html.append(f"<p class='updated'>Last updated: {now}</p>")
    html.append("</header>")
    
    if not grouped:
        html.append("<p class='empty'>No reports found.</p>")
    
    # Navigation
    if grouped:
        html.append("<nav>")
        html.append("<h2>Projects</h2>")
        html.append("<ul>")
        for project in sorted(grouped.keys()):
            scenarios = grouped[project]
            total_runs = sum(len(runs) for runs in scenarios.values())
            html.append(f"<li><a href='#project-{project}'>{project}</a> "
                       f"<span class='badge'>{total_runs} runs</span></li>")
        html.append("</ul>")
        html.append("</nav>")
    
    # Project sections
    for project in sorted(grouped.keys()):
        scenarios = grouped[project]
        html.append(f"<section id='project-{project}'>")
        html.append(f"<h2>🗂 {project}</h2>")
        
        for scenario in sorted(scenarios.keys()):
            runs = scenarios[scenario]
            html.append(f"<h3>{scenario}</h3>")
            html.append("<table>")
            html.append("<thead><tr>")
            html.append("<th>Date/Time</th><th>Writer</th><th>Reports</th>")
            html.append("</tr></thead>")
            html.append("<tbody>")
            
            # Sort by timestamp descending (newest first)
            for run_key in sorted(runs.keys(), reverse=True):
                files = runs[run_key]
                ts = files[0]['timestamp']
                writer = files[0]['writer']
                
                # Build links
                links = []
                for f in sorted(files, key=lambda x: x['suffix']):
                    label = SUFFIX_LABELS.get(f['suffix'], f['suffix'])
                    rel_path = f"reports/{f['project']}/{f['filename']}"
                    links.append(f"<a href='{rel_path}' class='report-link'>{label}</a>")
                
                html.append("<tr>")
                html.append(f"<td class='ts'>{format_timestamp(ts)}</td>")
                html.append(f"<td class='writer'>{writer}</td>")
                html.append(f"<td class='links'>{' '.join(links)}</td>")
                html.append("</tr>")
            
            html.append("</tbody></table>")
        
        html.append("</section>")
    
    html.append("</div>")
    html.append("</body></html>")
    return "\n".join(html)


def _css() -> str:
    return """
:root {
    --bg: #0d1117;
    --surface: #161b22;
    --surface2: #21262d;
    --border: #30363d;
    --text: #c9d1d9;
    --text-muted: #8b949e;
    --accent: #58a6ff;
    --accent2: #3fb950;
    --accent3: #d29922;
    --link: #58a6ff;
    --badge-bg: #1f6feb33;
    --badge-text: #58a6ff;
}
* { margin: 0; padding: 0; box-sizing: border-box; }
body {
    font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Helvetica, Arial, sans-serif;
    background: var(--bg); color: var(--text);
    line-height: 1.6; padding: 20px;
}
.container { max-width: 1200px; margin: 0 auto; }
header {
    background: var(--surface); border: 1px solid var(--border);
    border-radius: 8px; padding: 24px 32px; margin-bottom: 24px;
}
h1 { font-size: 1.5em; color: #f0f6fc; margin-bottom: 4px; }
.updated { color: var(--text-muted); font-size: 0.85em; }
nav {
    background: var(--surface); border: 1px solid var(--border);
    border-radius: 8px; padding: 16px 24px; margin-bottom: 24px;
}
nav h2 { font-size: 1em; color: var(--text-muted); margin-bottom: 8px; text-transform: uppercase; letter-spacing: 0.05em; }
nav ul { list-style: none; display: flex; gap: 16px; flex-wrap: wrap; }
nav a { color: var(--link); text-decoration: none; font-weight: 600; }
nav a:hover { text-decoration: underline; }
.badge {
    background: var(--badge-bg); color: var(--badge-text);
    padding: 2px 8px; border-radius: 12px; font-size: 0.8em;
}
section {
    background: var(--surface); border: 1px solid var(--border);
    border-radius: 8px; padding: 20px 24px; margin-bottom: 20px;
}
h2 { font-size: 1.25em; color: #f0f6fc; margin-bottom: 16px; padding-bottom: 8px; border-bottom: 1px solid var(--border); }
h3 { font-size: 1em; color: var(--accent3); margin: 16px 0 8px; }
table { width: 100%; border-collapse: collapse; }
th {
    background: var(--surface2); color: var(--text-muted);
    text-align: left; padding: 8px 12px; font-size: 0.8em;
    text-transform: uppercase; letter-spacing: 0.05em;
    border-bottom: 1px solid var(--border);
}
td { padding: 10px 12px; border-bottom: 1px solid var(--border); font-size: 0.9em; }
tr:hover td { background: var(--surface2); }
.ts { white-space: nowrap; color: var(--text-muted); font-family: 'SFMono-Regular', Consolas, monospace; font-size: 0.85em; }
.writer { font-weight: 600; color: var(--accent2); }
.links { display: flex; flex-wrap: wrap; gap: 6px; }
.report-link {
    color: var(--link); text-decoration: none;
    background: var(--badge-bg); padding: 3px 10px;
    border-radius: 6px; font-size: 0.8em; white-space: nowrap;
    transition: background 0.2s;
}
.report-link:hover { background: #1f6feb55; text-decoration: none; }
.empty { color: var(--text-muted); font-style: italic; padding: 40px; text-align: center; }
@media (max-width: 768px) {
    .links { flex-direction: column; }
    nav ul { flex-direction: column; gap: 8px; }
}
"""


def main():
    parser = argparse.ArgumentParser(description='Generate index.html for GitHub Pages reports')
    parser.add_argument('--reports-dir', default='docs/reports',
                       help='Directory containing report files (default: docs/reports)')
    parser.add_argument('--output', default='docs/index.html',
                       help='Output index.html path (default: docs/index.html)')
    args = parser.parse_args()
    
    grouped = scan_reports(args.reports_dir)
    html = generate_html(grouped, args.reports_dir)
    
    os.makedirs(os.path.dirname(args.output) or '.', exist_ok=True)
    with open(args.output, 'w', encoding='utf-8') as f:
        f.write(html)
    
    # Stats
    total_projects = len(grouped)
    total_runs = sum(
        len(runs)
        for scenarios in grouped.values()
        for runs in scenarios.values()
    )
    print(f"Index generated: {args.output}")
    print(f"  Projects: {total_projects}, Runs: {total_runs}")


if __name__ == '__main__':
    main()
