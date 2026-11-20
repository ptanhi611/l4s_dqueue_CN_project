## Attack scenario (during the flood)

Mean ± standard deviation over 3 runs. Measured from 2 s after the flood starts until it stops.

| Mode | L4S probe p50 (ms) | L4S probe p99 (ms) | Classic probe p50 (ms) | Classic probe p99 (ms) | Classic tput (Mbps) | L4S tput (Mbps) | Flood tput (Mbps) | DCTCP sanctions | DCTCP+ECN ok |
|---|---|---|---|---|---|---|---|---|---|
| DualQ basic | 260.5 ± 0.0 | 262.0 ± 0.4 | 20.2 ± 0.0 | 20.4 ± 0.0 | 0.0 ± 0.0 | 0.0 ± 0.0 | 48.3 ± 0.0 | n/a | 1.00 ± 0.00 |
| DualQ | 35.2 ± 0.0 | 38.0 ± 0.2 | 20.2 ± 0.0 | 21.4 ± 0.4 | 0.4 ± 0.2 | 0.2 ± 0.1 | 47.7 ± 0.1 | n/a | 1.00 ± 0.00 |
| DualQ+QP | 20.2 ± 0.0 | 22.0 ± 0.2 | 35.1 ± 0.2 | 46.7 ± 0.4 | 0.2 ± 0.0 | 0.9 ± 0.1 | 47.1 ± 0.0 | 0.7 ± 0.6 | 1.00 ± 0.00 |
| DualQ+QP+limit | 20.2 ± 0.0 | 24.4 ± 0.6 | 28.7 ± 1.3 | 45.5 ± 2.1 | 27.5 ± 1.5 | 13.7 ± 1.4 | 5.3 ± 0.0 | 6.3 ± 0.6 | 1.00 ± 0.00 |
| fq_codel (kernel) | 20.2 ± 0.0 | 20.4 ± 0.0 | 20.2 ± 0.0 | 20.4 ± 0.0 | 15.7 ± 0.1 | 15.9 ± 0.0 | 16.2 ± 0.0 | n/a | 1.00 ± 0.00 |
| dualpi2 (kernel) | 35.3 ± 0.0 | 37.9 ± 0.2 | 20.4 ± 0.0 | 23.8 ± 0.6 | 0.3 ± 0.1 | 0.3 ± 0.1 | 47.8 ± 0.0 | n/a | 1.00 ± 0.00 |
