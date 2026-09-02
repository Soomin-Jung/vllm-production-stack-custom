# Qwen3.6-27B P/D Benchmark Matrix

> 범위: Prefill-first 성능 최적화
>
> 핵심 축:
>
> - A = max-num-batched-tokens (MBT)
> - B = MTP / speculative decoding
> - C = CUDA Graph mode
>
> 목표: 단일 축의 주효과(main effect)와 A×B, A×C, B×C, A×B×C 상호작용(interaction)을 분리해서 해석한다.

---

## 1. Prefill benchmark 고정 조건

아래 값은 Prefill matrix 동안 고정한다.

~~~text
P/D topology        : P1D1
Prefill TP / PP     : TP1 / PP1
Decode TP / PP      : 별도 baseline 고정
max-num-seqs        : 8
chunked prefill     : ON
prefix cache        : OFF
async scheduling    : OFF
KV cache dtype      : fp8
gpu-memory-util     : 동일 값 고정
max-model-len       : 동일 값 고정
input/output set    : 동일 benchmark corpus 고정
concurrency         : 동일 값 고정
CUDA graph sizes    : auto (mode 실험 동안 수동 capture list 금지)
~~~

MBT/MTP/CUDA Graph 이외의 설정을 동시에 바꾸지 않는다.

### Primary metrics

~~~text
TTFT p50 / p90 / p95
Prefill phase latency p50 / p90 / p95
Prompt token throughput
Request throughput
~~~

### Secondary metrics

~~~text
GPU Util
SM Active
Tensor Core Active
DRAM Active
CPU utilization
KV cache usage
available KV blocks / startup KV capacity
CUDA Graph capture startup time / memory
Mooncake KV transfer success / latency
~~~

### MTP-specific metrics

~~~text
draft tokens/s
accepted tokens/s
draft acceptance rate
mean speculative acceptance length
~~~

---

## 2. Factor levels

### A — max-num-batched-tokens

| ID | MBT | 의미 |
|---|---:|---|
| A1 | 8,192 | 보수적 baseline / 작은 prefill chunk |
| A2 | 16,384 | 중간 기준점 |
| A3 | 32,768 | 긴 prompt throughput 우선 후보 |
| A4 | 65,536 | optional; A3가 유의미하게 개선되고 KV/graph memory 여유가 있을 때만 |

기본 staged matrix는 A1/A2/A3만 사용한다.

### B — MTP

| ID | 설정 | 의미 |
|---|---|---|
| B0 | OFF | no-spec baseline |
| B1 | K=1 | 가장 낮은 MTP overhead |
| B2 | K=2 | 중간점; 필요 시 추가 |
| B3 | K=3 | 공격적 MTP 후보 |

초기 reduced matrix는 B0/B1/B3를 사용한다.

B2는 다음 조건 중 하나일 때 추가한다.

~~~text
B1 < optimum < B3 로 보이는 경우
B3 acceptance가 급락하지만 B1은 유의미한 이득이 있는 경우
Decode 측 K2와 P/D pair consistency를 맞춰야 하는 경우
~~~

### C — CUDA Graph

| ID | mode | Prefill 의미 |
|---|---|---|
| C0 | NONE | eager control |
| C1 | PIECEWISE | Prefill/mixed batch용 우선 후보 |

Prefill-only matrix에서는 `FULL_DECODE_ONLY`를 사용하지 않는다.

`FULL_AND_PIECEWISE`는 Prefill의 주효과를 보는 1차 matrix에는 넣지 않는다.
Decode CUDA Graph 비교 단계에서 별도로 다룬다.

---

## 3. Anchor baseline

모든 interaction 비교의 중심점은 아래로 고정한다.

~~~text
P-BASE
A2 = MBT 16K
B0 = MTP OFF
C1 = PIECEWISE
~~~

이 기준점을 모든 실험 묶음에서 반복 사용해 run-to-run drift를 확인한다.

권장 반복:

~~~text
각 anchor / 최종 후보: 최소 3회
나머지 screening run: 최소 1회
~~~

차이가 실제 튜닝 효과인지 판단할 때는 절대 % cutoff보다 반복 run의 분산/변동폭을 먼저 본다.

---

# 4. (A) MBT only

목적:

> MTP와 CUDA Graph 조건을 고정한 상태에서 MBT만 바꿨을 때 Prefill compute/queue/KV trade-off를 본다.

고정:

~~~text
B = B0 (MTP OFF)
C = C1 (PIECEWISE)
~~~

| Test ID | MBT | MTP | CUDA Graph | 핵심 관찰 |
|---|---:|---|---|---|
| P-A1 | 8K | OFF | PIECEWISE | TTFT control / 작은 chunk |
| P-A2 | 16K | OFF | PIECEWISE | **anchor** |
| P-A3 | 32K | OFF | PIECEWISE | prompt tok/s 증가 vs TTFT/KV pressure |
| P-A4 | 64K | OFF | PIECEWISE | optional; memory 여유 시 |

판정 포인트:

~~~text
MBT ↑
  -> prompt tok/s ↑ ?
  -> TTFT p95 ↓ 또는 ↑ ?
  -> queue p95 변화?
  -> GPU SM/Tensor/DRAM active 변화?
  -> KV capacity 감소?
