#!/usr/bin/env python3
# Makes the graphs for the report from eval/results/<mode>/ folders.
#
#   latency_cdf.png       RTT CDF of the L4S probe and the Classic probe, one line per mode
#   throughput.png        Classic vs L4S flow throughput over time, one small chart per mode
#   dualq_internals.png   Our router's queue delays and PI2 probabilities over time
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

MODES = ['fifo', 'dualq', 'fq_codel', 'dualpi2']
LABELS = {'fifo': 'FIFO (ours, 1 queue)', 'dualq': 'DualQ (ours)',
          'fq_codel': 'fq_codel (kernel)', 'dualpi2': 'dualpi2 (kernel)'}
# One fixed color + line style per mode, the same in every graph
COLORS = {'fifo': '#2a78d6', 'dualq': '#eb6834', 'fq_codel': '#1baf7a', 'dualpi2': '#eda100'}
STYLES = {'fifo': '-', 'dualq': '-', 'fq_codel': '--', 'dualpi2': ':'}
FLOW_COLORS = {'classic': '#2a78d6', 'l4s': '#eb6834'}

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
            values.append(float(row[column]))
    return values


def plot_latency_cdf(base, modes):
    fig, axes = plt.subplots(1, 2, figsize=(11, 4), sharey=True)
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
    fig.legend(handles, labels, loc='upper center', ncol=len(labels), bbox_to_anchor=(0.5, 0.92))
    fig.suptitle('Latency under load: 1 Classic + 1 L4S bulk flow, 50 Mbps bottleneck, 20 ms base RTT')
    fig.tight_layout(rect=(0, 0, 1, 0.86))
    path = os.path.join(base, 'latency_cdf.png')
    fig.savefig(path, dpi=150)
    print('Saved', path)


def read_iperf_intervals(path):
    # Returns (times, mbps) from an iperf3 JSON file
    times, mbps = [], []
    if not os.path.exists(path):
        return times, mbps
    try:
        data = json.load(open(path))
        for interval in data['intervals']:
            times.append(interval['sum']['end'])
            mbps.append(interval['sum']['bits_per_second'] / 1e6)
    except (ValueError, KeyError):
        pass
    return times, mbps


def plot_throughput(base, modes):
    fig, axes = plt.subplots(1, len(modes), figsize=(3.2 * len(modes), 3.2), sharey=True, squeeze=False)
    for ax, mode in zip(axes[0], modes):
        for flow, label in [('classic', 'Classic (cubic)'), ('l4s', 'L4S (DCTCP)')]:
            t, mbps = read_iperf_intervals(os.path.join(base, mode, 'iperf_%s.json' % flow))
            if t:
                ax.plot(t, mbps, color=FLOW_COLORS[flow], linewidth=2, label=label)
        ax.set_title(LABELS[mode])
        ax.set_xlabel('Time (s)')
        ax.set_ylim(0, 55)  # link is 50 Mbps
    axes[0][0].set_ylabel('Throughput (Mbps)')
    handles, labels = axes[0][0].get_legend_handles_labels()
    fig.legend(handles, labels, loc='upper center', ncol=2)
    fig.tight_layout(rect=(0, 0, 1, 0.88))
    path = os.path.join(base, 'throughput.png')
    fig.savefig(path, dpi=150)
    print('Saved', path)


def plot_dualq_internals(base):
    path = os.path.join(base, 'dualq', 'router_stats.csv')
    t = read_csv_column(path, 'time_s')
    if not t:
        return
    fig, axes = plt.subplots(2, 1, figsize=(9, 5.5), sharex=True)

    axes[0].plot(t, read_csv_column(path, 'c_delay_ms'), color=FLOW_COLORS['classic'], linewidth=1.5, label='Classic queue')
    axes[0].plot(t, read_csv_column(path, 'l_delay_ms'), color=FLOW_COLORS['l4s'], linewidth=1.5, label='L4S queue')
    axes[0].axhline(15, color='#777777', linewidth=1, linestyle='--', label='Classic target (15 ms)')
    axes[0].set_ylabel('Queue delay (ms)')
    axes[0].set_ylim(bottom=0)
    axes[0].legend(loc='lower left', bbox_to_anchor=(0, 1.0), ncol=3)
    fig.suptitle('Inside our DualQ router')

    axes[1].plot(t, read_csv_column(path, 'p_prime'), color='#555555', linewidth=1.5, label="p' (base)")
    axes[1].plot(t, read_csv_column(path, 'p_c'), color=FLOW_COLORS['classic'], linewidth=1.5, label="p_C = p'^2 (Classic drop)")
    axes[1].plot(t, read_csv_column(path, 'p_l'), color=FLOW_COLORS['l4s'], linewidth=1.5, label="p_CL = k*p' (L4S mark)")
    axes[1].set_ylabel('Probability')
    axes[1].set_xlabel('Time (s)')
    axes[1].set_ylim(0, 1)
    axes[1].legend(loc='lower left', bbox_to_anchor=(0, 1.0), ncol=3)

    fig.tight_layout()
    out = os.path.join(base, 'dualq_internals.png')
    fig.savefig(out, dpi=150)
    print('Saved', out)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--input', default='eval/results')
    args = parser.parse_args()

    modes = [m for m in MODES if os.path.isdir(os.path.join(args.input, m))]
    if not modes:
        print('No result folders found in', args.input)
        return

    plot_latency_cdf(args.input, modes)
    plot_throughput(args.input, modes)
    plot_dualq_internals(args.input)


if __name__ == '__main__':
    main()
