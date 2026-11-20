#!/usr/bin/env python3
# Builds results/final/ for the report from the FINAL runs in experiments/:
#   experiments/*_final_normal_r1, _r2, ...   (normal scenario, repeated)
#   experiments/*_final_attack_r1, _r2, ...   (attack scenario, repeated)
#   experiments/*_final_breaker_off           (attack, starvation breaker switched off)
#
# Output in results/final/:
#   table_normal.md / .csv     mean +- std over the repeats, per mode
#   table_attack.md / .csv     the same, measured during the attack
#   table_breaker.md           breaker ON (final_attack runs) vs OFF
#   fig_normal.png             probe p99 RTT and throughput split, with error bars
#   fig_bulk_rtt.png           RTT seen by the bulk TCP flows themselves
#   fig_attack.png             L4S probe p99 and throughputs during the attack
#   run1_*.png                 the per-run graphs of repeat 1 (time series, CDFs, internals)
#   README.md                  what every file is and where it comes from
#
# Usage: python3 eval/make_final.py   (from the repo root, after eval/run_final.sh)

import csv
import glob
import math
import os
import shutil

import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
from matplotlib.ticker import ScalarFormatter

# Same folder as this script, so these imports work when run as python3 eval/make_final.py
from parse_metrics import (read_probe, read_iperf, read_iperf_rtt, read_queue_delays,
                           attack_metrics, ecn_check, percentile)

OUT = 'results/final'
NORMAL_MODES = ['fifo', 'dualq', 'dualq_qp', 'dualq_qp_police', 'fq_codel', 'dualpi2']
ATTACK_MODES = ['dualq_basic', 'dualq', 'dualq_qp', 'dualq_qp_police', 'fq_codel', 'dualpi2']
LABELS = {'fifo': 'FIFO', 'dualq_basic': 'DualQ basic', 'dualq': 'DualQ',
          'dualq_qp': 'DualQ+QP', 'dualq_qp_police': 'DualQ+QP+limit',
          'fq_codel': 'fq_codel\n(kernel)', 'dualpi2': 'dualpi2\n(kernel)'}
COLORS = {'classic': '#2a78d6', 'l4s': '#eb6834', 'flood': '#1baf7a'}

plt.rcParams.update({
    'font.size': 10, 'axes.spines.top': False, 'axes.spines.right': False,
    'axes.grid': True, 'axes.axisbelow': True, 'grid.color': '#e5e5e5', 'grid.linewidth': 0.8,
    'axes.edgecolor': '#999999', 'legend.frameon': False,
})


# ---------------- reading the runs ----------------

def find_runs(pattern):
    # experiments/2026-11-21_0105_final_normal_r1 -> sorted list of folders
    return sorted(glob.glob('experiments/*_' + pattern))


def probe_max(folder, name):
    rtts = []
    path = os.path.join(folder, 'probe_%s.csv' % name)
    if os.path.exists(path):
        with open(path) as f:
            for row in csv.DictReader(f):
                rtts.append(float(row['rtt_ms']))
    return max(rtts) if rtts else float('nan')


def dctcp_sanctions(folder):
    # How many times queue protection wrongly sanctioned the good L4S (DCTCP) flow
    path = os.path.join(folder, 'router_log.txt')
    if not os.path.exists(path):
        return float('nan')
    count = 0
    for line in open(path):
        if 'sanctioned' in line and '10.0.2.2:5202' in line:
            count += int(line.split('sanctioned')[1].split('times')[0])
    return count


def normal_metrics(folder, mode):
    l4s = read_probe(folder, 'l4s')
    classic = read_probe(folder, 'classic')
    l_q, c_q = read_queue_delays(folder, mode)
    (c_rtt, _), (l_rtt, _) = read_iperf_rtt(folder, 'classic'), read_iperf_rtt(folder, 'l4s')
    c_tput, l_tput = read_iperf(folder, 'classic'), read_iperf(folder, 'l4s')
    return {
        'l4s_probe_p50': l4s[0], 'l4s_probe_p99': l4s[2], 'l4s_probe_max': probe_max(folder, 'l4s'),
        'classic_probe_p50': classic[0], 'classic_probe_p99': classic[2],
        'l4s_qdelay_mean': sum(l_q) / len(l_q) if l_q else float('nan'),
        'l4s_qdelay_p99': percentile(sorted(l_q), 99) if l_q else float('nan'),
        'classic_qdelay_mean': sum(c_q) / len(c_q) if c_q else float('nan'),
        'classic_tput': c_tput, 'l4s_tput': l_tput, 'total_tput': c_tput + l_tput,
        'classic_flow_rtt': c_rtt, 'l4s_flow_rtt': l_rtt,
        'ecn_ok': 1.0 if ecn_check(folder) == 'yes' else 0.0,
    }


