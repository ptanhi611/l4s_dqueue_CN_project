#!/bin/bash
# FINAL experiment suite for the report.
#
# Runs, in this order:
#   1. normal scenario, all modes, REPEATS times   (label final_normal_r1, r2, ...)
#   2. attack scenario, all modes, REPEATS times   (label final_attack_r1, r2, ...)
#   3. attack scenario with the starvation breaker switched off (label final_breaker_off)
# Every run is archived in experiments/<date_time>_<label>/ (as usual), then
# eval/make_final.py builds results/final/ (tables with mean +- std, figures, README).
#
# Usage (from the repo root):  sudo ./eval/run_final.sh [repeats]
#   repeats = 3 by default, about 35 minutes in total

REPEATS=${1:-3}

if [ "$EUID" -ne 0 ]; then
    echo "Please run with sudo"
    exit 1
fi

NORMAL_MODES="fifo dualq dualq_qp dualq_qp_police fq_codel dualpi2"
ATTACK_MODES="dualq_basic dualq dualq_qp dualq_qp_police fq_codel dualpi2"

START=$(date +%s)

for i in $(seq 1 $REPEATS); do
    echo ""
    echo "######## Repeat $i of $REPEATS: normal ########"
    ./eval/traffic_profile.sh final_normal_r$i normal 30 "$NORMAL_MODES"

    echo ""
    echo "######## Repeat $i of $REPEATS: attack ########"
    ./eval/traffic_profile.sh final_attack_r$i attack 40 "$ATTACK_MODES"
done

echo ""
echo "######## Breaker test: attack with the starvation breaker OFF ########"
ROUTER_CONFIG=config/router_config_breaker_off.ini \
    ./eval/traffic_profile.sh final_breaker_off attack 40 "dualq dualq_qp_police"

echo ""
echo "######## Building results/final ########"
python3 eval/make_final.py || \
    echo "make_final.py failed (matplotlib missing for root?). Run without sudo: python3 eval/make_final.py"

if [ -n "$SUDO_USER" ]; then
    chown -R "$SUDO_USER" experiments results eval/results 2>/dev/null
fi

echo ""
echo "Done in $(( ($(date +%s) - START) / 60 )) minutes. Final tables and figures: results/final/"
