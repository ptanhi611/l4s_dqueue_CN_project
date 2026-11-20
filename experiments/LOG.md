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
| L4S flow | iperf3 TCP DCTCP, h1 → h2:5202, ECT(0) rewritten to ECT(1) by `tc pedit` on h1. **From Run 5 on:** sent to h2's second address 10.0.2.2, whose route on h1 has `congctl dctcp` (DCTCP active from the SYN on); the SYN itself is not rewritten |
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

## Debug 1: 2026-11-20 06:30, ECN handshake capture (`--debug-ecn`)

- **Code:** batch 5 diagnostic, plain kernel forwarding on r0 (no AQM)
- **Folder:** `2026-11-20_0630_debug1_ecn_handshake/ecn_debug.txt`
- **Question:** why does the DCTCP flow never get ECN (Runs 1–4)?

| Test | SYN from h1 (seen at h2) | SYN-ACK from h2 | Result (ss on h1) |
|---|---|---|---|
| A: `iperf3 -C dctcp`, with our tc rule | `Flags [S]`, tos 0x0 (no ECN request) | `[S.]` | `dctcp-reno`, `dctcp:fallback_mode` |
| B: `iperf3 -C dctcp`, without tc rule | `Flags [S]`, tos 0x0 (no ECN request) | `[S.]` | `dctcp-reno`, `dctcp:fallback_mode` |
| C: cubic, `tcp_ecn=1`, without tc rule | `Flags [SEW]` (asks for ECN) | `[S.E]` (ECN accepted) | cubic (ECN negotiated) |

**Findings**
1. **Root cause:** the data connection's SYN did not ask for ECN at all (no ECE/CWR flags). DCTCP was switched on only *after* the handshake (iperf3 `-C`), found no ECN, and fell back to Reno. A and B are identical, so **our tc rule was not the cause**. That hypothesis is rejected.
2. ECN negotiation through the path works when the SYN asks for it (test C).
3. Earlier hypotheses, for the record: (Run 1) the rule marked the SYN; (Run 2) h2's cubic listener refused an ECN-marked SYN; (Run 3) kernel 7.0 handshake changes. These were plausible but not the root cause. Measuring the handshake directly found the real cause in one run.
4. **Tooling bug found:** `ss` on this system does not print the `ecn`, `ts` or `sack` flags, so our "DCTCP+ECN" check could never succeed. Fixed: the check now looks for `dctcp:(ce_state...` (working) vs `dctcp-reno` / `fallback_mode` (failed).

**Fix (batch 6):** h2 gets a second address 10.0.2.2. h1's route to it uses `congctl dctcp`, so DCTCP is active from the SYN on. The L4S flow uses 10.0.2.2 and the Classic flow keeps 10.0.2.1 (cubic, unchanged). The tc rule no longer touches the SYN.

**Use in report:** methodology and debugging section: why we measured the handshake instead of guessing, and the lesson about congestion-control selection timing.

---

## Debug 2: 2026-11-20 06:43, ECN check after the fix

- **Code:** batch 6 (commit 92cf4db)
- **Folder:** `2026-11-20_0643_ecn_debug/` (written by `--debug-ecn`)
- **Result:**
  - Test A (old way, `iperf3 -C dctcp`): SYN `[S]`, `dctcp-reno`. Still fails, as expected.
  - **Test B (new way, route `congctl dctcp` to 10.0.2.2): SYN `[SEW]` → SYN-ACK `[S.E]`, ss shows `dctcp:(ce_state:0,alpha:17,ab_ecn:0,ab_tot:3282616)`.** DCTCP with ECN works.
- **Use in report:** before/after evidence for the ECN negotiation fix.

---

## Run 5: 2026-11-20 06:43, normal_v2 (first run with a real L4S flow)

- **Code:** batch 6, commit 92cf4db, kernel 7.0.0-43
- **Folder:** `2026-11-20_0643_normal_v2/`
- **Scenario / modes / duration:** normal, fifo dualq dualq_qp fq_codel dualpi2, 30 s
- **Status:** **valid.** All modes `OK: L4S flow is using DCTCP with ECN`.