def attack_metrics_flat(folder, mode):
    a = attack_metrics(folder)
    if not a:
        return None
    return {
        'l4s_probe_p50': a['l4s_probe'][0], 'l4s_probe_p99': a['l4s_probe'][1],
        'l4s_probe_max': probe_max(folder, 'l4s'),
        'classic_probe_p50': a['classic_probe'][0], 'classic_probe_p99': a['classic_probe'][1],
        'classic_tput': a['classic_tput'], 'l4s_tput': a['l4s_tput'], 'flood_tput': a['flood_tput'],
        'dctcp_sanctions': dctcp_sanctions(folder) if mode.startswith('dualq_qp') else float('nan'),
        'ecn_ok': 1.0 if ecn_check(folder) == 'yes' else 0.0,
    }


def collect(runs, modes, metric_fn):
    # Returns {mode: {metric: [value from each run]}}
    data = {}
    for run in runs:
        for mode in modes:
            folder = os.path.join(run, mode)
            if not os.path.isdir(folder):
                continue
            values = metric_fn(folder, mode)
            if values is None:
                continue
            data.setdefault(mode, {})
            for key, value in values.items():
                data[mode].setdefault(key, []).append(value)
    return data


def mean_std(values):
    values = [v for v in values if not math.isnan(v)]
    if not values:
        return float('nan'), float('nan')
    m = sum(values) / len(values)
    if len(values) < 2:
        return m, 0.0
    var = sum((v - m) ** 2 for v in values) / (len(values) - 1)
    return m, math.sqrt(var)


# ---------------- tables ----------------

def fmt(values, digits=1):
    m, s = mean_std(values)
    if math.isnan(m):
        return 'n/a'
    return '%.*f ± %.*f' % (digits, m, digits, s)


def write_table(name, title, data, modes, columns, n_runs, note):
    # columns: list of (metric key, column header, digits)
    md = ['## ' + title, '', 'Mean ± standard deviation over %d runs. %s' % (n_runs, note), '']
    md.append('| Mode | ' + ' | '.join(c[1] for c in columns) + ' |')
    md.append('|---|' + '---|' * len(columns))
    rows = []
    for mode in modes:
        if mode not in data:
            continue
        cells = [fmt(data[mode].get(c[0], []), c[2]) for c in columns]
        md.append('| %s | %s |' % (LABELS[mode].replace('\n', ' '), ' | '.join(cells)))
        rows.append([mode] + [x for c in columns for x in mean_std(data[mode].get(c[0], []))])
    with open(os.path.join(OUT, name + '.md'), 'w') as f:
        f.write('\n'.join(md) + '\n')
    with open(os.path.join(OUT, name + '.csv'), 'w', newline='') as f:
        writer = csv.writer(f)
        writer.writerow(['mode'] + [x for c in columns for x in (c[0] + '_mean', c[0] + '_std')])
        for r in rows:
            writer.writerow([r[0]] + ['%.3f' % v for v in r[1:]])
    print('\n'.join(md) + '\n')


# ---------------- figures ----------------

