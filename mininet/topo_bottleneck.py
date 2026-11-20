#!/usr/bin/env python3
# 3-node topology:  h1 (10.0.1.1) --- r0 --- h2 (10.0.2.1)
#
# r0 is a plain Mininet host with kernel forwarding OFF.
# Our program build/l4s_router does the forwarding with raw sockets.
# Links are NOT rate limited here: the 50 Mbps bottleneck is inside our router,
# so the queue builds in OUR queues and not in a kernel qdisc.
#
# Interactive:  sudo python3 mininet/topo_bottleneck.py
# Automatic:    sudo python3 mininet/topo_bottleneck.py --run dualq --scenario normal --duration 30
#   modes:      fifo, dualq_basic, dualq, dualq_qp, dualq_qp_police (our router) | fq_codel, dualpi2 (kernel)
#   scenarios:  normal = 1 Classic flow + 1 L4S flow + 2 latency probes
#               attack = normal + an ECT(1) UDP flood (60 Mbps) that ignores marks,
#                        from t=10 s until 10 s before the end

import argparse
import os
import time

from mininet.net import Mininet
from mininet.cli import CLI
from mininet.log import setLogLevel

RATE_MBIT = 50  # must match rate_mbps in config/router_config.ini
OUR_MODES = ['fifo', 'dualq_basic', 'dualq', 'dualq_qp', 'dualq_qp_police']
KERNEL_MODES = ['fq_codel', 'dualpi2']
ATTACK_START = 10    # seconds after the flows start
# Router config file. Can be changed for one run with the ROUTER_CONFIG environment
# variable, e.g. ROUTER_CONFIG=config/router_config_breaker_off.ini (used by eval/run_final.sh)
ROUTER_CONFIG = os.environ.get('ROUTER_CONFIG', 'config/router_config.ini')
FLOOD_RATE = '60M'   # more than the 50 Mbps link


def allow_dctcp():
    # Mininet hosts are separate network namespaces. Inside a namespace, Linux only
    # lets you choose a default congestion control from the "allowed" list, and that
    # list can only be changed from the main namespace. So we add dctcp here, before
    # the hosts exist. (This setting stays until reboot; it is harmless.)
    os.system('modprobe tcp_dctcp')
    path = '/proc/sys/net/ipv4/tcp_allowed_congestion_control'
    allowed = open(path).read().split()
    if 'dctcp' not in allowed:
        allowed.append('dctcp')
        with open(path, 'w') as f:
            f.write(' '.join(allowed))
    print('Allowed congestion controls:', open(path).read().strip())


def build_network():
    allow_dctcp()
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


def ecn_state(ss):
    # Reads "ss -tin" output for the L4S connection.
    #   working DCTCP shows  "dctcp:(ce_state:...,alpha:...)"
    #   failed ECN shows     "dctcp-reno" and "dctcp:fallback_mode"
    # (ss on newer systems does not print an "ecn" flag, so we do not rely on it)
    if 'dctcp-reno' in ss or 'fallback_mode' in ss:
        return 'NO'
    if 'dctcp' in ss:
        return 'yes'
    return '?'


def check_ecn(h1, out):
    ss = h1.cmd('ss -tin dst 10.0.2.2')
    with open(out + '/ss_check.txt', 'w') as f:
        f.write(ss)
    state = ecn_state(ss)
    if state == 'yes':
        print('OK: L4S flow is using DCTCP with ECN')
    elif state == 'NO':
        print('WARNING: L4S flow fell back to dctcp-reno, ECN was NOT negotiated. See %s/ss_check.txt' % out)
    else:
        print('WARNING: could not find the DCTCP connection. See %s/ss_check.txt' % out)