| Mode | L4S probe p50/p99/max (ms) | Classic probe p50/p99 (ms) | L4S queue mean/p99 (ms) | Classic queue mean/p99 (ms) | Tput C / L / total |
|---|---|---|---|---|---|
| fifo | 196.7 / 259.9 / 260.2 | 196.8 / 259.9 | – | 136.9 / 239.4 (single queue) | 19.0 / 28.5 / 47.5 |
| **dualq (ours)** | **20.3 / 21.2 / 23.7** | 34.2 / 40.5 | **0.09 / 0.70** | 10.1 / 19.8 | **23.0 / 24.5 / 47.5** |
| dualq_qp (ours) | 20.3 / 21.2 / 21.6 | 34.4 / 40.4 | 0.10 / 0.65 | 10.4 / 19.9 | 22.0 / 25.5 / 47.5 |
| fq_codel | 20.2 / 20.4 / 20.6 | 20.2 / 20.4 | n/a | n/a | 23.5 / 24.0 / 47.5 |
| **dualpi2 (kernel)** | **20.4 / 21.2 / 21.6** | 34.0 / 40.8 | **0.18 / 0.83** | 11.3 / 19.7 | **21.7 / 25.7 / 47.5** |

**Findings**
1. **Main result: our router reproduces RFC 9332 DualQ Coupled AQM with a real L4S flow, matching Linux `sch_dualpi2`.**
   - The L4S queue stays below 1 ms (mean 0.09 ms, p99 0.70 ms; kernel 0.18 / 0.83) while the DCTCP flow carries half the link.
   - The Classic queue is held at the 15 ms target region (mean 10.1 ms; kernel 11.3 ms).
