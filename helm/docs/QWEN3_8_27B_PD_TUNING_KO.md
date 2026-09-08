# Qwen3.8-27B P/D Disaggregation Tuning Guide

> 상태: P/D Cell runtime functional baseline 이후의 모델별 성능 최적화 계획
>
> 범위: NVIDIA H100/H200, node-local P/D Cell, Mooncake nvlink_intra

## 1. 출발점

P/D Cell infrastructure는 아래 기본 경로까지 runtime validation을 완료했다.

~~~text
Cell-local vLLM Router
  -> Prefill
  -> Mooncake nvlink_intra / CUDA IPC
  -> Decode remote KV load

GPU reservation
common Cell CUDA_VISIBLE_DEVICES
per-engine --device-ids partition
hostPID=true CUDA IPC baseline
pd-cell-guardian whole-cell recycle
~~~

여기서부터는 "P/D가 동작한다"와 "Qwen3.8-27B가 최적이다"를 분리한다.

최적화 목표:

- Prefill: TTFT, prompt throughput, long-context efficiency
- Decode: TPOT/ITL, output throughput
- Spec decode: MTP acceptance / effective speedup
- Cell: P:D replica ratio, GPU-per-engine, saturation point

## 2. Qwen3.8-27B 특성

Qwen3.8-27B는 27B dense hybrid-attention model이다.

공개 vLLM/Qwen 자료 기준:

- 64 layers
- 16 full-attention layers
- 48 Gated DeltaNet / linear-attention layers
- built-in MTP draft head
- native context 262,144
- Qwen3_5ForConditionalGeneration architecture

NVIDIA vLLM recipe는 H200에서 BF16 TP=1 baseline을 제시한다.

따라서 8-GPU H200 node에서도 첫 접근은 TP8이 아니다.

~~~text
model fits on one H200
  -> TP1 baseline
  -> P/D independent replica scale-out
  -> TP2 only when measurement justifies it
~~~

References:

- https://recipes.vllm.ai/Qwen/Qwen3.8-27B
- https://huggingface.co/Qwen/Qwen3.8-27B
- https://docs.vllm.ai/en/latest/features/disagg_prefill/

## 3. vLLM version gate

Qwen3.8은 매우 최근 model family이고 hybrid GDN + MTP 관련 수정이 계속 진행 중이다.

공식 recipe의 일부 NVIDIA validation은 0.26.1rc1.dev 계열 build에서 수행되었다.
성능 sweep 전에 exact vLLM image/commit을 pin하고 다음을 먼저 통과시킨다.

~~~text
Qwen3.8 load/generation
MTP correctness
Mooncake P/D transfer
streaming/reasoning/tool correctness
long-context correctness
~~~

기존 v0.26.0 custom image가 Qwen3.8을 load한다고 해서 곧바로 production performance reference로 가정하지 않는다.

## 4. MTP / Speculative Decoding contract

### P/D 양쪽에 켜는가?

초기 production baseline은 YES로 잡는다.

Built-in MTP를 쓰는 P/D에서는 Prefill과 Decode가 모두 speculative/MTP-aware cache/model layout으로 시작하도록 한다. 공개 P/D MTP 예시들도 producer와 consumer 양쪽에 speculative config를 둔다.

단, Prefill과 Decode의 num_speculative_tokens까지 반드시 같아야 하는 것은 아니다. 공개 P/D 예시 중에는 Prefill과 Decode의 K를 다르게 두는 구성도 있다.

권장 시작점:

~~~text
Prefill: MTP on, K=1
Decode : MTP on, K=1
~~~

이후 Decode만:

~~~text
K=1 -> K=2 -> K=3
~~~

으로 sweep한다.

Qwen3.8 공식 NVIDIA recipe는 일반 serving 예시에서 MTP K=3을 제시한다.

References:

- https://recipes.vllm.ai/Qwen/Qwen3.8-27B
- https://github.com/vllm-project/vllm-ascend/blob/main/docs/source/tutorials/models/GLM5.2.md
- https://github.com/vllm-project/vllm-ascend/blob/main/docs/source/tutorials/features/pd_disaggregation_mooncake_multi_node.md

### MTP acceptance를 반드시 측정

