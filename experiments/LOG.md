# Experiment log (lab notebook)

Every run of `eval/traffic_profile.sh` is archived automatically in
`experiments/<date_time>_<label>/` (all CSV/JSON results, graphs, `console.txt`,
and the `router_config.ini` that was used). After each run, add a short entry
below: what changed, what we saw, and whether the run is valid. Keep failed runs
too. They explain our design decisions in the report.

## Common setup (unless an entry says otherwise)

| Item | Value |
|---|---|
| Topology | h1 (10.0.1.1) — r0 — h2 (10.0.2.1), Mininet, Linux 6.17+ |
| Bottleneck | 50 Mbps on r0 → h2. Our router: paced inside `l4s_router`. Kernel modes: HTB 50 Mbit + qdisc |
| Base RTT | 20 ms (netem delay on h2's egress, ACK path) |
| Buffer | 1.5 MB shared (our router); kernel defaults for fq_codel / dualpi2 |
| Classic flow | iperf3 TCP cubic, h1 → h2:5201, no ECN |
| L4S flow | iperf3 TCP DCTCP, h1 → h2:5202, ECT(0) rewritten to ECT(1) by `tc pedit` on h1 |
| Probes | UDP, 100 B every 10 ms, one ECT(1) (L4S queue), one Not-ECT (Classic queue) |
| AQM settings | target 15 ms, Tupdate 16 ms, α 0.16, β 3.2, k 2, L4S step 1 ms (RFC 9332 defaults) |

---

## Run 1: 2026-11-20 ~05:15, first full comparison

- **Code:** batches 1+2 (first working forwarder + automation)
- **Folder:** `2026-11-20_run1_first_full_comparison/` (console only, raw files were overwritten)
- **Status:** partly valid. FIFO and Classic-side results valid. L4S results invalid (bug).

| Mode | L4S probe p50/p95/p99 (ms) | Classic probe p50/p95/p99 (ms) | Tput C / L / total (Mbps) |
|---|---|---|---|
| fifo | 235.9 / 311.3 / 315.4 | 235.8 / 311.2 / 315.7 | 15.3 / 23.4 / 38.7 |
| dualq | 224.1 / 394.7 / 395.7 | 34.7 / 45.0 / 70.2 | 8.8 / 29.9 / 38.7 |
| fq_codel | 20.2 / 20.3 / 20.4 | 20.2 / 20.4 / 20.4 | 23.5 / 24.0 / 47.5 |
| dualpi2 (kernel) | 35.8 / 44.6 / 46.7 | 20.5 / 24.1 / 45.7 (15.8% loss) | 0.8 / 46.7 / 47.5 |

**Findings**
1. **Bufferbloat reproduced.** FIFO p95 311 ms matches the time to drain a full buffer: 1.5 MB × 8 / 39 Mbps ≈ 310 ms.
2. **PI2 Classic control works.** In dualq, the Classic probe p50 is 34.7 ms = 20 ms base + 15 ms target.
3. **Scheduler works as designed.** Classic got ~23% of the link when both queues were busy, close to the expected ~25% from the 4500 B L4S quantum.
4. **Bug: the L4S flow ignored marks.** ECN was not negotiated, so CE marks had no effect and the L4S queue filled the whole buffer. Check: 1.5 MB / (0.77 × 39 Mbps) ≈ 400 ms, and we measured 395 ms. Cause, found in Run 2: DCTCP sends its SYN as ECT(0), and h2's cubic listener refuses ECN for an ECN-marked SYN.
5. **Accidental "attack" experiment.** With the same unresponsive L4S flow, kernel dualpi2 kept the L4S delay bounded (overload handling) but starved Classic (0.8 Mbps, 15.8% probe loss). Our router (no overload handling yet) filled its buffer. **Neither handles an unresponsive L4S flow well.** This is the motivation for queue protection (Phase 3).
6. **Bug: our router reached only ~39 of ~47.5 Mbps.** Pacing restarted its schedule from "now" after every slightly late wakeup. An isolated timing test showed 35.7 Mbps with the old logic and 49.9 Mbps with the fix.

**Use in report:** bufferbloat baseline numbers; motivation for queue protection (finding 5); debugging and validation story.

---

## Run 2: 2026-11-20 ~05:45, after the pacing fix

- **Code:** batches 1+2+3 (pacing fix, ECN check, queue-delay metric)
- **Folder:** `2026-11-20_run2_pacing_fixed_classic_only/`
- **Status:** valid as a **Classic-traffic validation** run. The L4S bulk flow was not L4S (bug), so all bulk traffic was Classic.

| Mode | L4S probe p50/p99 (ms) | Classic probe p50/p95/p99 (ms) | Classic queue mean/p99 (ms) | Tput C / L / total |
|---|---|---|---|---|
| fifo | 194.7 / 259.9 | 194.5 / 255.9 / 259.9 | 135.7 / 239.8 (single queue) | 18.9 / 28.6 / 47.5 |
| dualq (ours) | 20.2 / 20.3 | **34.1 / 40.4 / 41.7** | **10.6 / 20.9** | **24.8 / 22.7 / 47.5** |
| fq_codel | 20.2 / 20.5 | 20.2 / 20.3 / 20.4 | n/a | 24.4 / 22.3 / 46.8 |
| dualpi2 (kernel) | 20.2 / 20.4 | **34.2 / 40.0 / 42.7** | **11.9 / 22.0** | **24.9 / 22.6 / 47.5** |

**Findings**
1. **Pacing fix confirmed.** Our router now carries 47.5 Mbps, the same as the kernel modes. Run 1 gave 38.7 Mbps.
2. **Bufferbloat:** FIFO p99 260 ms ≈ full buffer at 47.5 Mbps (1.5 MB × 8 / 47.5 Mbps ≈ 253 ms).
3. **ECN still not negotiated.** `sysctl net.ipv4.tcp_congestion_control=dctcp` failed inside the Mininet host with "Operation not permitted". Linux only allows a non-default congestion control inside a namespace if it is on the *allowed* list, which can only be changed from the main namespace. So DCTCP fell back to `dctcp-reno`, sent Not-ECT, and the "L4S" flow went to the Classic queue. Evidence: kernel dualpi2 `pkts_in_l 3000` = exactly the number of L4S probe packets; our router's L4S queue delay was 0.
4. **Our Classic path matches Linux `sch_dualpi2`.** With only Classic bulk traffic, our dualq and the kernel's dualpi2 give almost the same numbers: Classic probe p50 34.1 vs 34.2 ms, p99 41.7 vs 42.7 ms, Classic queue mean 10.6 vs 11.9 ms, throughput split 24.8/22.7 vs 24.9/22.6 Mbps.
5. **Sparse L4S traffic sees no queuing** (probe = 20.2 ms = base RTT) in both dual-queue implementations, like fq_codel.

**Use in report:** "Validation against Linux sch_dualpi2 (Classic traffic)" table (finding 4); pacing before/after (finding 1).

---

## Sim 1: 2026-11-20, offline attack simulation (no Mininet)

- **Code:** batch 4 classes (DualQueue, PI2AQM, DualQScheduler, QueueProtection) with the same decision logic as `main.cpp`
- **Folder:** `2026-11-20_sim1_offline_attack_logic/` (`sim_attack.cpp` + `output.txt`, build command inside)
- **Traffic:** unresponsive 60 Mbps ECT(1) UDP flood + unresponsive 10 Mbps Not-ECT Classic stream + 100 B ECT(1) probe every 10 ms, 50 Mbps link, 6 s per mode (first 2 s skipped). No TCP, so no flow reacts to marks or drops.
- **Status:** valid as a logic check of overload handling and queue protection. Not a replacement for the Mininet runs.

| Mode | L4S probe queue delay | L4S queue | Classic queue | Flood / Classic tput (Mbps) | p_C |
|---|---|---|---|---|---|
| dualq_basic | **259 ms** | 259 ms | 0.0 ms | 46.1 / 3.6 | 0.00 |
| dualq (overload) | **15.5 ms** | 14.8 ms | 0.0 ms | 42.8 / 7.1 | 0.30 |
| dualq_qp | **0.24 ms** | 0.21 ms | 16.0 ms | 42.6 / 7.2 | 0.28 |

**Findings**
1. **Without overload handling**, an unresponsive ECT(1) flow fills the whole shared buffer (259 ms) and Classic packets get tail-dropped (3.6 of 10 Mbps).
2. **RFC overload handling bounds the L4S delay at about the 15 ms target**, the same behaviour as kernel dualpi2 in Run 1 (~15 ms queue). But low latency for L4S is lost: L4S is now as slow as Classic.
3. **Queue protection restores L4S latency (0.24 ms)** by moving the flood to the Classic queue, where the AQM holds it at the 15 ms target.
4. **Honest limitation:** the flood still competes with Classic traffic inside the Classic queue (Classic stream 7.2 of 10 Mbps). Queue protection protects the L4S queue; it does not give per-flow fairness. Only fq_codel isolates every flow. Mention this in the report as a trade-off: per-flow state for the whole link vs a small per-flow check only on the L4S queue.

---

## Run 3: 2026-11-20 06:10, normal_v1

- **Code:** batch 4, commit 7394da9 (DCTCP allowed-list fix, overload handling, queue protection). Kernel 7.0.0-43
- **Folder:** `2026-11-20_0610_normal_v1/`
- **Scenario / modes / duration:** normal, fifo dualq dualq_qp fq_codel dualpi2, 30 s
- **Status:** valid as a **repeat of the Classic-traffic validation** (reproduces Run 2). The L4S bulk flow was still not L4S.

| Mode | L4S probe p50/p99/max (ms) | Classic probe p50/p95/p99 (ms) | Classic queue mean/p99 (ms) | Tput C / L / total |
|---|---|---|---|---|
| fifo | 180.1 / 259.9 / 260.2 | 180.2 / 258.7 / 260.0 | 133.5 / 239.5 (single queue) | 17.3 / 30.2 / 47.5 |
| dualq (ours) | 20.2 / 20.3 / **86.0** | 34.6 / 40.8 / 42.0 | 10.9 / 21.6 | 24.9 / 22.5 / 47.4 |
| dualq_qp (ours) | 20.2 / 20.4 / **142.6** | 33.7 / 40.4 / 42.8 | 10.3 / 22.1 | 24.0 / 22.7 / 46.7 |
| fq_codel | 20.2 / 20.5 / 20.6 | 20.2 / 20.4 / 20.5 | n/a | 24.4 / 22.5 / 46.9 |
| dualpi2 (kernel) | 20.2 / 20.4 / 20.5 | 34.4 / 40.6 / 45.7 | 12.3 / 21.4 | 27.0 / 20.2 / 47.2 |

**Findings**
1. **ECN still not negotiated**, even though h2 now really uses DCTCP (`h2: default congestion control = dctcp`). So the cubic-listener explanation was not the (whole) cause. New hypothesis: Linux 7.0's newer ECN negotiation (AccECN-related changes) treats our rewrite of the **SYN's** ECN bits as path tampering and disables ECN. Checked next with `--debug-ecn`.
2. **Classic validation reproduced.** Classic probe p50 34.6 (ours) vs 34.4 ms (kernel); Classic queue mean 10.9 vs 12.3 ms; total throughput 47.4 vs 47.2 Mbps. Run 2 gave the same picture, so the result is repeatable.
3. **Queue protection causes no harm in normal traffic.** dualq_qp ≈ dualq and moved 0 packets. Caveat: only the probe used the L4S queue, so it is not yet tested against a real DCTCP flow.
4. **Startup latency spike in our router only.** The L4S probe reached 86 ms (dualq) and 143 ms (dualq_qp) once, about 0.3 s after start; kernel dualpi2 never went above 20.5 ms. Likely cause: our **starvation breaker** (Classic head waits > 50 ms, so Classic gets strict priority and L4S waits) firing during the Classic slow-start overshoot. The kernel uses credit-based weighted round robin (classic_protection 10%) without a time-based breaker.

**Use in report:** repeatability of the Classic validation; scheduler design discussion (finding 4).

---

## Run 4: 2026-11-20 06:13, attack_v1

- **Code:** same as Run 3 (commit 7394da9)
- **Folder:** `2026-11-20_0613_attack_v1/`
- **Scenario / modes / duration:** attack (60 Mbps ECT(1) UDP flood, t = 10–30 s), dualq_basic dualq dualq_qp fq_codel dualpi2, 40 s
- **Status:** valid for **the flood vs the L4S queue** (the flood really was ECT(1)). The "L4S" TCP flow was Not-ECT (same ECN problem), so it was a second Classic flow.

During the attack (steady state, 2 s after the flood starts):

| Mode | L4S probe p50/p99 (ms) | L4S probe loss (whole run) | Classic probe p50/p99 (ms) | Tput Classic / "L4S" / flood (Mbps) |
|---|---|---|---|---|
| dualq_basic | **260.5 / 262.7** | 5.8% | 20.2 / 20.3 | 0.0 / 0.0 / 48.3 |
| dualq (overload, ours) | **35.2 / 38.1** | 10.2% | 20.2 / 21.5 | 0.4 / 0.0 / 47.9 |
| dualq_qp (ours) | **20.2 / 22.3** | **1.1%** | 35.0 / 46.1 | 0.4 / 0.6 / 47.7 |
| fq_codel | 20.2 / 20.5 | 0.0% | 20.2 / 20.5 | 16.0 / 15.1 / 16.9 |
| dualpi2 (kernel) | **35.2 / 37.9** | 10.8% | 20.4 / 24.6 | 0.4 / 0.5 / 47.8 |

**Findings**
1. **Without overload handling, the flood fills the shared buffer.** L4S probe 260 ms ≈ 1.5 MB × 8 / 48 Mbps ≈ 250 ms, with 20,992 tail drops.
2. **Our overload handling reproduces Linux `sch_dualpi2` almost exactly under attack:** L4S probe 35.2/38.1 vs 35.2/37.9 ms, flood 47.9 vs 47.8 Mbps, probe loss 10.2% vs 10.8%. The L4S delay is bounded at about the 15 ms Classic target, so low latency is lost but the buffer does not fill. (The offline Sim 1 predicted 15.5 ms queue delay; here 35 − 20 = 15 ms. It matches.)
3. **Queue protection keeps the L4S queue low-latency during the attack.** L4S probe 20.2/22.3 ms (no queuing) and 1.1% loss, versus 35 ms and ~10% loss for both overload-only implementations. The flood was sanctioned 20 times in 20 s (once per 1 s sanction period), 105,944 packets were moved to Classic, and no other flow was touched.
4. **Two visible side effects in `latency_timeseries.png` (dualq_qp):**
   - a large spike (~220 ms) right at attack onset. Likely the starvation breaker again: the flood suddenly fills the Classic queue, Classic waits > 50 ms, and L4S gets no service.
   - a "comb" of small spikes (to ~25–28 ms RTT) every 1 s: when the sanction expires, the flood re-enters the L4S queue for up to 30 KB before it is sanctioned again.
5. **Honest limitation: TCP throughput is not protected by any dual-queue variant.** In all dual-queue modes, including the kernel's, both TCP flows dropped to ≤ 0.6 Mbps while the unresponsive flood took ~48 Mbps. Only fq_codel shared the link fairly (16 / 15 / 17 Mbps) with no extra delay. Queue protection protects the **latency of the L4S queue**, not per-flow fairness.

**Use in report:** validation of overload handling against the kernel (finding 2); the queue-protection result (finding 3, `latency_timeseries.png`); trade-off and limitation discussion (findings 4–5).

**To do:** (a) fix ECN negotiation and repeat both runs with a real L4S flow; (b) test the scheduler without the 50 ms breaker (findings 4 of Runs 3 and 4); (c) consider shortening the sanction or rechecking it more gradually to remove the comb.

---

## Entry template

```
## Run N: <date time>, <label>
- Code: <git commit / what changed>
- Folder: <experiments/...>
- Scenario / modes / duration:
- Status: valid / partly valid / invalid (why)
- Results: <table or key numbers>
- Findings: <numbered, with a number to back each one>
- Use in report: <which section / graph>
```