2. **Coupling gives rate fairness.** DCTCP and cubic split the link about evenly (24.5 vs 23.0 Mbps; kernel 25.7 vs 21.7), although DCTCP sees far more congestion signals (2,737 CE marks vs 27 Classic drops). This is the p_C = (p_CL / k)² law working.
3. **Bufferbloat baseline** reproduced again (FIFO p99 260 ms).
4. **Queue protection: no false positives with a real DCTCP flow.** dualq_qp moved 0 packets and behaved like dualq.
5. **No startup spikes this time** (max 23.7 ms vs 86–143 ms in Run 3). In Run 3 both bulk flows were Classic, so the Classic slow-start overshoot was larger. This supports the starvation-breaker explanation; the breaker is still tested separately.
6. **Bulk-flow RTT: where L4S beats fq_codel.** The RTT measured by each sending TCP stack (iperf3 `mean_rtt`, computed afterwards from this run's files):

   | Mode | Classic flow (cubic) | L4S flow (DCTCP) |
   |---|---|---|
   | fifo | 193.5 ms | 192.5 ms |
   | **dualq (ours)** | 32.6 ms | **20.5 ms** |
   | dualq_qp (ours) | 33.7 ms | 20.6 ms |
   | fq_codel | 23.3 ms | **28.0 ms** |
   | dualpi2 (kernel) | 33.6 ms | 20.6 ms |

   fq_codel gives ~20 ms to the sparse probes, but the **bulk DCTCP flow itself sees 28.0 ms** there (about 8 ms of queuing). In our DualQ and the kernel's dualpi2 it sees **20.5 ms (0.5 ms of queuing) at full share of the link**. That is the L4S promise: low latency for capacity-seeking flows, not only for sparse ones. Trade-off: Classic flows see ~33 ms under DualQ (15 ms target) vs 23 ms under fq_codel.

**Use in report:** headline result table (findings 1–2); validation against the kernel.

---

## Run 6: 2026-11-20 06:46, attack_v2 (real L4S flow + ECT(1) flood)

- **Code:** same as Run 5
- **Folder:** `2026-11-20_0646_attack_v2/`
- **Scenario / modes / duration:** attack (60 Mbps ECT(1) UDP flood, t = 10–30 s), dualq_basic dualq dualq_qp fq_codel dualpi2, 40 s
- **Status:** valid. All modes DCTCP + ECN OK.

During the attack (steady state, 2 s after the flood starts):

| Mode | L4S probe p50/p99 (ms) | L4S probe loss (whole run) | Classic probe p50/p99 (ms) | Tput Classic / L4S / flood (Mbps) |
|---|---|---|---|---|
| dualq_basic | 260.4 / 262.2 | 0.1% | 20.2 / 20.3 | 0.0 / 0.0 / 48.3 |
| dualq (ours) | **35.2 / 38.0** | 11.4% | 20.2 / 21.1 | 0.4 / 0.2 / 47.7 |
| dualq_qp (ours) | **20.2 / 22.3** | **1.0%** | 35.0 / 46.4 | 0.2 / **0.9** / 47.2 |
| fq_codel | 20.2 / 20.4 | 0.0% | 20.2 / 20.4 | 15.7 / 15.9 / 16.2 |
| dualpi2 (kernel) | **35.2 / 37.9** | 10.4% | 20.4 / 23.5 | 0.4 / 0.2 / 47.9 |

**Findings**
1. **Overload handling matches the kernel again, now with a real L4S flow:** L4S probe 35.2/38.0 vs 35.2/37.9 ms; flood 47.7 vs 47.9 Mbps. This repeats Run 4.
2. **Queue protection keeps the L4S queue fast:** L4S probe 20.2/22.3 ms during the attack, L4S queue mean 0.75 ms over the run, probe loss 1.0% (vs ~11% without protection). The flood was sanctioned 20 times.
3. **New problem: the good DCTCP flow still starves (0.9 Mbps) with queue protection.** The sanctioned flood, now in the Classic queue, keeps the PI2 controller in **overload** (p_C ≥ 0.25). Then the coupled probability p_CL = k·p' ≥ 1, so **every L4S packet is CE-marked** and DCTCP shrinks to its minimum window. Sim 2 confirms this: dualq_qp is in overload 100% of the time. Moving the flood out of the L4S queue is not enough; it must also be **limited**. → Phase 3b: rate limit for sanctioned flows (mode `dualq_qp_police`).
4. **One false positive:** the DCTCP flow was sanctioned once (116 packets) right at attack onset. Likely cause: the flood suddenly fills the Classic queue, the 50 ms starvation breaker gives Classic strict priority, and DCTCP's backlog in the L4S queue grows past 30 KB. The same onset effect gives the ~145 ms probe spike in dualq and dualq_qp.
5. fq_codel again shares fairly (15.7 / 15.9 / 16.2 Mbps) with ~20 ms probes. This is the bar for the policing mode.

**Use in report:** overload validation (finding 1); the queue-protection latency result (finding 2); the coupling insight that motivates policing (finding 3). This is a genuine, non-obvious design lesson.

---

## Sim 2: 2026-11-20, offline attack simulation with policing

- **Folder:** `2026-11-20_sim2_offline_attack_policing/` (same setup as Sim 1, plus `dualq_qp_police` with a 5 Mbps limit, plus the share of time in overload)

| Mode | L4S probe queue delay | Classic queue | Flood / Classic tput (Mbps) | p_C | Time in overload |
|---|---|---|---|---|---|
| dualq_basic | 259 ms | 0.0 ms | 46.2 / 3.7 | 0.00 | 0% |
| dualq | 15.3 ms | 0.0 ms | 42.8 / 7.0 | 0.30 | 99% |
| dualq_qp | 0.20 ms | 16.4 ms | 42.7 / 7.0 | 0.29 | **100%** |
| **dualq_qp_police** | **0.07 ms** | **0.02 ms** | **5.7 / 10.0** | **0.00** | **0%** |

**Findings:** (1) this confirms Run 6 finding 3: with redirect-only queue protection the controller sits in overload all the time, which forces 100% L4S marking. (2) With the 5 Mbps limit the flood can no longer congest the Classic queue: no overload, p_C ≈ 0, and the Classic stream gets its full 10 Mbps. Prediction for Mininet: with `dualq_qp_police`, the DCTCP flow keeps its throughput during the attack.

---

## Final suite: `eval/run_final.sh` → `results/final/`

- Runs normal ×3 and attack ×3 (all modes, including `dualq_qp_police`), plus one attack run with the starvation breaker off (`config/router_config_breaker_off.ini`).
- `eval/make_final.py` builds `results/final/`: tables with mean ± std (`table_normal`, `table_attack`, `table_breaker`), error-bar figures (`fig_normal`, `fig_attack`, `fig_bulk_rtt`), copies of the repeat-1 graphs, and a README listing the source runs.
- **After running: add the key numbers and findings here** (policing result, breaker on/off, run-to-run spread).

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
