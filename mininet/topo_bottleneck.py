#!/usr/bin/env python3
# 3-node topology:  h1 (10.0.1.1) --- r0 --- h2 (10.0.2.1)
#
# r0 is a plain Mininet host with kernel forwarding OFF.
# Our program build/l4s_router does the forwarding with raw sockets.
# Links are NOT rate limited here: the 50 Mbps bottleneck is inside our router,
# so the queue builds in OUR queues and not in a kernel qdisc.
#
# Interactive:  sudo python3 mininet/topo_bottleneck.py
# Automatic:    sudo python3 mininet/topo_bottleneck.py --run dualq --duration 30
#               modes: fifo, dualq (our router) | fq_codel, dualpi2 (Linux kernel baselines)

import argparse
import os
import time

from mininet.net import Mininet
from mininet.cli import CLI
from mininet.log import setLogLevel

RATE_MBIT = 50  # must match rate_mbps in config/router_config.ini


def build_network():
    net = Mininet(controller=None, autoSetMacs=False)

    h1 = net.addHost('h1', ip='10.0.1.1/24', mac='00:00:00:00:01:01', defaultRoute='via 10.0.1.254')
    h2 = net.addHost('h2', ip='10.0.2.1/24', mac='00:00:00:00:02:01', defaultRoute='via 10.0.2.254')
    r0 = net.addHost('r0', ip=None)

    net.addLink(h1, r0, intfName1='h1-eth0', intfName2='r0-eth0', params2={'ip': '10.0.1.254/24'})
    net.addLink(h2, r0, intfName1='h2-eth0', intfName2='r0-eth1', params2={'ip': '10.0.2.254/24'})

    net.start()

    # Offloads off, forwarding off, delay, ECT(1) rule (see the script for details)
    for node in (h1, h2, r0):
        print(node.cmd('bash mininet/configure_routes.sh ' + node.name))

    os.makedirs('eval/results', exist_ok=True)
    return net


def setup_kernel_baseline(r0, mode):
    # Baselines use the normal Linux kernel forwarding + a kernel qdisc.
    # HTB limits r0-eth1 to 50 Mbit, and the AQM (fq_codel or dualpi2) sits under it,
    # so the queue builds inside fq_codel / dualpi2.
    r0.cmd('sysctl -q -w net.ipv4.ip_forward=1')
    r0.cmd('tc qdisc add dev r0-eth1 root handle 1: htb default 10')
    r0.cmd('tc class add dev r0-eth1 parent 1: classid 1:10 htb rate %dmbit ceil %dmbit' % (RATE_MBIT, RATE_MBIT))
    out = r0.cmd('tc qdisc add dev r0-eth1 parent 1:10 handle 20: %s' % mode)
    if out.strip() != '':
        print('Could not set up %s on r0: %s' % (mode, out.strip()))
        if mode == 'dualpi2':
            print('dualpi2 needs Linux kernel 6.17+ and a new iproute2. Skipping this mode.')
        return False
    print(r0.cmd('tc qdisc show dev r0-eth1'))
    return True


def run_experiment(net, mode, duration):
    h1, h2, r0 = net.get('h1', 'h2', 'r0')
    out = 'eval/results/' + mode
    os.makedirs(out, exist_ok=True)

    # 1. Start the forwarding path
    if mode in ('fifo', 'dualq'):
        r0.cmd('./build/l4s_router config/router_config.ini %s %s/router_stats.csv > %s/router_log.txt 2>&1 &'
               % (mode, out, out))
    else:
        if not setup_kernel_baseline(r0, mode):
            return
    time.sleep(1)

    # 2. Sanity check: can h1 reach h2 at all?
    ping = h1.cmd('ping -c 3 -W 1 10.0.2.1')
    print(ping)
    if ' 0 received' in ping:
        print('ERROR: h1 cannot reach h2 in mode %s. Check %s/router_log.txt' % (mode, out))
        r0.cmd('pkill -INT l4s_router')
        return

    # 3. Servers on h2
    h2.cmd('iperf3 -s -p 5201 -D')   # Classic receiver
    h2.cmd('iperf3 -s -p 5202 -D')   # L4S receiver
    h2.cmd('python3 eval/latency_probe.py --server > /dev/null 2>&1 &')
    time.sleep(1)

    # 4. Traffic from h1: one Classic bulk flow, one L4S bulk flow, two latency probes
    print('Running %s for %d s ...' % (mode, duration))
    h1.cmd('iperf3 -c 10.0.2.1 -p 5201 -t %d -i 1 -J > %s/iperf_classic.json 2>&1 &' % (duration, out))
    h1.cmd('iperf3 -c 10.0.2.1 -p 5202 -C dctcp -t %d -i 1 -J > %s/iperf_l4s.json 2>&1 &' % (duration, out))
    h1.cmd('python3 eval/latency_probe.py --ecn 1 --duration %d --out %s/probe_l4s.csv > %s/probe_l4s.txt 2>&1 &'
           % (duration, out, out))
    h1.cmd('python3 eval/latency_probe.py --ecn 0 --duration %d --out %s/probe_classic.csv > %s/probe_classic.txt 2>&1 &'
           % (duration, out, out))

    time.sleep(duration + 4)

    # 5. Stop everything
    r0.cmd('pkill -INT l4s_router')
    h2.cmd('pkill iperf3')
    h2.cmd('pkill -f latency_probe.py')
    time.sleep(1)

    print(h1.cmd('cat %s/probe_l4s.txt %s/probe_classic.txt' % (out, out)))
    if mode in ('fifo', 'dualq'):
        print(r0.cmd('cat %s/router_log.txt' % out))
    else:
        print(r0.cmd('tc -s qdisc show dev r0-eth1'))
    print('Results saved in', out)


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--run', choices=['fifo', 'dualq', 'fq_codel', 'dualpi2'],
                        help='run one experiment automatically instead of opening the CLI')
    parser.add_argument('--duration', type=int, default=30)
    args = parser.parse_args()

    setLogLevel('info')
    net = build_network()

    if args.run:
        run_experiment(net, args.run, args.duration)
    else:
        print('\nNext: mininet> r0 ./build/l4s_router config/router_config.ini dualq > eval/results/router_log.txt 2>&1 &')
        print('Then: mininet> h1 ping -c 5 10.0.2.1\n')
        CLI(net)

    net.stop()
