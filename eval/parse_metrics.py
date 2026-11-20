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

MODES = ['fifo', 'dualq_basic', 'dualq', 'dualq_qp', 'fq_codel', 'dualpi2']
OUR_MODES = ['fifo', 'dualq_basic', 'dualq', 'dualq_qp']


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


def parse_tc_time(text):
    # tc prints times like "850us", "12.3ms" or "1.2s". Returns ms.
    match = re.match(r'([\d.]+)(us|ms|s)', text)
    if not match:
        return 0.0
    value, unit = float(match.group(1)), match.group(2)
    if unit == 'us':
        return value / 1000.0
    if unit == 's':
        return value * 1000.0
    return value


def read_queue_delays(folder, mode):
    # Returns (l4s_list, classic_list) of queue delay samples in ms, taken every 100 ms.
    #   our router:      router_stats.csv (written by l4s_router)
    #   kernel dualpi2:  tc_samples.txt   (lines like "prob 0.01 delay_c 12ms delay_l 800us")
    l4s, classic = [], []
    if mode in OUR_MODES:
        path = os.path.join(folder, 'router_stats.csv')
        if os.path.exists(path):
            with open(path) as f:
                for row in csv.DictReader(f):
                    classic.append(float(row['c_delay_ms']))
                    l4s.append(float(row['l_delay_ms']))
        if mode == 'fifo':
            l4s = []  # fifo has only one queue, reported in the classic column
    elif mode == 'dualpi2':
        path = os.path.join(folder, 'tc_samples.txt')
        if os.path.exists(path):
            for line in open(path):
                c = re.search(r'delay_c (\S+)', line)
                l = re.search(r'delay_l (\S+)', line)
                if c and l:
                    classic.append(parse_tc_time(c.group(1)))
                    l4s.append(parse_tc_time(l.group(1)))
    return l4s, classic


def mean(values):
    if not values:
        return float('nan')
    return sum(values) / len(values)


def ecn_check(folder):
    # Reads ss_check.txt written during the run: was the L4S flow DCTCP with ECN?
    path = os.path.join(folder, 'ss_check.txt')
    if not os.path.exists(path):
        return '?'
    text = open(path).read()
    # working DCTCP: "dctcp:(ce_state:...)"; failed ECN: "dctcp-reno" / "fallback_mode"
    if 'dctcp-reno' in text or 'fallback_mode' in text:
        return 'NO'
    if 'dctcp' in text:
        return 'yes'
    return '?'


def read_run_info(folder):
    # run_info.txt has lines like "scenario=attack" and "attack_start=10"
    info = {}
    path = os.path.join(folder, 'run_info.txt')
    if os.path.exists(path):
        for line in open(path):
            if '=' in line:
                key, value = line.strip().split('=', 1)
                info[key] = value
    return info


def iperf_mean_in_window(path, start, end, offset=0.0):
    # Average throughput (Mbps) of the 1-second iperf3 intervals inside [start, end].
    # offset: time when this iperf3 test started (the flood starts at attack_start).
    values = []
    if not os.path.exists(path):
        return float('nan')
    try:
        data = json.load(open(path))
        for interval in data['intervals']:
            t0 = interval['sum']['start'] + offset
            t1 = interval['sum']['end'] + offset
            if t0 >= start and t1 <= end:
                values.append(interval['sum']['bits_per_second'] / 1e6)
    except (ValueError, KeyError):
        return float('nan')
    return mean(values)


def probe_in_window(folder, name, start, end, interval=0.01):
    # p50 and p99 RTT of the probes sent inside [start, end] (probe seq * 10 ms = send time)
    rtts = []
    path = os.path.join(folder, 'probe_%s.csv' % name)
    if os.path.exists(path):
        with open(path) as f:
            for row in csv.DictReader(f):
                t = int(row['seq']) * interval
                if start <= t <= end:
                    rtts.append(float(row['rtt_ms']))
    rtts.sort()
    return percentile(rtts, 50), percentile(rtts, 99)


