#!/usr/bin/env python3
# Live view of the router for demos: reads the router's stats file while an
# experiment runs and redraws a small text dashboard every 0.5 s.
#
# Terminal 1:  sudo ./eval/traffic_profile.sh demo attack 40 "dualq_qp_police"
# Terminal 2:  python3 eval/live_view.py eval/results/dualq_qp_police/router_stats.csv
#
# Stop with Ctrl+C.

import csv
import os
import sys
import time

BAR_WIDTH = 50  # characters; 1 character = 1 ms of queue delay


def bar(value_ms):
    n = int(value_ms)
    if n > BAR_WIDTH:
        return '#' * BAR_WIDTH + '>'
    return '#' * n


def read_last_two(path):
    # Returns the last two data rows of the CSV as dicts (or fewer if the file is new)
    with open(path) as f:
        rows = list(csv.DictReader(f))
    return rows[-2:]


def main():
    if len(sys.argv) < 2:
        print('Usage: python3 eval/live_view.py <router_stats.csv>')
        return
    path = sys.argv[1]

    print('Waiting for', path, '...')
    while True:
        try:
            if not os.path.exists(path):
                time.sleep(0.5)
                continue
            rows = read_last_two(path)
            if len(rows) < 2:
                time.sleep(0.5)
                continue
            prev, last = rows[0], rows[1]

            dt = float(last['time_s']) - float(prev['time_s'])
            mbps = (float(last['sent_bytes']) - float(prev['sent_bytes'])) * 8 / dt / 1e6 if dt > 0 else 0
            c_delay = float(last['c_delay_ms'])
            l_delay = float(last['l_delay_ms'])
            overload = last.get('overload', '0') == '1'

            lines = []
            lines.append('L4S DualQ router - live view        (Ctrl+C to stop)')
            lines.append('file: %s' % path)
            lines.append('')
            lines.append('time %7.1f s        output %5.1f Mbps (link 50)' % (float(last['time_s']), mbps))
            lines.append('')
            lines.append('Queue delay (1 # = 1 ms)')
            lines.append('  L4S     %7.2f ms |%s' % (l_delay, bar(l_delay)))
            lines.append('  Classic %7.2f ms |%s' % (c_delay, bar(c_delay)))
            lines.append('')
            lines.append("PI2:  p' = %.3f   p_C (Classic drop) = %.3f   p_CL (L4S mark) = %.3f   %s" % (
                float(last['p_prime']), float(last['p_c']), float(last['p_l']),
                'OVERLOAD' if overload else ''))
            lines.append('')
            lines.append('Counters since start:')
            lines.append('  L4S CE marks %-9s Classic CE marks %-9s AQM drops %-9s tail drops %s' % (
                last['l_marks'], last['c_marks'], last['aqm_drops'], last['tail_drops']))
            if 'qp_redirects' in last:
                lines.append('  L4S overload drops %-9s queue protection moved %s packets to Classic' % (
                    last['l4s_drops'], last['qp_redirects']))

            # Clear the screen and draw
            sys.stdout.write('\033[2J\033[H' + '\n'.join(lines) + '\n')
            sys.stdout.flush()
            time.sleep(0.5)
        except KeyboardInterrupt:
            print()
            return
        except (OSError, ValueError, KeyError, ZeroDivisionError):
            # File being rewritten or a half-written line: just try again
            time.sleep(0.5)


if __name__ == '__main__':
    main()
