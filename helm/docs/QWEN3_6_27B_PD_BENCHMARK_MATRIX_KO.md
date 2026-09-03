# Qwen3.6-27B P/D Benchmark Matrix

> 범위: Qwen3.6-27B node-local P/D Cell 성능 최적화
>
> 고정 topology baseline:
>
> - Prefill TP=2 / PP=1
> - Decode TP=2 / PP=1
> - Mooncake nvlink_intra
> - H200-class 8-GPU node

## 1. 최적화 목표

~~~text
Prefill
  TTFT / prompt throughput / long-context compute

Decode
  TPOT / ITL / output throughput / MTP

Memory
  hybrid GDN state + full-attention KV capacity

Fabric
  TP2 collective + P->D KV/state transfer의 NVLink 이용률
~~~

최종 목표는 단일 엔진 최고 수치가 아니라 P/D 양쪽 자원을 충분히 사용하면서
queue, KV pressure, GPU compute, NVLink 중 어느 하나도 지속적인 병목이 되지 않는 지점이다.

---

# 2. Qwen3.6 hybrid cache 구조

Qwen3.6-27B:

~~~text
48 x Gated DeltaNet
16 x full attention
~~~

request cache footprint는:

~~~text
fixed recurrent GDN state
+ context-length-proportional full-attention KV
+ MTP 사용 시 speculative recurrent state
~~~

로 보는 것이 맞다.

## TP2 / FP8 KV planning model

Qwen3.6 config:

~~~text
GDN
  key heads   16
  value heads 48
  head dim    128
  conv kernel 4
  SSM state   float32

Full attention
  KV heads    4
  head dim    256
~~~

TP=2에서는 GDN state와 full-attention KV가 rank 단위로 분할된다.

block-size=256, FP8 KV를 가정할 경우 vLLM의 hybrid page-size alignment 결과는
약 1.7K~1.8K token 규모의 physical cache block으로 올라갈 수 있다.

대표 arithmetic:

~~~text
resolved hybrid block ~= 1792 tokens
~~~

실제 engine startup log의 resolved block size를 source of truth로 사용한다.

### MBT와 hybrid block size는 다른 축

Decode MBT=2048이 좋은 후보라고 해서:

~~~text
2048 > 1792
therefore faster
~~~

인 것은 아니다.

~~~text
hybrid block size
  cache allocation / state page granularity

max-num-batched-tokens
  scheduler iteration token budget
~~~

서로 다른 의미다.

---

# 3. Context length별 per-rank cache planning

TP2 + FP8 attention KV + block-size=256 + resolved block~=1792 가정.

48 GDN state의 대략적 per-request/per-rank 비용:

~~~text
MTP OFF ~84 MiB
K1      ~168 MiB
K3      ~336 MiB
~~~

16 full-attention KV는 context에 거의 선형 증가한다.

| Context | Full-attn KV | Total / OFF | Total / K1 | Total / K3 |
|---:|---:|---:|---:|---:|
| 8K | ~140 MiB | ~224 MiB | ~308 MiB | ~476 MiB |
| 32K | ~532 MiB | ~616 MiB | ~700 MiB | ~868 MiB |
| 64K | ~1.01 GiB | ~1.09 GiB | ~1.18 GiB | ~1.34 GiB |
| 128K | ~2.02 GiB | ~2.11 GiB | ~2.19 GiB | ~2.35 GiB |
| 170K | ~2.60 GiB | ~2.68 GiB | ~2.76 GiB | ~2.93 GiB |
| 200K | ~3.14 GiB | ~3.23 GiB | ~3.31 GiB | ~3.47 GiB |

이 표는 planning용 근사치다.

실제 기준:

~~~text
GPU KV cache size
num GPU blocks
Maximum concurrency
resolved hybrid block size
~~~

startup log를 기록한다.

핵심:

- long context에서는 full-attention KV가 지배한다.
- short context에서는 MTP speculative state overhead 비중이 커진다.
- MTP K를 올릴 때 speedup과 maximum concurrency 감소를 같이 본다.

---

# 4. max-num-seqs = 1024

H100/H200-class GPU에서 vLLM 0.26.0 OpenAI API server의 자동 default가 1024인 것은 맞다.

그러나 단순 scheduler upper bound만은 아니다.

## 영향을 받는 영역

~~~text
scheduler active sequence ceiling

query / seq_lens / block-table metadata buffers

attention backend persistent buffers

Mamba/GDN state-index metadata

CUDA Graph capture envelope
~~~

기본 max cudagraph capture size:

~~~text
min(max_num_seqs * 2, 512)
~~~

## Hybrid + FULL decode CUDA Graph