def run_experiment(net, mode, scenario, duration):
    h1, h2, r0 = net.get('h1', 'h2', 'r0')
    out = 'eval/results/' + mode
    os.makedirs(out, exist_ok=True)
    attack_len = max(5, duration - ATTACK_START - 10)

    # 1. Start the forwarding path
    path_start = time.time()
    if mode in OUR_MODES:
        r0.cmd('./build/l4s_router %s %s %s/router_stats.csv > %s/router_log.txt 2>&1 &'
               % (ROUTER_CONFIG, mode, out, out))
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
    if scenario == 'attack':
        # One-shot server (-1) that logs what it RECEIVES, once per second
        h2.cmd('iperf3 -s -p 5203 -1 -i 1 -J > %s/iperf_flood.json 2>&1 &' % out)
    time.sleep(1)

    # Write down the settings of this run (used by the plots and kept for the report)
    with open(out + '/run_info.txt', 'w') as f:
        f.write('mode=%s\nscenario=%s\nduration=%d\nconfig=%s\n' % (mode, scenario, duration, ROUTER_CONFIG))
        # seconds between router start and flow start (to line up router_stats.csv with the flows)
        f.write('flows_start=%.2f\n' % (time.time() - path_start))
        if scenario == 'attack':
            f.write('attack_start=%d\nattack_end=%d\nflood_rate=%s\n'
                    % (ATTACK_START, ATTACK_START + attack_len, FLOOD_RATE))

    # 4. Traffic from h1: one Classic bulk flow, one L4S bulk flow, two latency probes
    print('Running %s (%s scenario) for %d s ...' % (mode, scenario, duration))
    h1.cmd('iperf3 -c 10.0.2.1 -p 5201 -t %d -i 1 -J > %s/iperf_classic.json 2>&1 &' % (duration, out))
    # L4S flow goes to h2's second address 10.0.2.2: h1's route to it uses DCTCP from the SYN on
    h1.cmd('iperf3 -c 10.0.2.2 -p 5202 -t %d -i 1 -J > %s/iperf_l4s.json 2>&1 &' % (duration, out))
    h1.cmd('python3 eval/latency_probe.py --ecn 1 --duration %d --out %s/probe_l4s.csv > %s/probe_l4s.txt 2>&1 &'
           % (duration, out, out))
    h1.cmd('python3 eval/latency_probe.py --ecn 0 --duration %d --out %s/probe_classic.csv > %s/probe_classic.txt 2>&1 &'
           % (duration, out, out))

    # Kernel dualpi2 reports its own queue delays: sample them every 100 ms,
    # so we can compare its L4S queue delay with our router's.
    if mode == 'dualpi2':
        r0.cmd('(while true; do tc -s qdisc show dev r0-eth1 | grep delay_c; sleep 0.1; done) > %s/tc_samples.txt 2>&1 &'
               ' echo $! > /tmp/tc_loop.pid' % out)

    # 5. After 5 s, check that the L4S flow really uses DCTCP with ECN
    time.sleep(5)
    check_ecn(h1, out)

    # 6. Attack: a UDP flood with ECT(1) (TOS = 1) that does not react to CE marks
    if scenario == 'attack':
        time.sleep(ATTACK_START - 5)
        print('Attack: %s ECT(1) UDP flood for %d s' % (FLOOD_RATE, attack_len))
        h1.cmd('iperf3 -c 10.0.2.1 -p 5203 -u -b %s -l 1400 -S 1 -t %d > /dev/null 2>&1 &'
               % (FLOOD_RATE, attack_len))
        time.sleep(duration - ATTACK_START + 4)
    else:
        time.sleep(max(0, duration - 5) + 4)

    # 7. Stop everything
    r0.cmd('pkill -INT l4s_router')
    if mode == 'dualpi2':
        r0.cmd('kill $(cat /tmp/tc_loop.pid) 2>/dev/null')
    h2.cmd('pkill iperf3')
    h2.cmd('pkill -f latency_probe.py')
    time.sleep(1)

    print(h1.cmd('cat %s/probe_l4s.txt %s/probe_classic.txt' % (out, out)))
    if mode in OUR_MODES:
        print(r0.cmd('cat %s/router_log.txt' % out))
    else:
        print(r0.cmd('tc -s qdisc show dev r0-eth1'))
    print('Results saved in', out)