def grouped_bars(ax, data, modes, series, ylabel, log=False):
    # series: list of (metric key, label, color)
    modes = [m for m in modes if m in data]
    width = 0.8 / len(series)
    for i, (key, label, color) in enumerate(series):
        means, stds = [], []
        for mode in modes:
            m, s = mean_std(data[mode].get(key, []))
            means.append(0 if math.isnan(m) else m)
            stds.append(0 if math.isnan(s) else s)
        x = [j + (i - (len(series) - 1) / 2) * width for j in range(len(modes))]
        ax.bar(x, means, width * 0.9, yerr=stds, color=color, label=label,
               error_kw={'elinewidth': 1, 'capsize': 3, 'ecolor': '#333333'})
    ax.set_xticks(range(len(modes)))
    ax.set_xticklabels([LABELS[m] for m in modes], fontsize=9)
    ax.set_ylabel(ylabel)
    if log:
        ax.set_yscale('log')
        ax.yaxis.set_major_formatter(ScalarFormatter())
        ax.yaxis.set_minor_formatter(plt.NullFormatter())
    # Legend below the panel, so it never covers a bar
    ax.legend(loc='upper center', bbox_to_anchor=(0.5, -0.16), ncol=len(series), fontsize=9)


def fig_normal(data, n_runs):
    fig, axes = plt.subplots(1, 2, figsize=(12, 4.8))
    grouped_bars(axes[0], data, NORMAL_MODES,
                 [('l4s_probe_p99', 'L4S probe', COLORS['l4s']), ('classic_probe_p99', 'Classic probe', COLORS['classic'])],
                 'Probe RTT, 99th percentile (ms, log)', log=True)
    axes[0].set_yticks([20, 30, 50, 100, 200, 300])
    axes[0].set_title('Latency (base RTT 20 ms)')
    grouped_bars(axes[1], data, NORMAL_MODES,
                 [('l4s_tput', 'L4S flow (DCTCP)', COLORS['l4s']), ('classic_tput', 'Classic flow (cubic)', COLORS['classic'])],
                 'Throughput (Mbps)')
    axes[1].set_title('Throughput split (link 50 Mbps)')
    fig.suptitle('Normal scenario: mean ± std over %d runs' % n_runs)
    fig.tight_layout()
    fig.savefig(os.path.join(OUT, 'fig_normal.png'), dpi=150)
    plt.close(fig)


def fig_bulk_rtt(data, n_runs):
    fig, ax = plt.subplots(figsize=(8, 4.6))
    grouped_bars(ax, data, NORMAL_MODES,
                 [('l4s_flow_rtt', 'L4S flow (DCTCP)', COLORS['l4s']), ('classic_flow_rtt', 'Classic flow (cubic)', COLORS['classic'])],
                 'Mean RTT seen by the flow (ms)')
    ax.axhline(20, color='#777777', linewidth=1, linestyle='--', label='base RTT (20 ms)')
    ax.legend(loc='upper center', bbox_to_anchor=(0.5, -0.16), ncol=3, fontsize=9)
    ax.set_title('RTT of the bulk TCP flows themselves (iperf3), mean ± std over %d runs' % n_runs)
    fig.tight_layout()
    fig.savefig(os.path.join(OUT, 'fig_bulk_rtt.png'), dpi=150)
    plt.close(fig)


def fig_attack(data, n_runs):
    fig, axes = plt.subplots(1, 2, figsize=(12, 4.8))
    grouped_bars(axes[0], data, ATTACK_MODES, [('l4s_probe_p99', 'L4S probe', COLORS['l4s'])],
                 'L4S probe RTT p99 during attack (ms, log)', log=True)
    axes[0].set_yticks([20, 30, 50, 100, 200, 300])
    axes[0].set_title('L4S latency during the ECT(1) flood')
    grouped_bars(axes[1], data, ATTACK_MODES,
                 [('l4s_tput', 'L4S flow (DCTCP)', COLORS['l4s']), ('classic_tput', 'Classic flow (cubic)', COLORS['classic']),
                  ('flood_tput', 'Flood (UDP)', COLORS['flood'])],
                 'Throughput during attack (Mbps)')
    axes[1].set_title('Who gets the link during the attack')
    fig.suptitle('Attack scenario (60 Mbps ECT(1) UDP flood): mean ± std over %d runs' % n_runs)
    fig.tight_layout()
    fig.savefig(os.path.join(OUT, 'fig_attack.png'), dpi=150)
    plt.close(fig)