vLLM 0.26.0은 Mamba/GDN cache가 있는 model에서 full decode cudagraph를 사용할 때:

~~~text
max_num_seqs <= available Mamba cache blocks
~~~

를 검사한다.

upstream 코드 설명:

~~~text
Each decode sequence requires one Mamba cache block.
~~~

따라서 실제 동시성이 낮아도:

~~~text
max-num-seqs 1024
available Mamba blocks 700
~~~

이면 FULL decode CUDA Graph 초기화가 실패할 수 있다.

## 권장

### Prefill

~~~text
max-num-seqs = 1024
~~~

를 non-binding ceiling으로 유지하는 것은 가능하다.

하지만 실제 Prefill 제약은:

~~~text
MBT
max-num-partial-prefills
max-long-partial-prefills
long-prefill-token-threshold
KV admission
~~~

쪽에서 먼저 올 수 있다.

### Decode

초기 권장:

~~~text
max-num-seqs = 512
~~~

1024를 full factorial의 고정값으로 두지 않는다.

한 번만:

~~~text
256
512
1024
~~~

를 short-context / high-concurrency에서 비교한다.

512와 1024의 saturation throughput이 같으면 512가 non-binding이므로 이후 512 고정.

---

# 5. CUDA Graph mode 전체

vLLM 0.26.0 CUDAGraphMode:

~~~text
NONE
PIECEWISE
FULL
FULL_DECODE_ONLY
FULL_AND_PIECEWISE
~~~

## C0 NONE

CUDA Graph 사용 안 함.

장점:

~~~text
clean control
capture memory 없음
debug/correctness 유리
~~~

단점:

~~~text
kernel launch / CPU dispatch overhead 노출
~~~

P/D 모두 baseline.

## C1 PIECEWISE

CUDA Graph-compatible partition을 capture하고 attention/GDN 등 incompatible op는 graph 밖에서 실행.

장점:

~~~text
dynamic/non-uniform prefill에 유연
hybrid model Prefill의 우선 후보
mixed batch compatibility
~~~

단점:

~~~text
partition boundary / CPU dispatch overhead
FULL보다 Decode launch overhead 큼
capture memory 존재
~~~

Prefill primary candidate.

## C2 FULL

전체 model forward를 full graph로 capture하는 single mode.

장점:

~~~text
compatible shape에서는 launch overhead 최소
~~~

단점:

~~~text
backend / shape compatibility 제약이 큼
hybrid GDN arbitrary prefill/mixed batch에 부적합
vLLM이 backend capability에 따라 자동 downgrade 가능
~~~

Qwen3.6 main matrix에서는 제외.

requested mode와 resolved mode를 startup log에서 구분한다.

## C3 FULL_DECODE_ONLY

~~~text
uniform decode -> FULL
prefill/mixed  -> NONE
~~~

P/D Decode engine에 가장 자연스러운 specialized mode.

장점:

~~~text
pure Decode full graph
PIECEWISE graph memory 절약 가능
P/D decode 전용 구조에 적합
~~~

주의:

~~~text
MTP + hybrid state compatibility
capture-size alignment
max-num-seqs <= Mamba blocks
~~~

Decode primary candidate.

## C4 FULL_AND_PIECEWISE

~~~text
uniform decode -> FULL
prefill/mixed  -> PIECEWISE
~~~

장점:

~~~text
general serving에서 가장 공격적
decode full + prefill/mixed piecewise
~~~

단점:

~~~text
capture memory 가장 큼
startup/capture time 길음
role-separated P/D에서는 사용하지 않는 graph까지 보유 가능
~~~

Decode에서는 FULL_DECODE_ONLY와 비교하는 fallback/control.

## P/D 권장

| Engine | Primary | Control | Follow-up |
|---|---|---|---|
| Prefill | PIECEWISE | NONE | FULL_AND_PIECEWISE 필요 시 |
| Decode | FULL_DECODE_ONLY | NONE | FULL_AND_PIECEWISE |
| FULL | main matrix 제외 | resolved-mode 확인 | 필요 시 |

---

# 6. Prefill fixed baseline

~~~text
TP=2
PP=1
max-num-seqs=1024
prefix cache OFF
async scheduling OFF
KV dtype fp8
same GPU pair
same P/D topology
~~~

## P-A MBT

~~~text
8K
16K
32K
64K optional
~~~

## P-B MTP

~~~text
OFF
K1
K2 optional
K3
~~~

Prefill에서는 MTP가 generation speedup의 중심축이 아니다.

확인:

~~~text
MTP-aware state/cache overhead
TTFT regression
CPU/GDN dispatch overhead
KV capacity regression
P/D correctness
~~~

