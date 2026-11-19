#!/bin/bash
# Runs the same experiment in every mode, one after another, then makes the
# summary table and the graphs.
#
# Usage (from the repo root):  sudo ./eval/traffic_profile.sh [duration_seconds]
#
# Each mode: 1 Classic (cubic) bulk flow + 1 L4S (DCTCP, ECT(1)) bulk flow
#            + 1 L4S latency probe + 1 Classic latency probe, through a 50 Mbps bottleneck.

DURATION=${1:-30}
MODES="fifo dualq fq_codel dualpi2"

if [ "$EUID" -ne 0 ]; then
    echo "Please run with sudo"
    exit 1
fi

make || exit 1
mkdir -p eval/results

for MODE in $MODES; do
    echo ""
    echo "================ $MODE ================"
    mn -c > /dev/null 2>&1
    python3 mininet/topo_bottleneck.py --run $MODE --duration $DURATION
done
mn -c > /dev/null 2>&1

# Results were written as root; give them back to the normal user
if [ -n "$SUDO_USER" ]; then
    chown -R "$SUDO_USER" eval/results
fi

echo ""
python3 eval/parse_metrics.py --input eval/results
python3 eval/plot_results.py --input eval/results || \
    echo "Plotting failed (matplotlib missing for root?). Run without sudo: python3 eval/plot_results.py"