def copy_run1_figures(normal_runs, attack_runs):
    copied = []
    pairs = []
    if normal_runs:
        r = normal_runs[0]
        pairs += [(r, 'latency_cdf.png', 'run1_normal_latency_cdf.png'),
                  (r, 'throughput.png', 'run1_normal_throughput.png'),
                  (r, 'internals_dualq.png', 'run1_normal_internals_dualq.png')]
    if attack_runs:
        r = attack_runs[0]
        pairs += [(r, 'latency_timeseries.png', 'run1_attack_latency_timeseries.png'),
                  (r, 'throughput.png', 'run1_attack_throughput.png'),
                  (r, 'internals_dualq_qp.png', 'run1_attack_internals_dualq_qp.png'),
                  (r, 'internals_dualq_qp_police.png', 'run1_attack_internals_dualq_qp_police.png')]
    for run, src, dst in pairs:
        path = os.path.join(run, src)
        if os.path.exists(path):
            shutil.copy(path, os.path.join(OUT, dst))
            copied.append((dst, run))
    return copied


# ---------------- README ----------------

def write_readme(normal_runs, attack_runs, breaker_runs, copied):
    lines = ['# Final results', '',
             'Generated by `eval/make_final.py` from the runs of `eval/run_final.sh`.',
             'Raw data of every run (CSV/JSON, console output, config) is in the listed `experiments/` folders.', '',
             '## Runs used', '']
    for title, runs in [('Normal scenario', normal_runs), ('Attack scenario', attack_runs),
                        ('Breaker off', breaker_runs)]:
        lines.append('**%s:** ' % title + (', '.join('`%s`' % r for r in runs) if runs else 'none'))
        lines.append('')
    lines += ['## Setup', '',
              '- Mininet: h1 - r0 - h2, 50 Mbps bottleneck inside r0, 20 ms base RTT, 1.5 MB buffer (our router).',
              '- Normal: 1 Classic flow (cubic, Not-ECT) + 1 L4S flow (DCTCP with ECN, ECT(1)) + 2 UDP latency probes '
              '(100 B every 10 ms; one ECT(1), one Not-ECT), 30 s.',
              '- Attack: normal + 60 Mbps unresponsive ECT(1) UDP flood from t = 10 s to 30 s of a 40 s run. '
              'Attack numbers are measured from 2 s after the flood starts until it ends.',
              '- Modes: FIFO, DualQ basic (no overload handling), DualQ (RFC 9332 incl. overload), '
              'DualQ+QP (our queue protection), DualQ+QP+limit (queue protection + 5 Mbps limit for sanctioned flows), '
              'Linux fq_codel, Linux sch_dualpi2.', '',
              '## Files', '',
              '| File | What it shows |', '|---|---|',
              '| `table_normal.md/.csv` | Normal scenario: probe RTTs, queue delays, throughput, bulk-flow RTT |',
              '| `table_attack.md/.csv` | Attack scenario, during the flood: probe RTTs, throughputs, wrong sanctions of the DCTCP flow |',
              '| `table_breaker.md` | Starvation breaker on vs off (attack scenario) |',
              '| `fig_normal.png` | Probe p99 RTT and throughput split per mode (error bars = std over runs) |',
              '| `fig_bulk_rtt.png` | RTT seen by the bulk Classic and L4S TCP flows |',
              '| `fig_attack.png` | L4S probe p99 and throughput of each flow during the attack |']
    for dst, run in copied:
        lines.append('| `%s` | copy of a per-run graph from `%s` |' % (dst, run))
    lines += ['', 'Column notes: probe RTT includes the 20 ms base RTT; queue delay is sampled every 100 ms inside the '
              'bottleneck (our router and kernel dualpi2 only); "bulk flow RTT" is the mean RTT measured by the '
              'sending TCP stack (iperf3); "DCTCP sanctions" counts how often queue protection wrongly sanctioned '
              'the well-behaved L4S flow.']
    with open(os.path.join(OUT, 'README.md'), 'w') as f:
        f.write('\n'.join(lines) + '\n')


# ---------------- main ----------------

