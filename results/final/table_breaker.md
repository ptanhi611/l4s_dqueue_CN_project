## Starvation breaker: on (50 ms) vs off

ON = mean ± std of the 3 final attack runs; OFF = 1 run(s) with `config/router_config_breaker_off.ini`. "Max" = worst L4S probe RTT over the whole run (the spike at attack onset).

| Mode | Breaker | L4S probe p99 during attack (ms) | L4S probe max, whole run (ms) | L4S tput (Mbps) | Classic tput (Mbps) | DCTCP sanctions |
|---|---|---|---|---|---|---|
| DualQ | on | 38.0 ± 0.2 | 148.1 ± 3.1 | 0.2 ± 0.1 | 0.4 ± 0.2 | n/a |
| DualQ | off | 38.0 ± 0.0 | 149.6 ± 0.0 | 0.2 ± 0.0 | 0.4 ± 0.0 | n/a |
| DualQ+QP+limit | on | 24.4 ± 0.6 | 29.2 ± 0.3 | 13.7 ± 1.4 | 27.5 ± 1.5 | 6.3 ± 0.6 |
| DualQ+QP+limit | off | 25.4 ± 0.0 | 28.6 ± 0.0 | 17.4 ± 0.0 | 24.5 ± 0.0 | 5.0 ± 0.0 |