~~~

---

# 5. (B) MTP only

목적:

> MBT와 CUDA Graph를 고정하고 Prefill MTP가 실제 이득인지, 단순 overhead인지 본다.

고정:

~~~text
A = A2 (16K)
C = C1 (PIECEWISE)
~~~

| Test ID | MBT | MTP | CUDA Graph | 핵심 관찰 |
|---|---:|---|---|---|
| P-B0 | 16K | OFF | PIECEWISE | **anchor** |
| P-B1 | 16K | K1 | PIECEWISE | 최소 MTP overhead |
| P-B3 | 16K | K3 | PIECEWISE | 공격적 후보 |
| P-B2 | 16K | K2 | PIECEWISE | 필요 시 interpolation |

Prefill에서 MTP는 Decode처럼 직접적인 generation speedup을 기대하지 않는다.

주요 질문:

~~~text
MTP-aware model/cache path를 켰을 때
Prefill TTFT / prompt tok/s / CPU overhead가 나빠지는가?

P/D correctness / KV transfer layout은 정상인가?
~~~

---

# 6. (C) CUDA Graph mode only

목적:

> 동일 workload에서 Prefill PIECEWISE graph가 eager 대비 실제 이득을 주는지 본다.

고정:

~~~text
A = A2 (16K)
B = B0 (MTP OFF)
~~~

| Test ID | MBT | MTP | CUDA Graph | 핵심 관찰 |
|---|---:|---|---|---|
| P-C0 | 16K | OFF | NONE | eager control |
| P-C1 | 16K | OFF | PIECEWISE | **anchor** |

판정 포인트:

~~~text
TTFT
prompt tok/s
CPU util
GPU SM Active
CUDA Graph memory
startup/capture time
~~~

PIECEWISE가 유리하더라도 startup memory/capture overhead까지 같이 기록한다.

---

# 7. (A × B) MBT + MTP

목적:

> MTP overhead/benefit이 MBT에 따라 달라지는지 확인한다.

고정:

~~~text
C = C1 (PIECEWISE)
~~~

### Reduced matrix

| Test ID | MBT | MTP | CUDA Graph |
|---|---:|---|---|
| P-AB-01 | 8K | OFF | PIECEWISE |
| P-AB-02 | 8K | K1 | PIECEWISE |
| P-AB-03 | 8K | K3 | PIECEWISE |
| P-AB-04 | 16K | OFF | PIECEWISE |
| P-AB-05 | 16K | K1 | PIECEWISE |
| P-AB-06 | 16K | K3 | PIECEWISE |
| P-AB-07 | 32K | OFF | PIECEWISE |
| P-AB-08 | 32K | K1 | PIECEWISE |
| P-AB-09 | 32K | K3 | PIECEWISE |

관찰:

~~~text
MBT가 커질수록 MTP overhead가 희석되는가?
MTP ON에서 CPU/GDN dispatch 병목이 커지는가?
MTP ON일 때 최적 MBT가 OFF와 달라지는가?
~~~

B2(K2)는 최적점이 B1과 B3 사이에 있다고 보일 때만 추가한다.

---

# 8. (A × C) MBT + CUDA Graph

목적:

> CUDA Graph PIECEWISE 효과가 MBT에 따라 달라지는지 확인한다.

고정:

~~~text
B = B0 (MTP OFF)
~~~

| Test ID | MBT | MTP | CUDA Graph |
|---|---:|---|---|
| P-AC-01 | 8K | OFF | NONE |
| P-AC-02 | 8K | OFF | PIECEWISE |
| P-AC-03 | 16K | OFF | NONE |
| P-AC-04 | 16K | OFF | PIECEWISE |
| P-AC-05 | 32K | OFF | NONE |
| P-AC-06 | 32K | OFF | PIECEWISE |

관찰:

~~~text
작은 MBT에서는 graph launch/CPU overhead 절감이 큰가?
큰 MBT에서는 kernel compute가 지배해 graph 효과가 줄어드는가?
PIECEWISE가 KV capacity/startup memory를 얼마나 추가로 사용하나?
~~~

---

# 9. (B × C) MTP + CUDA Graph

목적:

> MTP ON/OFF에 따라 Prefill PIECEWISE graph 효과가 달라지는지 확인한다.

고정:

~~~text
A = A2 (16K)
~~~

| Test ID | MBT | MTP | CUDA Graph |
|---|---:|---|---|
| P-BC-01 | 16K | OFF | NONE |
| P-BC-02 | 16K | OFF | PIECEWISE |
| P-BC-03 | 16K | K1 | NONE |
| P-BC-04 | 16K | K1 | PIECEWISE |
| P-BC-05 | 16K | K3 | NONE |
| P-BC-06 | 16K | K3 | PIECEWISE |

핵심:

~~~text
MTP OFF:
  NONE vs PIECEWISE

MTP K1:
  NONE vs PIECEWISE

MTP K3:
  NONE vs PIECEWISE
~~~