MTP server가 뜨는 것만으로 가속이 발생한다고 판단하지 않는다.

~~~text
vllm:spec_decode_num_accepted_tokens_total
vllm:spec_decode_num_draft_tokens_total
~~~

MTP off / K1 / K2 / K3에 대해 다음을 같이 비교한다.

- output tokens/s
- TPOT / ITL
- acceptance ratio / mean acceptance length
- GPU utilization
- CPU utilization
- batch size별 효과

## 5. 초기에는 피할 조합

### PP > 1 + MTP

최근 upstream에서 startup failure, wrong output, scheduling 문제가 보고되어 있다.

초기 baseline: PP=1.

- https://github.com/vllm-project/vllm/issues/52069
- https://github.com/vllm-project/vllm/issues/49355
- https://github.com/vllm-project/vllm/issues/44697

### Prefix cache + MTP

Qwen3.8 hybrid GDN + MTP에서 prefix-cache retention/reuse 관련 최근 issue가 있다.
MTP correctness/performance를 먼저 끝낸 후 별도 A/B로 추가한다.

- https://github.com/vllm-project/vllm/issues/53504
- https://github.com/vllm-project/vllm/issues/53670

### TurboQuant KV + MTP

Qwen3.8 MTP에서 TurboQuant KV 사용 시 silent repetition collapse 보고가 있다.
초기 NVIDIA baseline은 FP8 KV로 둔다.

- https://github.com/vllm-project/vllm/issues/52475

### Async scheduling + hybrid MTP

accepted-token state와 async scheduling 관련 최근 issue가 있다.
초기 correctness baseline은 --no-async-scheduling으로 두고 후속 A/B로 본다.

- https://github.com/vllm-project/vllm/issues/51571

## 6. Stage 0 — Functional baseline

Topology:

~~~text
H200 8-GPU node

P1D1
Prefill TP1 / PP1
Decode  TP1 / PP1

used compute GPU = 2
remaining GPUs   = replica/topology sweep capacity
~~~

Prefill start point:

~~~text
TP=1
PP=1
MTP off
prefix cache off
async scheduling off
KV cache fp8
gpu-memory-utilization 0.90
max-model-len 200K
max-num-batched-tokens 8192
max-num-seqs 8
~~~

Decode start point:

~~~text
TP=1
PP=1
MTP off
prefix cache off
async scheduling off
KV cache fp8
gpu-memory-utilization 0.90
max-model-len 200K
max-num-batched-tokens 1024
max-num-seqs 32
~~~

위 값들은 최종 recommendation이 아니라 sweep 시작점이다.

Functional gate:

~~~text
actual Mooncake transfer success
Decode remote KV load
Decode full prompt recompute 없음
normal generation
streaming correctness
reasoning parser correctness
tool parser correctness (사용 시)
128K+ long prompt correctness
~~~

## 7. Stage 1 — Prefill tuning

### TP

~~~text
P TP1 vs TP2
~~~

27B가 TP1로 fit하므로 TP2는 메모리 때문이 아니라 긴 prompt compute latency 절감이 collective overhead보다 클 때만 선택한다.

### max-num-batched-tokens

~~~text
8192
16384
32768
~~~

필요하면 65536을 후속으로 추가한다.

측정:

- TTFT p50/p95/p99
- prompt tokens/s
- GPU utilization
- CPU utilization
- KV transfer latency

### max-num-seqs

~~~text
4
8
16
32
~~~

Prefill에서는 무작정 큰 값을 쓰지 않는다. 활성 long prompt가 많으면 activation pressure와 TTFT queueing이 커질 수 있다.

### Chunked Prefill

Qwen3.8 long-context에서는 중요한 축이지만 hybrid GDN + MTP 조합에서 최근 performance issue가 존재한다.

~~~text
MTP off + chunked prefill
  -> stable

MTP on + chunked prefill
  -> separate A/B
~~~

Reference:

- https://github.com/vllm-project/vllm/issues/51008

## 8. Stage 2 — Decode tuning

Decode 목표는 low TPOT/ITL, high output throughput, healthy MTP acceptance다.

TP:

~~~text
D TP1 baseline
D TP1 vs TP2
~~~

Scheduling budget:

~~~text
max-num-batched-tokens: 512 / 1024 / 2048
max-num-seqs:           16 / 32 / 64
~~~

MTP:

~~~text
MTP off
K=1
K=2
K=3
~~~

공식 recipe의 K3을 무조건 최종값으로 쓰지 않는다.
acceptance와 실측 TPOT/output throughput이 같이 좋아지는 지점을 선택한다.

## 9. Stage 3 — 8-GPU node P:D ratio

TP1/TP1이 가능하다면 GPU를 TP 확대보다 replica 확대에 우선 사용할 수 있다.

~~~text
P1D1 = 2 GPUs      baseline
P1D2 = 3 GPUs      decode-heavy
P1D4 = 5 GPUs      more decode-heavy
P2D2 = 4 GPUs      balanced / higher RPS
P2D4 = 6 GPUs      high decode capacity
P3D2 = 5 GPUs      prompt-heavy / long-context
~~~

P/D separation의 핵심은 모든 GPU에 같은 TP를 강제하는 것이 아니라 Prefill compute와 Decode capacity를 독립적으로 scale하는 것이다.

## 10. Long-context benchmark matrix

Input buckets:

~~~text
8K
32K
64K
128K
160K
200K
~~~

Output buckets:

~~~text
128
512
2K
~~~

Reasoning mode도 workload class로 분리한다.

~~~text
thinking off
reasoning_effort=low
reasoning_effort=medium
reasoning_effort=xhigh
~~~

## 11. Metrics

End-to-end:

- request throughput
- token throughput
- E2E latency
- TTFT p50/p95/p99
- TPOT p50/p95/p99
- ITL p50/p95/p99

Prefill:

- prompt tokens/s
- prefill queue time
- batch tokens
- GPU/CPU utilization
- KV transfer bytes/time

Decode:

- output tokens/s
- active sequences
- GPU utilization

MTP:

- draft tokens
- accepted tokens
- acceptance ratio / mean acceptance length
- MTP on/off throughput delta

Transport:

- successful/failed transfers
- transfer latency/bytes/descriptors
- Decode remote-load confirmation

## 12. 권장 실험 순서

~~~text
0. exact vLLM/Qwen3.8 image certification

1. P1D1 TP1/TP1
   MTP off / prefix off / async off

2. Prefill
   MBT 8K -> 16K -> 32K
   seqs 4 -> 8 -> 16 -> 32
   TP1 vs TP2

3. Decode
   MBT 512 -> 1K -> 2K
   seqs 16 -> 32 -> 64
   TP1 vs TP2

4. MTP
   both P/D enabled
   P K=1
   D K=1 -> 2 -> 3

5. P:D ratio
   P1D1 -> P1D2 -> P1D4
   plus P2D2 balanced control

6. prefix cache A/B
7. async scheduling A/B
8. 128K~200K long-context + concurrency + soak
~~~

## 13. Production promotion gate

~~~text
[ ] no-MTP correctness baseline
[ ] MTP output correctness
[ ] MTP acceptance healthy
[ ] streaming correctness
[ ] reasoning/tool parsing correctness
[ ] 128K+ long-context correctness
[ ] actual P/D KV transfer
[ ] Decode no full prompt recompute
[ ] guardian whole-cell recovery
[ ] hostPID CUDA IPC baseline
[ ] target concurrency
[ ] TTFT SLO
[ ] TPOT/ITL SLO
[ ] CUDA IPC/transfer failure = 0 in soak
~~~

## 14. Upstream references

Official / project:

- https://recipes.vllm.ai/Qwen/Qwen3.8-27B
- https://huggingface.co/Qwen/Qwen3.8-27B
- https://docs.vllm.ai/en/latest/features/disagg_prefill/
- https://docs.vllm.ai/en/stable/api/vllm/engine/arg_utils/

Current caveats:

- https://github.com/vllm-project/vllm/issues/52069
- https://github.com/vllm-project/vllm/issues/49355
- https://github.com/vllm-project/vllm/issues/51008
- https://github.com/vllm-project/vllm/issues/51571
- https://github.com/vllm-project/vllm/issues/53504
- https://github.com/vllm-project/vllm/issues/53670
- https://github.com/vllm-project/vllm/issues/52475