def attack_metrics(folder):
    # Numbers measured only DURING the attack (None if this was not an attack run)
    info = read_run_info(folder)
    if info.get('scenario') != 'attack':
        return None
    start, end = float(info['attack_start']), float(info['attack_end'])
    # Skip the first 2 s of the attack, so we measure the steady state
    w0, w1 = start + 2, end
    return {
        'l4s_probe': probe_in_window(folder, 'l4s', w0, w1),
        'classic_probe': probe_in_window(folder, 'classic', w0, w1),
        'classic_tput': iperf_mean_in_window(os.path.join(folder, 'iperf_classic.json'), w0, w1),
        'l4s_tput': iperf_mean_in_window(os.path.join(folder, 'iperf_l4s.json'), w0, w1),
        'flood_tput': iperf_mean_in_window(os.path.join(folder, 'iperf_flood.json'), w0, w1, offset=start),
    }


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--input', default='eval/results')
    args = parser.parse_args()

    columns = ['mode',
               'l4s_p50_ms', 'l4s_p95_ms', 'l4s_p99_ms', 'l4s_loss_pct',
               'classic_p50_ms', 'classic_p95_ms', 'classic_p99_ms', 'classic_loss_pct',
               'classic_tput_mbps', 'l4s_tput_mbps', 'total_tput_mbps',
               'l4s_qdelay_mean_ms', 'l4s_qdelay_p99_ms', 'classic_qdelay_mean_ms', 'classic_qdelay_p99_ms',
               'l4s_ecn_ok',
               'atk_l4s_p50_ms', 'atk_l4s_p99_ms', 'atk_classic_p50_ms', 'atk_classic_p99_ms',
               'atk_classic_tput_mbps', 'atk_l4s_tput_mbps', 'atk_flood_tput_mbps']
    rows = []
    attacks = {}

    for mode in MODES:
        folder = os.path.join(args.input, mode)
        if not os.path.isdir(folder):
            continue
        l4s = read_probe(folder, 'l4s')
        classic = read_probe(folder, 'classic')
        c_tput = read_iperf(folder, 'classic')
        l_tput = read_iperf(folder, 'l4s')
        l_q, c_q = read_queue_delays(folder, mode)
        qdelay = [mean(l_q), percentile(sorted(l_q), 99), mean(c_q), percentile(sorted(c_q), 99)]
        rows.append([mode] + list(l4s) + list(classic) + [c_tput, l_tput, c_tput + l_tput] + qdelay
                    + [ecn_check(folder)])
        attacks[mode] = attack_metrics(folder)

    if not rows:
        print('No result folders found in', args.input)
        return

    # Print a readable table
    print('Probe RTT in ms (base RTT is ~20 ms). Throughput in Mbps (link is 50 Mbps).\n')
    print('%-11s | %-26s | %-26s | %-22s' % ('', 'L4S probe  p50/p95/p99 loss', 'Classic probe p50/p95/p99 loss',
                                           'Throughput C / L / total'))
    for r in rows:
        print('%-11s | %6.1f %6.1f %6.1f %4.1f%% | %6.1f %6.1f %6.1f %4.1f%% | %5.1f / %5.1f / %5.1f' %
              (r[0], r[1], r[2], r[3], r[4], r[5], r[6], r[7], r[8], r[9], r[10], r[11]))

    print('\nQueue delay inside the bottleneck in ms (sampled every 100 ms; fq_codel does not report it)')
    print('%-11s | %-19s | %-19s | %s' % ('', 'L4S queue mean/p99', 'Classic mean/p99', 'L4S flow DCTCP+ECN?'))
    for r in rows:
        print('%-11s | %8.2f %10.2f | %8.2f %10.2f | %s' % (r[0], r[12], r[13], r[14], r[15], r[16]))
    print('(fifo has one queue: it is shown in the Classic column)')

    if any(attacks.values()):
        print('\nDURING THE ATTACK (ECT(1) UDP flood, steady state from 2 s after it starts)')
        print('%-11s | %-17s | %-17s | %s' % ('', 'L4S probe p50/p99', 'Classic p50/p99', 'Tput Classic / L4S / flood (Mbps)'))
        for mode in [r[0] for r in rows]:
            a = attacks[mode]
            if a:
                print('%-11s | %7.1f %8.1f | %7.1f %8.1f | %5.1f / %5.1f / %5.1f' % (
                    mode, a['l4s_probe'][0], a['l4s_probe'][1], a['classic_probe'][0], a['classic_probe'][1],
                    a['classic_tput'], a['l4s_tput'], a['flood_tput']))

    out_path = os.path.join(args.input, 'summary.csv')
    with open(out_path, 'w', newline='') as f:
        writer = csv.writer(f)
        writer.writerow(columns)
        for r in rows:
            a = attacks.get(r[0])
            atk = []
            if a:
                atk = list(a['l4s_probe']) + list(a['classic_probe']) + [a['classic_tput'], a['l4s_tput'], a['flood_tput']]
            writer.writerow([r[0]] + ['%.2f' % v for v in r[1:-1]] + [r[-1]] + ['%.2f' % v for v in atk])
    print('\nSaved', out_path)


if __name__ == '__main__':
    main()
