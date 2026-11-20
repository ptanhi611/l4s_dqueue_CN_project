#!/usr/bin/env python3
# Makes the graphs for the report from eval/results/<mode>/ folders
# (or from an archived run: --input experiments/<run>).
#
#   latency_cdf.png          RTT CDF of the L4S probe and the Classic probe, one line per mode
#   latency_timeseries.png   L4S probe RTT over time, one small chart per mode (shows the attack)
#   throughput.png           Classic / L4S / flood throughput over time, one small chart per mode
#   internals_<mode>.png     Our router's queue delays and PI2 probabilities over time
#
# Usage: python3 eval/plot_results.py --input eval/results
# Needs only matplotlib.

import argparse
import csv
import json
import os

import matplotlib
matplotlib.use('Agg')  # no screen needed, just save PNG files
import matplotlib.pyplot as plt
from matplotlib.ticker import ScalarFormatter

MODES = ['fifo', 'dualq_basic', 'dualq', 'dualq_qp', 'dualq_qp_police', 'fq_codel', 'dualpi2']
OUR_DUALQ_MODES = ['dualq_basic', 'dualq', 'dualq_qp', 'dualq_qp_police']
LABELS = {'fifo': 'FIFO (ours)', 'dualq_basic': 'DualQ basic (ours)', 'dualq': 'DualQ + overload (ours)',
          'dualq_qp': 'DualQ + queue protection (ours)', 'dualq_qp_police': 'DualQ + QP + rate limit (ours)',
          'fq_codel': 'fq_codel (kernel)',
          'dualpi2': 'dualpi2 (kernel)'}
# One fixed color + line style per mode, the same in every graph
COLORS = {'fifo': '#2a78d6', 'dualq': '#eb6834', 'fq_codel': '#1baf7a', 'dualpi2': '#eda100',
          'dualq_qp': '#e87ba4', 'dualq_basic': '#008300', 'dualq_qp_police': '#6250d6'}
STYLES = {'fifo': '-', 'dualq_basic': '-.', 'dualq': '-', 'dualq_qp': '-', 'dualq_qp_police': '-',
          'fq_codel': '--', 'dualpi2': ':'}
FLOW_COLORS = {'classic': '#2a78d6', 'l4s': '#eb6834', 'flood': '#1baf7a'}
ATTACK_SHADE = '#ececec'

plt.rcParams.update({
    'font.size': 10,
    'axes.spines.top': False,
    'axes.spines.right': False,
    'axes.grid': True,
    'grid.color': '#e5e5e5',
    'grid.linewidth': 0.8,
    'axes.edgecolor': '#999999',
    'legend.frameon': False,
})


def read_csv_column(path, column):
    values = []
    if not os.path.exists(path):
        return values
    with open(path) as f:
        for row in csv.DictReader(f):
            if column in row and row[column] != '':
                values.append(float(row[column]))
    return values


def read_run_info(folder):
    info = {}
    path = os.path.join(folder, 'run_info.txt')
    if os.path.exists(path):
        for line in open(path):
            if '=' in line:
                key, value = line.strip().split('=', 1)
                info[key] = value
    return info


def shade_attack(ax, folder):
    # Light grey band over the time the flood was running
    info = read_run_info(folder)
    if info.get('scenario') == 'attack':
        ax.axvspan(float(info['attack_start']), float(info['attack_end']), color=ATTACK_SHADE, zorder=0,
                   label='attack (ECT(1) flood)')


def save(fig, base, name):
    path = os.path.join(base, name)
    fig.savefig(path, dpi=150)
    plt.close(fig)
    print('Saved', path)


def plot_latency_cdf(base, modes):
    fig, axes = plt.subplots(1, 2, figsize=(11, 4.2), sharey=True)
    for ax, probe, title in [(axes[0], 'l4s', 'L4S probe (ECT(1))'), (axes[1], 'classic', 'Classic probe (Not-ECT)')]:
        for mode in modes:
            rtts = sorted(read_csv_column(os.path.join(base, mode, 'probe_%s.csv' % probe), 'rtt_ms'))
            if not rtts:
                continue
            cdf = [(i + 1) / len(rtts) for i in range(len(rtts))]
            ax.plot(rtts, cdf, color=COLORS[mode], linestyle=STYLES[mode], linewidth=2, label=LABELS[mode])
        ax.set_xscale('log')
        ax.set_xticks([10, 20, 30, 50, 100, 200, 300, 500])
        ax.xaxis.set_major_formatter(ScalarFormatter())
        ax.xaxis.set_minor_formatter(plt.NullFormatter())
        ax.set_title(title)
        ax.set_xlabel('Round-trip time (ms, log scale)')
    axes[0].set_ylabel('Fraction of probes')
    handles, labels = axes[0].get_legend_handles_labels()
    fig.legend(handles, labels, loc='upper center', ncol=3, bbox_to_anchor=(0.5, 0.94))
    fig.suptitle('Probe latency over the whole run (50 Mbps bottleneck, 20 ms base RTT)')
    fig.tight_layout(rect=(0, 0, 1, 0.83))
    save(fig, base, 'latency_cdf.png')


