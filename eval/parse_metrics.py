#!/usr/bin/env python3
# Reads eval/results/<mode>/ folders and prints one summary table.
# Also writes eval/results/summary.csv (used for the report).
#
# Usage: python3 eval/parse_metrics.py --input eval/results
# Uses only the Python standard library.

import argparse
import csv
import json
import os
import re

MODES = ['fifo', 'dualq', 'fq_codel', 'dualpi2']


def percentile(sorted_values, p):
    if not sorted_values:
        return float('nan')
    index = int(p / 100.0 * (len(sorted_values) - 1))
    return sorted_values[index]


def read_probe(folder, name):
    # Returns (p50, p95, p99, loss_percent) for probe_<name>.csv
    rtts = []
    path = os.path.join(folder, 'probe_%s.csv' % name)
    if os.path.exists(path):
        with open(path) as f:
            for row in csv.DictReader(f):
                rtts.append(float(row['rtt_ms']))
    rtts.sort()

    # The probe prints "sent X, received Y, lost Z" into probe_<name>.txt
    loss = float('nan')
    txt = os.path.join(folder, 'probe_%s.txt' % name)
    if os.path.exists(txt):
        match = re.search(r'sent (\d+), received (\d+)', open(txt).read())
        if match and int(match.group(1)) > 0:
            sent, received = int(match.group(1)), int(match.group(2))
            loss = 100.0 * (sent - received) / sent

    return percentile(rtts, 50), percentile(rtts, 95), percentile(rtts, 99), loss


def read_iperf(folder, name):
    # Returns average received throughput in Mbps for iperf_<name>.json
    path = os.path.join(folder, 'iperf_%s.json' % name)
    if not os.path.exists(path):
        return float('nan')
    try:
        data = json.load(open(path))
        return data['end']['sum_received']['bits_per_second'] / 1e6
    except (ValueError, KeyError):
        print('  (could not read %s - check the file for an iperf3 error)' % path)
        return float('nan')


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--input', default='eval/results')
    args = parser.parse_args()

    columns = ['mode',
               'l4s_p50_ms', 'l4s_p95_ms', 'l4s_p99_ms', 'l4s_loss_pct',
               'classic_p50_ms', 'classic_p95_ms', 'classic_p99_ms', 'classic_loss_pct',
               'classic_tput_mbps', 'l4s_tput_mbps', 'total_tput_mbps']
    rows = []

    for mode in MODES:
        folder = os.path.join(args.input, mode)
        if not os.path.isdir(folder):
            continue
        l4s = read_probe(folder, 'l4s')
        classic = read_probe(folder, 'classic')
        c_tput = read_iperf(folder, 'classic')
        l_tput = read_iperf(folder, 'l4s')
        rows.append([mode] + list(l4s) + list(classic) + [c_tput, l_tput, c_tput + l_tput])

    if not rows:
        print('No result folders found in', args.input)
        return

    # Print a readable table
    print('Probe RTT in ms (base RTT is ~20 ms). Throughput in Mbps (link is 50 Mbps).\n')
    print('%-9s | %-26s | %-26s | %-22s' % ('', 'L4S probe  p50/p95/p99 loss', 'Classic probe p50/p95/p99 loss',
                                           'Throughput C / L / total'))
    for r in rows:
        print('%-9s | %6.1f %6.1f %6.1f %4.1f%% | %6.1f %6.1f %6.1f %4.1f%% | %5.1f / %5.1f / %5.1f' %
              (r[0], r[1], r[2], r[3], r[4], r[5], r[6], r[7], r[8], r[9], r[10], r[11]))

    out_path = os.path.join(args.input, 'summary.csv')
    with open(out_path, 'w', newline='') as f:
        writer = csv.writer(f)
        writer.writerow(columns)
        for r in rows:
            writer.writerow([r[0]] + ['%.2f' % v for v in r[1:]])
    print('\nSaved', out_path)


if __name__ == '__main__':
    main()
