#!/bin/bash
# Runs the same experiment in several modes, one after another, makes the summary
# table and the graphs, and ARCHIVES everything in experiments/<date_time>_<label>/
# (results, graphs, full console output, settings), so no run is ever lost.
#
# Usage (from the repo root):
#   sudo ./eval/traffic_profile.sh <label> [normal|attack] [duration_s] ["mode list"]
#
# Examples:
#   sudo ./eval/traffic_profile.sh baseline_check
#   sudo ./eval/traffic_profile.sh attack_test attack 40
#   sudo ./eval/traffic_profile.sh qp_only attack 40 "dualq dualq_qp"
#
# Modes: fifo dualq_basic dualq dualq_qp (our router) | fq_codel dualpi2 (Linux kernel)

LABEL=$1
SCENARIO=${2:-normal}
DURATION=${3:-30}
MODES=$4

if [ "$EUID" -ne 0 ]; then
    echo "Please run with sudo"
    exit 1
fi
if [ -z "$LABEL" ]; then
    echo "Usage: sudo ./eval/traffic_profile.sh <label> [normal|attack] [duration_s] [\"mode list\"]"
    exit 1
fi

# Default modes for each scenario
if [ -z "$MODES" ]; then
    if [ "$SCENARIO" = "attack" ]; then
        MODES="dualq_basic dualq dualq_qp fq_codel dualpi2"
    else
        MODES="fifo dualq dualq_qp fq_codel dualpi2"
    fi
fi

# Safety: if eval/results holds a run that was never archived, keep it instead of deleting it
if [ -d eval/results ] && [ -n "$(ls -A eval/results 2>/dev/null)" ] && [ ! -f eval/results/.archived ]; then
    SAVED="experiments/$(date +%Y-%m-%d_%H%M)_unarchived_previous_results"
    mkdir -p experiments
    mv eval/results "$SAVED"
    echo "Moved old, unarchived results to $SAVED"
fi

RUN_DIR="experiments/$(date +%Y-%m-%d_%H%M)_${LABEL}"
mkdir -p "$RUN_DIR"

# Everything inside { } is printed AND saved to console.txt
{
    echo "Run: $RUN_DIR"
    echo "Scenario: $SCENARIO | duration: $DURATION s | modes: $MODES"
    echo "Git commit: $(git -c safe.directory='*' rev-parse --short HEAD 2>/dev/null)"
    echo "Kernel: $(uname -r)"

    make || exit 1
    rm -rf eval/results
    mkdir -p eval/results

    for MODE in $MODES; do
        echo ""
        echo "================ $MODE ================"
        mn -c > /dev/null 2>&1
        python3 mininet/topo_bottleneck.py --run $MODE --scenario $SCENARIO --duration $DURATION
    done
    mn -c > /dev/null 2>&1

    echo ""
    python3 eval/parse_metrics.py --input eval/results
    python3 eval/plot_results.py --input eval/results || \
        echo "Plotting failed (matplotlib missing for root?). Run without sudo: python3 eval/plot_results.py --input $RUN_DIR"
} 2>&1 | tee "$RUN_DIR/console.txt"

# Archive: copy all results and graphs, plus the config used
cp -r eval/results/* "$RUN_DIR/" 2>/dev/null
cp config/router_config.ini "$RUN_DIR/"
touch eval/results/.archived

# Results were written as root; give them back to the normal user
if [ -n "$SUDO_USER" ]; then
    chown -R "$SUDO_USER" experiments eval/results
fi

echo ""
echo "Archived in $RUN_DIR"
echo "Add a short entry for this run in experiments/LOG.md (what changed, what you saw)."