def plot_latency_timeseries(base, modes):
    fig, axes = plt.subplots(1, len(modes), figsize=(3.2 * len(modes), 3.4), sharey=True, squeeze=False)
    for ax, mode in zip(axes[0], modes):
        folder = os.path.join(base, mode)
        shade_attack(ax, folder)
        path = os.path.join(folder, 'probe_l4s.csv')
        seq = read_csv_column(path, 'seq')
        rtt = read_csv_column(path, 'rtt_ms')
        if seq:
            ax.plot([s * 0.01 for s in seq], rtt, color=COLORS[mode], linewidth=1)
        ax.set_yscale('log')
        ax.set_yticks([20, 30, 50, 100, 200, 400])
        ax.yaxis.set_major_formatter(ScalarFormatter())
        ax.yaxis.set_minor_formatter(plt.NullFormatter())
        ax.set_title(LABELS[mode], fontsize=9)
        ax.set_xlabel('Time (s)')
    axes[0][0].set_ylabel('L4S probe RTT (ms, log)')
    handles, labels = axes[0][0].get_legend_handles_labels()
    if handles:
        fig.legend(handles, labels, loc='upper center')
    fig.tight_layout(rect=(0, 0, 1, 0.88))
    save(fig, base, 'latency_timeseries.png')


def read_iperf_intervals(path, offset=0.0):
    # Returns (times, mbps) from an iperf3 JSON file
    times, mbps = [], []
    if not os.path.exists(path):
        return times, mbps
    try:
        data = json.load(open(path))
        for interval in data['intervals']:
            times.append(interval['sum']['end'] + offset)
            mbps.append(interval['sum']['bits_per_second'] / 1e6)
    except (ValueError, KeyError):
        pass
    return times, mbps


def plot_throughput(base, modes):
    fig, axes = plt.subplots(1, len(modes), figsize=(3.2 * len(modes), 3.4), sharey=True, squeeze=False)
    for ax, mode in zip(axes[0], modes):
        folder = os.path.join(base, mode)
        shade_attack(ax, folder)
        info = read_run_info(folder)
        flows = [('classic', 'Classic (cubic)', 0.0), ('l4s', 'L4S (DCTCP)', 0.0)]
        if info.get('scenario') == 'attack':
            flows.append(('flood', 'Flood (UDP, ECT(1))', float(info['attack_start'])))
        for flow, label, offset in flows:
            t, mbps = read_iperf_intervals(os.path.join(folder, 'iperf_%s.json' % flow), offset)
            if t:
                ax.plot(t, mbps, color=FLOW_COLORS[flow], linewidth=2, label=label)
        ax.set_title(LABELS[mode], fontsize=9)
        ax.set_xlabel('Time (s)')
        ax.set_ylim(0, 55)  # link is 50 Mbps
    axes[0][0].set_ylabel('Throughput (Mbps)')
    # Collect legend entries from all panels (the flood only exists in attack runs)
    handles, labels = [], []
    for ax in axes[0]:
        for h, l in zip(*ax.get_legend_handles_labels()):
            if l not in labels:
                handles.append(h)
                labels.append(l)
    fig.legend(handles, labels, loc='upper center', ncol=len(labels))
    fig.tight_layout(rect=(0, 0, 1, 0.88))
    save(fig, base, 'throughput.png')


def plot_internals(base, mode):
    folder = os.path.join(base, mode)
    path = os.path.join(folder, 'router_stats.csv')
    # router_stats.csv time starts when the router starts; shift it so 0 = when the flows start
    offset = float(read_run_info(folder).get('flows_start', 0))
    t = [x - offset for x in read_csv_column(path, 'time_s')]
    if not t:
        return
    fig, axes = plt.subplots(2, 1, figsize=(9, 5.5), sharex=True)

    for ax in axes:
        shade_attack(ax, folder)
    axes[0].plot(t, read_csv_column(path, 'c_delay_ms'), color=FLOW_COLORS['classic'], linewidth=1.5, label='Classic queue')
    axes[0].plot(t, read_csv_column(path, 'l_delay_ms'), color=FLOW_COLORS['l4s'], linewidth=1.5, label='L4S queue')
    axes[0].axhline(15, color='#777777', linewidth=1, linestyle='--', label='Classic target (15 ms)')
    axes[0].set_ylabel('Queue delay (ms)')
    axes[0].set_ylim(bottom=0)
    axes[0].legend(loc='lower left', bbox_to_anchor=(0, 1.0), ncol=4)

    axes[1].plot(t, read_csv_column(path, 'p_prime'), color='#555555', linewidth=1.5, label="p' (base)")
    axes[1].plot(t, read_csv_column(path, 'p_c'), color=FLOW_COLORS['classic'], linewidth=1.5, label="p_C = p'^2 (Classic drop)")
    axes[1].plot(t, read_csv_column(path, 'p_l'), color=FLOW_COLORS['l4s'], linewidth=1.5, label="p_CL = k*p' (L4S mark)")
    axes[1].set_ylabel('Probability')
    axes[1].set_xlabel('Time since flows started (s)')
    axes[1].set_ylim(0, 1.05)
    axes[1].legend(loc='lower left', bbox_to_anchor=(0, 1.0), ncol=4)

    fig.suptitle('Inside our router: ' + LABELS[mode])
    fig.tight_layout()
    save(fig, base, 'internals_%s.png' % mode)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--input', default='eval/results')
    args = parser.parse_args()

    modes = [m for m in MODES if os.path.isdir(os.path.join(args.input, m))]
    if not modes:
        print('No result folders found in', args.input)
        return

    plot_latency_cdf(args.input, modes)
    plot_latency_timeseries(args.input, modes)
    plot_throughput(args.input, modes)
    for mode in modes:
        if mode in OUR_DUALQ_MODES:
            plot_internals(args.input, mode)


if __name__ == '__main__':
    main()