def debug_ecn(net):
    # Finds out WHY the L4S (DCTCP) flow does not get ECN.
    # Uses plain kernel forwarding on r0 (no AQM, no l4s_router), so only the
    # hosts and our tc rule on h1 are involved. Captures the SYN / SYN-ACK on h2
    # (tos = ECN bits of the IP header, flags: E = ECE, W = CWR, e = AE in new tcpdump)
    # and checks the result with ss on h1. Three tests:
    #   A) the old way: iperf3 -C dctcp (DCTCP switched on only after the SYN)
    #   B) the new way: route to 10.0.2.2 with "congctl dctcp" (DCTCP from the SYN on)
    h1, h2, r0 = net.get('h1', 'h2', 'r0')
    r0.cmd('sysctl -q -w net.ipv4.ip_forward=1')
    os.makedirs('eval/results', exist_ok=True)
    report = []

    def line(text=''):
        print(text)
        report.append(text)

    line('Kernel: ' + os.uname().release)
    for node in (h1, h2):
        line('%s: tcp_ecn=%s tcp_ecn_fallback=%s default_cc=%s' % (
            node.name, node.cmd('sysctl -n net.ipv4.tcp_ecn').strip(),
            node.cmd('sysctl -n net.ipv4.tcp_ecn_fallback').strip(),
            node.cmd('sysctl -n net.ipv4.tcp_congestion_control').strip()))
    line('tc filter on h1: ' + ' '.join(h1.cmd('tc filter show dev h1-eth0 egress').split()))
    route = h1.cmd('ip route get 10.0.2.2').strip()
    line('h1 route to 10.0.2.2: ' + (route.splitlines()[0] if route else 'none'))

    def one_test(name, dest, cc_option):
        line('')
        line('===== Test %s =====' % name)
        h2.cmd('pkill iperf3; sleep 0.5')
        h2.cmd('iperf3 -s -p 5202 -D')   # new listener, so it gets h2's current default (dctcp)
        h2.cmd('tcpdump -l -i h2-eth0 -n -v -c 8 "tcp port 5202 and tcp[tcpflags] & tcp-syn != 0"'
               ' > /tmp/syn_capture.txt 2>&1 &')
        time.sleep(1)
        h1.cmd('iperf3 -c %s -p 5202 %s -t 3 > /dev/null 2>&1 &' % (dest, cc_option))
        time.sleep(1.5)
        ss = h1.cmd('ss -tin dst %s:5202' % dest)
        time.sleep(3)
        h2.cmd('pkill tcpdump')
        time.sleep(0.5)
        line('--- SYN / SYN-ACK seen at h2 (control connection first, then data connection) ---')
        for text in h2.cmd('cat /tmp/syn_capture.txt').splitlines():
            if 'tos' in text or 'Flags' in text:
                line('  ' + ' '.join(text.split()))
        line('--- ss on h1 during the transfer ---')
        for text in ss.splitlines():
            line('  ' + ' '.join(text.split()))
        state = ecn_state(ss)
        if state == 'yes':
            line('RESULT: DCTCP with ECN is working')
        elif state == 'NO':
            line('RESULT: ECN NOT negotiated (dctcp-reno / fallback_mode)')
        else:
            line('RESULT: no DCTCP connection found (expected for a cubic test)')

    # A: the old way (iperf3 -C dctcp switches DCTCP on only after the SYN) -> expected to FAIL
    one_test('A: old way, iperf3 -C dctcp to 10.0.2.1', '10.0.2.1', '-C dctcp')
    # B: the new way (route to 10.0.2.2 uses DCTCP from the SYN on), with our tc rule -> expected OK
    one_test('B: new way, DCTCP route to 10.0.2.2, with tc rule', '10.0.2.2', '')
    h2.cmd('pkill iperf3')

    # Save straight into experiments/ so it is kept
    folder = 'experiments/%s_ecn_debug' % time.strftime('%Y-%m-%d_%H%M')
    os.makedirs(folder, exist_ok=True)
    with open(folder + '/ecn_debug.txt', 'w') as f:
        f.write('\n'.join(report) + '\n')
    if os.environ.get('SUDO_USER'):
        os.system('chown -R %s experiments' % os.environ['SUDO_USER'])
    print('\nSaved %s/ecn_debug.txt' % folder)


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--run', choices=OUR_MODES + KERNEL_MODES,
                        help='run one experiment automatically instead of opening the CLI')
    parser.add_argument('--scenario', choices=['normal', 'attack'], default='normal')
    parser.add_argument('--duration', type=int, default=30)
    parser.add_argument('--debug-ecn', action='store_true',
                        help='check ECN negotiation of the DCTCP flow (about 1 minute)')
    args = parser.parse_args()

    setLogLevel('info')
    net = build_network()

    if args.debug_ecn:
        debug_ecn(net)
    elif args.run:
        run_experiment(net, args.run, args.scenario, args.duration)
    else:
        print('\nNext: mininet> r0 ./build/l4s_router config/router_config.ini dualq > eval/results/router_log.txt 2>&1 &')
        print('Then: mininet> h1 ping -c 5 10.0.2.1\n')
        CLI(net)

    net.stop()
