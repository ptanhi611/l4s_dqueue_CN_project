#!/usr/bin/env python3
# UDP latency probe with a chosen ECN codepoint.
#
# On h2 (echo server):
#     python3 eval/latency_probe.py --server
# On h1 (client), one probe every 10 ms for 30 s:
#     python3 eval/latency_probe.py --dest 10.0.2.1 --ecn 1 --duration 30 --out eval/results/probe_l4s.csv
#
# --ecn 0 = Not-ECT (Classic queue), --ecn 1 = ECT(1) (L4S queue), --ecn 2 = ECT(0) (Classic queue)

import argparse
import select
import socket
import struct
import time

PORT = 9000


def run_server():
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.bind(('0.0.0.0', PORT))
    print('Echo server on UDP port', PORT)
    while True:
        data, addr = sock.recvfrom(2048)
        sock.sendto(data, addr)


def percentile(sorted_values, p):
    if not sorted_values:
        return 0.0
    index = int(p / 100.0 * (len(sorted_values) - 1))
    return sorted_values[index]


def run_client(dest, ecn, interval, duration, size, out_path):
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    # Lowest 2 bits of TOS are the ECN field
    sock.setsockopt(socket.IPPROTO_IP, socket.IP_TOS, ecn)

    rtts = {}  # seq -> rtt in ms
    sent = 0
    start = time.time()
    next_send = start
    padding = b'x' * max(0, size - 12)

    while time.time() - start < duration + 1.0:  # extra 1 s to collect late replies
        now = time.time()

        if now >= next_send and now - start < duration:
            packet = struct.pack('!Id', sent, now) + padding  # seq (4 bytes) + send time (8 bytes)
            sock.sendto(packet, (dest, PORT))
            sent += 1
            next_send += interval

        wait = max(0.0, min(next_send - time.time(), 0.01))
        ready, _, _ = select.select([sock], [], [], wait)
        if ready:
            data, _ = sock.recvfrom(2048)
            seq, send_time = struct.unpack('!Id', data[:12])
            rtts[seq] = (time.time() - send_time) * 1000.0

    with open(out_path, 'w') as f:
        f.write('seq,rtt_ms\n')
        for seq in sorted(rtts):
            f.write('%d,%.3f\n' % (seq, rtts[seq]))

    values = sorted(rtts.values())
    lost = sent - len(values)
    print('sent %d, received %d, lost %d' % (sent, len(values), lost))
    print('RTT ms: p50 %.2f | p95 %.2f | p99 %.2f | max %.2f' % (
        percentile(values, 50), percentile(values, 95), percentile(values, 99),
        values[-1] if values else 0.0))
    print('saved', out_path)


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--server', action='store_true')
    parser.add_argument('--dest', default='10.0.2.1')
    parser.add_argument('--ecn', type=int, default=1, choices=[0, 1, 2])
    parser.add_argument('--interval', type=float, default=0.01)
    parser.add_argument('--duration', type=float, default=30)
    parser.add_argument('--size', type=int, default=100)
    parser.add_argument('--out', default='eval/results/probe.csv')
    args = parser.parse_args()

    if args.server:
        run_server()
    else:
        run_client(args.dest, args.ecn, args.interval, args.duration, args.size, args.out)
