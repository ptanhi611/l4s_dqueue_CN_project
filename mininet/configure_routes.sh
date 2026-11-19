#!/bin/bash
# Runs inside one Mininet node. Called by topo_bottleneck.py:
#     bash mininet/configure_routes.sh <h1|h2|r0>

NODE=$1

# 1. Turn off offloads on every interface of this node.
#    Otherwise the raw socket sees 64 KB "super packets" (TSO/GSO/GRO),
#    and packets with unfinished checksums (tx checksum offload).
for IF in $(ip -o link show | awk -F': ' '{print $2}' | cut -d@ -f1 | grep -v '^lo$'); do
    ethtool -K $IF tso off gso off gro off lro off tx off rx off 2>/dev/null
done

if [ "$NODE" = "r0" ]; then
    # 2. Kernel must NOT forward. Only our l4s_router forwards packets.
    sysctl -q -w net.ipv4.ip_forward=0
    echo "r0: kernel forwarding off, offloads off"
fi

if [ "$NODE" = "h2" ]; then
    # 3. Base RTT: 20 ms delay on h2's outgoing side (the ACK path).
    #    This is only a delay line, no rate limit, so no queue builds here.
    tc qdisc add dev h2-eth0 root netem delay 20ms limit 100000
    echo "h2: netem delay 20 ms on h2-eth0"
fi

if [ "$NODE" = "h1" ]; then
    # 4. L4S bulk sender trick.
    #    Normal Linux has no TCP Prague. DCTCP is a scalable congestion control
    #    but it sends ECT(0). For TCP traffic going to port 5202, this rule
    #    rewrites ECN bits ECT(0) -> ECT(1) and fixes the IP checksum.
    #    So:  iperf3 -c 10.0.2.1 -p 5202 -C dctcp   ==> L4S flow
    #         iperf3 -c 10.0.2.1 -p 5201            ==> Classic (cubic) flow
    modprobe tcp_dctcp 2>/dev/null
    tc qdisc add dev h1-eth0 clsact
    tc filter add dev h1-eth0 egress protocol ip u32 \
        match ip protocol 6 0xff \
        match ip dport 5202 0xffff \
        action pedit ex munge ip tos set 0x01 retain 0x03 pipe \
        action csum ip
    echo "h1: ECT(1) rewrite for TCP port 5202 installed"
fi
