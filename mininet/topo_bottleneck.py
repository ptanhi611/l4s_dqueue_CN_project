#!/usr/bin/env python3
# 3-node topology:  h1 (10.0.1.1) --- r0 --- h2 (10.0.2.1)
#
# r0 is a plain Mininet host with kernel forwarding OFF.
# Our program build/l4s_router does the forwarding with raw sockets.
# Links are NOT rate limited here: the 50 Mbps bottleneck is inside our router,
# so the queue builds in OUR queues and not in a kernel qdisc.
#
# Run:  sudo -E python3 mininet/topo_bottleneck.py

import os
from mininet.net import Mininet
from mininet.cli import CLI
from mininet.log import setLogLevel


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


if __name__ == '__main__':
    setLogLevel('info')
    net = build_network()
    print('\nNext: mininet> r0 ./build/l4s_router config/router_config.ini dualq > eval/results/router_log.txt 2>&1 &')
    print('Then: mininet> h1 ping -c 5 10.0.2.1\n')
    CLI(net)
    net.stop()