## P-C CUDA Graph

~~~text
NONE
PIECEWISE
~~~

## Prefill interactions

~~~text
P-A x P-B
P-A x P-C
P-B x P-C
P-A x P-B x P-C
~~~

Reduced full factorial:

~~~text
3 MBT x 3 MTP x 2 CG
= 18 configurations
~~~

main/pairwise에서 명확히 열세인 level은 final ABC에서 제거한다.

---

# 7. Decode MBT

Decode-only에서는 Prefill 수준의 큰 MBT를 유지할 이유가 거의 없다.

## Pure decode

MTP OFF:

~~~text
approximately 1 scheduled token / active request / step
~~~

max-num-seqs=512라면 MBT=512만으로도 pure decode 512-request envelope를 덮는다.

## MTP

uniform decode planning:

~~~text
scheduled tokens / request ~= 1 + K
~~~

| max seqs | MTP | full-envelope tokens |
|---:|---:|---:|
| 512 | OFF | 512 |
| 512 | K1 | 1024 |
| 512 | K2 | 1536 |
| 512 | K3 | 2048 |
| 1024 | OFF | 1024 |
| 1024 | K1 | 2048 |
| 1024 | K3 | 4096 |

실제 long-context workload는 KV/state capacity 때문에 이 수치 전에 saturation될 가능성이 높다.

## D-MBT sweep

~~~text
512
1024
2048
4096 optional control
~~~

예상:

~~~text
512
  low-overhead
  high concurrency / MTP에서 budget cap 가능

1024
  strong baseline

2048
  max-seqs512 + K3 envelope까지 커버
  Decode production 후보

4096
  2K에서 scheduler saturation이 확인될 때만
~~~

따라서 2K를 Decode 우선 후보로 보는 것은 합리적이다.

근거는 Mamba block crossing이 아니라:

~~~text
target decode concurrency x (1 + K)
를 충분히 커버하면서
불필요한 large token budget을 피함
~~~

이다.

---

# 8. Decode benchmark factors

고정 baseline:

~~~text
TP=2
PP=1
max-num-seqs=512 initial
prefix cache OFF
async scheduling OFF
KV dtype fp8
same GPU pair
~~~

## D-A MBT

~~~text
512
1024
2048
4096 optional
~~~

## D-B MTP

~~~text
OFF
K1
K2
K3
~~~

## D-C CUDA Graph

~~~text
NONE
FULL_DECODE_ONLY
FULL_AND_PIECEWISE
~~~

FULL은 hybrid backend automatic downgrade 때문에 main factor에서 제외.

## D-D max cudagraph capture size

초기:

~~~text
auto
~~~

후속:

~~~text
128
256
512
~~~

MTP에서는 capture shape가 1+K 배수로 정렬된다.

planning target:

~~~text
capture coverage
>= target concurrent decode seqs x (1 + K)
~~~

## Decode interaction 우선순위

~~~text
1. MBT x MTP
2. MTP x CUDA Graph
3. MBT x CUDA Graph
4. winner에서 capture size
~~~

---

# 9. Context-length matrix

P/D 모두:

~~~text
8K
32K
64K
128K
170K
200K
~~~

Output:

~~~text
128
512
2K
~~~

## Prefill 관점

~~~text
context up
-> TTFT
-> prompt tok/s
-> GPU compute saturation
-> chunking behavior
~~~

## Decode 관점

~~~text
context up
-> full-attention KV footprint up
-> max concurrent seqs down
-> TPOT / memory-bandwidth pressure
~~~

## MTP 관점

~~~text
K up
-> speculative GDN state footprint up
-> acceptance
-> output tok/s
-> maximum concurrency down
~~~

---

# 10. KV capacity validation

각 engine startup마다 기록:

~~~text
resolved attention block size
resolved Mamba/GDN block size
KV cache memory
num GPU blocks
maximum concurrency estimate
CUDA Graph memory
~~~

runtime:

~~~text
vllm:kv_cache_usage_perc
vllm:num_preemptions_total
running requests
waiting requests
waiting reason
~~~

production 후보:

~~~text
sustained KV ceiling 없음
preemption approximately 0
170K target workload concurrency margin 존재
~~~

---

# 11. TP2와 NVLink

P/D 모두 TP2 고정은 합리적인 baseline이다.

장점:

~~~text
weight/rank footprint 감소
GDN state/rank footprint 감소
full-attention KV/rank footprint 감소
long-context concurrency 증가 가능
~~~

비용:

~~~text
layer별 TP collective
-> NVLink traffic
~~~

따라서 TP2는 compute/memory만 보지 않고 NVLink와 같이 평가한다.

---

# 12. NVLink traffic을 분리해서 측정