def main():
    os.makedirs(OUT, exist_ok=True)
    normal_runs = find_runs('final_normal_r*')
    attack_runs = find_runs('final_attack_r*')
    breaker_runs = find_runs('final_breaker_off')
    print('normal runs:', normal_runs)
    print('attack runs:', attack_runs)
    print('breaker-off runs:', breaker_runs)
    if not normal_runs and not attack_runs:
        print('No final runs found. Run: sudo ./eval/run_final.sh')
        return

    if normal_runs:
        normal = collect(normal_runs, NORMAL_MODES, normal_metrics)
        write_table('table_normal', 'Normal scenario', normal, NORMAL_MODES, [
            ('l4s_probe_p50', 'L4S probe p50 (ms)', 1), ('l4s_probe_p99', 'L4S probe p99 (ms)', 1),
            ('classic_probe_p50', 'Classic probe p50 (ms)', 1), ('classic_probe_p99', 'Classic probe p99 (ms)', 1),
            ('l4s_qdelay_mean', 'L4S queue mean (ms)', 2), ('l4s_qdelay_p99', 'L4S queue p99 (ms)', 2),
            ('classic_qdelay_mean', 'Classic queue mean (ms)', 1),
            ('classic_tput', 'Classic tput (Mbps)', 1), ('l4s_tput', 'L4S tput (Mbps)', 1),
            ('classic_flow_rtt', 'Classic flow RTT (ms)', 1), ('l4s_flow_rtt', 'L4S flow RTT (ms)', 1),
            ('ecn_ok', 'DCTCP+ECN ok', 2)],
            len(normal_runs), 'Probe and flow RTTs include the 20 ms base RTT. FIFO has one queue (shown as Classic).')
        fig_normal(normal, len(normal_runs))
        fig_bulk_rtt(normal, len(normal_runs))

    if attack_runs:
        attack = collect(attack_runs, ATTACK_MODES, attack_metrics_flat)
        write_table('table_attack', 'Attack scenario (during the flood)', attack, ATTACK_MODES, [
            ('l4s_probe_p50', 'L4S probe p50 (ms)', 1), ('l4s_probe_p99', 'L4S probe p99 (ms)', 1),
            ('classic_probe_p50', 'Classic probe p50 (ms)', 1), ('classic_probe_p99', 'Classic probe p99 (ms)', 1),
            ('classic_tput', 'Classic tput (Mbps)', 1), ('l4s_tput', 'L4S tput (Mbps)', 1),
            ('flood_tput', 'Flood tput (Mbps)', 1), ('dctcp_sanctions', 'DCTCP sanctions', 1),
            ('ecn_ok', 'DCTCP+ECN ok', 2)],
            len(attack_runs), 'Measured from 2 s after the flood starts until it stops.')
        fig_attack(attack, len(attack_runs))

        if breaker_runs:
            modes = ['dualq', 'dualq_qp_police']
            off = collect(breaker_runs, modes, attack_metrics_flat)
            md = ['## Starvation breaker: on (50 ms) vs off', '',
                  'ON = mean ± std of the %d final attack runs; OFF = %d run(s) with '
                  '`config/router_config_breaker_off.ini`. "Max" = worst L4S probe RTT over the whole run '
                  '(the spike at attack onset).' % (len(attack_runs), len(breaker_runs)), '',
                  '| Mode | Breaker | L4S probe p99 during attack (ms) | L4S probe max, whole run (ms) | '
                  'L4S tput (Mbps) | Classic tput (Mbps) | DCTCP sanctions |',
                  '|---|---|---|---|---|---|---|']
            for mode in modes:
                for label, d in [('on', attack), ('off', off)]:
                    if mode in d:
                        v = d[mode]
                        md.append('| %s | %s | %s | %s | %s | %s | %s |' % (
                            LABELS[mode], label, fmt(v['l4s_probe_p99']), fmt(v['l4s_probe_max']),
                            fmt(v['l4s_tput']), fmt(v['classic_tput']), fmt(v['dctcp_sanctions'])))
            with open(os.path.join(OUT, 'table_breaker.md'), 'w') as f:
                f.write('\n'.join(md) + '\n')
            print('\n'.join(md) + '\n')

    copied = copy_run1_figures(normal_runs, attack_runs)
    write_readme(normal_runs, attack_runs, breaker_runs, copied)
    print('Saved everything in', OUT)


if __name__ == '__main__':
    main()