이 결과는 Qwen hybrid/GDN + MTP에서 graph path가 CPU/GPU 실행에 어떤 영향을 주는지 판단하는 핵심 interaction이다.

---

# 10. (A × B × C) Full interaction

Reduced factor set:

~~~text
A = {8K, 16K, 32K}
B = {OFF, K1, K3}
C = {NONE, PIECEWISE}
~~~

따라서 full factorial은:

~~~text
3 × 3 × 2 = 18 unique configurations
~~~

| Test ID | MBT | MTP | CUDA Graph |
|---|---:|---|---|
| P-ABC-01 | 8K | OFF | NONE |
| P-ABC-02 | 8K | OFF | PIECEWISE |
| P-ABC-03 | 8K | K1 | NONE |
| P-ABC-04 | 8K | K1 | PIECEWISE |
| P-ABC-05 | 8K | K3 | NONE |
| P-ABC-06 | 8K | K3 | PIECEWISE |
| P-ABC-07 | 16K | OFF | NONE |
| P-ABC-08 | 16K | OFF | PIECEWISE |
| P-ABC-09 | 16K | K1 | NONE |
| P-ABC-10 | 16K | K1 | PIECEWISE |
| P-ABC-11 | 16K | K3 | NONE |
| P-ABC-12 | 16K | K3 | PIECEWISE |
| P-ABC-13 | 32K | OFF | NONE |
| P-ABC-14 | 32K | OFF | PIECEWISE |
| P-ABC-15 | 32K | K1 | NONE |
| P-ABC-16 | 32K | K1 | PIECEWISE |
| P-ABC-17 | 32K | K3 | NONE |
| P-ABC-18 | 32K | K3 | PIECEWISE |

### 확장 시 run 수

| 확장 | 조합 수 |
|---|---:|
| Reduced: 3 MBT × 3 MTP × 2 CG | 18 |
| K2 추가: 3 × 4 × 2 | 24 |
| 64K 추가: 4 × 3 × 2 | 24 |
| K2 + 64K 모두 추가 | 4 × 4 × 2 = 32 |

처음부터 32개를 돌리지 않는다.

---

# 11. 권장 staged execution

실제 benchmark는 아래 순서가 효율적이다.

## Stage P0 — Anchor reproducibility

~~~text
P-BASE = 16K / MTP OFF / PIECEWISE
3회 반복
~~~

목적:

> benchmark noise floor 확보.

---

## Stage P1 — Main effects

~~~text
A only
  8K / 16K / 32K

B only
  OFF / K1 / K3

C only
  NONE / PIECEWISE
~~~

이 단계에서 확실히 열세인 level은 interaction matrix에서 제거할 수 있다.

---

## Stage P2 — Pairwise interactions

우선순위:

~~~text
1. A × B
2. B × C
3. A × C
~~~

이유:

- A×B: Prefill batch budget과 MTP overhead interaction
- B×C: hybrid/GDN + MTP에서 graph path interaction
- A×C: graph 이득이 compute-heavy MBT에서 유지되는지 확인

---

## Stage P3 — Reduced ABC

P1/P2 결과에서 살아남은 후보만 사용한다.

예:

~~~text
A survivors = {16K, 32K}
B survivors = {OFF, K1}
C survivor  = {PIECEWISE}

=> 2 × 2 × 1 = 4 final interaction runs
~~~

반드시 18개 full factorial을 다 돌릴 필요는 없다.

---

# 12. 결과 기록 표

각 run은 최소 아래 형식으로 기록한다.

| Field | Value |
|---|---|
| Test ID | P-ABC-xx |
| MBT | |
| MTP K | |
| CUDA Graph mode | |
| input bucket | |
| output bucket | |
| concurrency | |
| TTFT p50 | |
| TTFT p90 | |
| TTFT p95 | |
| Prefill p95 | |
| prompt tok/s | |
| request/s | |
| GPU util | |
| SM active | |
| Tensor active | |
| DRAM active | |
| CPU util | |
| KV cache usage | |
| KV blocks / capacity | |
| graph capture memory | |
| startup/capture time | |
| Mooncake transfer | |
| MTP acceptance | |
| mean acceptance length | |
| Notes | |

---

# 13. Prefill selection rule

최종 Prefill 후보는 단일 최고 throughput만으로 고르지 않는다.

우선순위:

~~~text
1. correctness / Mooncake transfer
2. TTFT p95
3. prompt token throughput
4. tail stability (p90/p95 spread)
5. CPU/GPU balance
6. KV capacity
7. startup / CUDA Graph memory overhead
~~~

특히 MBT가 큰 configuration이 prompt tok/s는 높지만 TTFT p95와 KV capacity를 크게 악화시키면 production winner로 보지 않는다.

---

# 14. 다음 단계 — Decode matrix

Prefill winner를 고른 뒤 Decode는 별도 factor set으로 간다.

~~~text
D-A = max-num-batched-tokens
D-B = MTP K
D-C = CUDA Graph mode
D-D = max-num-seqs
D-E = max_cudagraph_capture_size
~~~

Decode에서는 Prefill과 달리 `FULL_DECODE_ONLY`와 MTP-aware CUDA Graph capture size가 주요 축이 된다.