P/D node-local에서는 최소 두 종류가 겹친다.

~~~text
A. intra-engine TP2 collective
   P rank0 <-> P rank1
   D rank0 <-> D rank1

B. inter-engine P->D KV/state transfer
   Prefill GPU pair -> Decode GPU pair
~~~

## N0 Idle

~~~text
no request
~~~

fabric noise baseline.

## N1 Prefill TP2 control

Prefill compute 위주 run.

측정:

~~~text
NVLink TX/RX
PCIe TX/RX
prompt tok/s
SM Active
~~~

P-side TP collective baseline.

## N2 Decode TP2 control

Decode generation 위주 run.

D-side TP collective baseline.

## N3 Transfer-focused P/D

~~~text
128K / output 16~32
170K / output 16~32
200K / output 16~32
~~~

긴 Prefill + 매우 짧은 Decode로 transfer interval을 분리.

기대:

~~~text
P-side NVLink TX spike
D-side NVLink RX spike
Mooncake transfer success
PCIe보다 NVLink activity 우세
~~~

## N4 Production shape

~~~text
170K input
2K output
target concurrency
~~~

TP traffic + P->D transfer + MTP가 동시에 있을 때 fabric saturation 여부 확인.

---

# 13. NVLink metrics

~~~text
DCGM_FI_PROF_NVLINK_TX_BYTES
DCGM_FI_PROF_NVLINK_RX_BYTES
DCGM_FI_DEV_NVLINK_BANDWIDTH_TOTAL
DCGM_EXP_P2P_STATUS

DCGM_FI_PROF_PCIE_TX_BYTES
DCGM_FI_PROF_PCIE_RX_BYTES

NVLink CRC
NVLink replay
NVLink recovery
~~~

해석:

~~~text
P2P status
  path available?

NVLink TX/RX
  actual fabric traffic?

PCIe TX/RX
  PCIe/fallback traffic dominating?

CRC/replay/recovery
  healthy at load?
~~~

## fabric utilization reference

spec sheet peak만 기준으로 삼지 않는다.

먼저 동일 node / 동일 GPU placement에서:

~~~text
NCCL all-reduce / send-recv or P2P microbenchmark
~~~

로 empirical ceiling을 측정한다.

그 뒤:

~~~text
observed vLLM/Mooncake NVLink GB/s
/
same-topology empirical NVLink ceiling
~~~

로 평가한다.

---

# 14. 최종 staged benchmark

## Stage 0 Hardware / cache baseline

~~~text
P TP2 / D TP2
resolved hybrid block 기록
KV blocks / maximum concurrency 기록
NVLink topology 확인
same-placement NCCL/P2P ceiling 측정
~~~

## Stage 1 Prefill main effects

~~~text
MBT
MTP
CG NONE vs PIECEWISE
~~~

## Stage 2 Prefill interactions

~~~text
MBT x MTP
MTP x CG
MBT x CG
survivor ABC
~~~

## Stage 3 Decode max-num-seqs non-binding check

~~~text
256
512
1024
~~~

short context / high concurrency에서 1회.

## Stage 4 Decode main effects

~~~text
MBT 512 / 1K / 2K
MTP OFF / K1 / K2 / K3
CG NONE / FULL_DECODE_ONLY / FULL_AND_PIECEWISE
~~~

## Stage 5 Decode interactions

~~~text
MBT x MTP
MTP x CG
MBT x CG
~~~

## Stage 6 Capture-size sweep

winner 기준:

~~~text
auto
128
256
512
~~~

## Stage 7 Context/cache surface

~~~text
8K
32K
64K
128K
170K
200K
~~~

KV capacity / maximum concurrency를 같이 기록.

## Stage 8 NVLink isolation

~~~text
N0 idle
N1 P TP2
N2 D TP2
N3 transfer-focused
N4 production workload
~~~

---

# 15. Selection rule

Prefill winner:

~~~text
TTFT p95
prompt tok/s
GPU saturation
CPU overhead
KV capacity
NVLink TP cost
~~~

Decode winner:

~~~text
TPOT / ITL p95
generation tok/s
MTP acceptance
maximum concurrency
KV/state footprint
CUDA Graph memory
NVLink TP cost
~~~

Cell winner:

~~~text
P/D queue balance
no sustained KV pressure
no preemption
P->D transfer not bottleneck
NVLink not saturated/erroring
170K + 2K workload stable
~~~

핵심:

> P와 D가 각각 가진 TP2 GPU를 충분히 사용하고, cache와 NVLink headroom을 남기면서,
> 어느 한쪽 queue도 지속적으로 쌓이지 않는 configuration을 선택한다.
