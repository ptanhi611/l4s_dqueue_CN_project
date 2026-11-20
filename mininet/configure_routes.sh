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

    # 4. Receiver side of the L4S flow must also be DCTCP:
    #    - DCTCP puts ECT(0) on its SYN. A cubic listener sees an ECN-marked SYN
    #      as suspicious and refuses ECN. A DCTCP listener accepts it.
    #    - A DCTCP receiver echoes every CE mark accurately (classic ECN does not).
    #    h2 only sends ACKs, so this does not change the Classic flow's behaviour
    #    (the Classic sender h1 uses cubic and does not ask for ECN).
    modprobe tcp_dctcp 2>/dev/null
    sysctl -q -w net.ipv4.tcp_congestion_control=dctcp
    echo "h2: default congestion control = $(sysctl -n net.ipv4.tcp_congestion_control)"

    # 5. Second address for h2, used only by the L4S flow (see step 6 on h1)
    ip addr add 10.0.2.2/24 dev h2-eth0
    echo "h2: extra address 10.0.2.2 for the L4S flow"
fi

if [ "$NODE" = "h1" ]; then
    # 6. L4S sender: DCTCP must be active BEFORE the SYN is sent, otherwise the SYN
    #    does not ask for ECN and DCTCP falls back to Reno ("dctcp-reno").
    #    (iperf3 -C only switches the congestion control after the handshake.)
    #    Linux can choose the congestion control per route: everything h1 sends to
    #    10.0.2.2 uses DCTCP from the first packet. The Classic flow uses 10.0.2.1 (cubic).
    modprobe tcp_dctcp 2>/dev/null
    ip route add 10.0.2.2/32 via 10.0.1.254 congctl dctcp
    echo "h1: route to 10.0.2.2 uses: $(ip route get 10.0.2.2 | head -1)"

    # 7. L4S marking trick. Normal Linux has no TCP Prague, and DCTCP sends ECT(0).
    #    For TCP packets to 10.0.2.2:5202 that are ECT(0) and are NOT the SYN,
    #    rewrite the ECN bits to ECT(1) and fix the IP checksum.
    #    (We leave the SYN alone so the ECN handshake sees exactly what DCTCP sent.)
    #    So:  iperf3 -c 10.0.2.2 -p 5202   ==> L4S flow (DCTCP, ECT(1))
    #         iperf3 -c 10.0.2.1 -p 5201   ==> Classic flow (cubic, Not-ECT)
    tc qdisc add dev h1-eth0 clsact
    tc filter add dev h1-eth0 egress protocol ip u32 \
        match ip protocol 6 0xff \
        match ip dst 10.0.2.2/32 \
        match ip dport 5202 0xffff \
        match ip tos 0x02 0x03 \
        match u8 0x00 0x02 at 33 \
        action pedit ex munge ip tos set 0x01 retain 0x03 pipe \
        action csum ip
    # (at 33 = TCP flags byte: 20 B IP header + 13; bit 0x02 = SYN must be 0)
    echo "h1: ECT(0) -> ECT(1) rewrite for TCP to 10.0.2.2:5202 installed"
fi
