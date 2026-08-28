# vLLM Production Stack 0.1.8 Custom — P/D Cell 설계 및 운영 가이드

## 1. 문서 목적

이 문서는 custom vLLM Production Stack 0.1.8에서 단기 운영하는 **node-local Prefill/Decode Cell**의 확정 설계를 설명한다.

2026-08-25 기준 핵심 결정은 다음과 같다.

- **Global Router**: 기존 LMStack Router 유지 가능
- **P/D Cell Orchestrator**: `vllm-project/router`의 **vLLM Router로 고정**
- **Cell 내부 LMStack Router 지원 제거**
- KV connector는 `models[].kvTransfer.connector`에서 모델별 선언
- vLLM Router가 connector에 맞는 P/D control-plane metadata를 생성
- 고객사 우선 검증 backend는 **MooncakeConnector**
- 현재 검증 baseline은 vLLM `0.26.0-cu129` + source-built `mooncake-transfer-engine 0.3.10-post2`
- P/D Cell은 **단일 Pod/단일 노드 배치**를 contract로 하므로 exact `MooncakeConnector`는 Chart가 `mooncake_protocol=nvlink_intra`를 강제로 주입
- Mooncake P/D engine은 container별 Device Plugin GPU 격리를 사용하지 않고 **Pod-local GPU reservation + 공통 CUDA namespace + vLLM `--device-ids` 분할**을 사용

이 문서의 목표는 Helm이 단순히 Pod를 띄우는 수준이 아니라, **Router ↔ Prefill ↔ Decode ↔ KV connector 사이의 실제 protocol contract까지 일치시키는 것**이다.

---

## 2. 최종 아키텍처

```text
Client / LiteLLM
        |
        v
+-------------------------+
| Global LMStack Router   |
| model discovery/routing |
+-------------------------+
        |
        | model service
        v
+------------------------------------------------------+
| P/D Cell Pod                                         |
|                                                      |
|  +----------------------+                            |
|  | gpu-reservation      |  total GPU resource owner |
|  | writes sorted UUIDs  |  -> shared emptyDir       |
|  +----------------------+                            |
|                                                      |
|  +----------------------+                            |
|  | vllm-project/router  |  :8000                    |
|  | P/D orchestrator     |  metrics :29000           |
|  +----------+-----------+                            |
|             |                                        |
|       +-----+-----+------------------+               |
|       |                            |                 |
|       v                            v                 |
|  Prefill :8101              Decode :8201/8202       |
|  same Cell CVD              same Cell CVD           |
|  --device-ids subset        --device-ids subset     |
|       |                            ^                 |
|       +--- Mooncake nvlink_intra --+                |
|                                                      |
+------------------------------------------------------+
```

P/D Cell은 한 Pod 안에 Router/P/D container를 같이 두므로 모든 container가 같은 Pod network namespace를 공유한다.

따라서 Cell 내부 HTTP endpoint는 다음처럼 localhost로 연결한다.

```text
Router -> Prefill  http://127.0.0.1:8101
Router -> Decode   http://127.0.0.1:8201
Router -> Decode   http://127.0.0.1:8202
```

Mooncake Prefill bootstrap도 동일하다.

```text
Router -> Mooncake bootstrap http://127.0.0.1:9001/query
```

---

## 3. 왜 P/D Cell에서 LMStack Router를 제거했는가

### 3.1 LMStack orchestrated P/D의 control-plane contract

LMStack Router의 `disaggregated_prefill_orchestrated` path는 Prefill 요청에 일반적인 vLLM `kv_transfer_params`를 넣고, **Prefill HTTP response에 다시 포함된 `kv_transfer_params`를 Decode로 전달**하는 response-driven 방식이다.

이 방식은 NxDI/NIXL 계열 orchestration contract와 잘 맞는다.

개념적으로:

```text
Router
  |
  | do_remote_decode=true
  v
Prefill
  |
  | response.kv_transfer_params
  v
Router
  |
  | copied metadata
  v
Decode
```

### 3.2 vLLM 0.26.0 Mooncake contract와의 불일치

vLLM `0.26.0`의 `MooncakeConnector`는 Prefill scheduler에서 `transfer_id`가 없으면 다음 경고를 내고 transfer 대상 요청으로 등록하지 않는다.

```text
Missing transfer_id in kv_transfer_params from router!
```

또한 Decode가 remote KV를 사용하려면 최소 다음 metadata가 필요하다.

```json
{
  "do_remote_prefill": true,
  "remote_engine_id": "...",
  "remote_bootstrap_addr": "...",
  "transfer_id": "..."
}
```

Mooncake의 Prefill `request_finished()`는 NIXL식 Decode metadata를 HTTP response에 만들어 돌려주지 않는다. 따라서 LMStack Router가 기대하는:

```text
Prefill response -> kv_transfer_params -> Decode
```

계약 자체가 Mooncake와 맞지 않는다.

실제 runtime에서도 다음이 확인됐다.

```text
Router: Prefill responses did not contain kv_transfer_params
Prefill: Missing transfer_id in kv_transfer_params from router!
Decode: 정상 응답 생성
```

이 경우 정상 응답은 P/D 성공 증거가 아니다. Decode가 remote KV 요청을 받지 않았기 때문에 일반 inference처럼 prompt를 자체 Prefill했을 가능성이 높다.

### 3.3 vLLM Router의 connector-specific orchestration

`vllm-project/router`는 NIXL을 기본 backend로 시작했지만 2026-04-17 Mooncake 지원 PR #151을 통해 connector-specific orchestration을 추가했다.

vLLM Router는 Mooncake를 선택하면:

1. Prefill bootstrap server `/query` 호출
2. Prefill `engine_id`를 DP rank별로 획득
3. 요청마다 `transfer_id` 생성
4. Prefill에 Mooncake 전용 params 삽입
5. Decode에 `remote_engine_id`, `remote_bootstrap_addr`, 동일 `transfer_id` 삽입

즉 Mooncake에서 Prefill HTTP response의 KV metadata에 의존하지 않는다.

이 때문에 **Cell 내부 P/D orchestrator는 vLLM Router로 고정**한다.

---

## 4. Router 역할 분리

### Global LMStack Router

Global Router는 다음 역할만 담당한다.

- 여러 model service discovery
- model-level/global traffic routing
- 여러 P/D Cell replica를 backend pool로 관리
- 공개 endpoint 역할

Global LMStack Router는 Cell 내부의 Mooncake/NIXL handoff protocol을 직접 처리하지 않는다.

### Cell-local vLLM Router

Cell Router는 다음 역할을 담당한다.

- Prefill/Decode pair 선택
- two-stage request orchestration
- KV connector별 metadata 생성
- Mooncake bootstrap 조회
- Prefill/Decode health tracking
- P/D request metrics

따라서 Helm에서는 더 이상 다음 선택을 제공하지 않는다.

```yaml
router:
  type: lmstack | vllm | custom
```

`router.type`, `router.args`, `router.kvConnector`는 제거한다.

- Router implementation은 고정: `vllm-router`
- fixed protocol을 우회하는 full `args` override 제거
- Router KV mode는 `models[].kvTransfer.connector`에서 자동 파생

---

## 5. vLLM Router image baseline

Mooncake 지원은 2026-04-17 PR #151에서 들어갔다.

그 뒤 Python launcher에도 `--kv-connector`가 연결되었다. 따라서 너무 오래된 wheel/image를 사용하면 다음 문제가 날 수 있다.

```text
vllm-router --kv-connector mooncake
unrecognized arguments: --kv-connector
```

현재 chart baseline은 다음으로 고정한다.

```text
vllm-project/router v0.1.15
```

폐쇄망에서는 upstream image tag를 암묵적으로 사용하지 말고 다음 형태를 권장한다.

```text
Git tag/SHA
   -> internal source build
   -> private registry
   -> immutable image digest pin
```

예:

```yaml
pdCellSpec:
  router:
    repository: registry.internal/vllm/vllm-router
    tag: v0.1.15
```

Global `routerSpec`는 LMStack Router용이므로 Cell Router가 상속하지 않는다.

---

## 6. vLLM Router CLI contract

P1D2 + Mooncake 예시에서 Helm은 다음 의미의 command/args를 생성한다.

```bash
vllm-router \
  --host 0.0.0.0 \
  --port 8000 \
  --policy consistent_hash \
  --vllm-pd-disaggregation \
  --kv-connector mooncake \
  --health-check-interval-secs 30 \
  --health-check-timeout-secs 5 \
  --prefill http://127.0.0.1:8101 9001 \
  --decode http://127.0.0.1:8201 \
  --decode http://127.0.0.1:8202 \
  --prometheus-host 0.0.0.0 \
  --prometheus-port 29000
```

Upstream `Dockerfile.router`는 `vllm-router`를 `CMD`로 제공하고 ENTRYPOINT는 고정하지 않으므로 Helm은 안전하게 다음 command를 명시한다.

```yaml
command:
  - vllm-router
```

`router.command`는 사내 image path 차이 같은 실행파일 위치 변경에만 사용한다. Router implementation을 바꾸는 escape hatch로 사용하지 않는다.

---

## 7. KV connector mapping

Helm은 engine connector 이름으로 vLLM Router의 connector mode를 자동 결정한다.

| Engine `kv_connector` | vLLM Router `--kv-connector` |
|---|---|
| `NixlConnector`, `NixlPullConnector`, `NixlPushConnector` | `nixl` |
| `MooncakeConnector` | `mooncake` |
| 이름에 `Mori`/`MoRI`가 포함된 connector | `moriio` |

지원되지 않는 connector는 render 단계에서 fail한다.

주의: Router가 `moriio`를 지원한다고 해서 사용하는 vLLM engine version도 해당 connector를 지원한다는 뜻은 아니다. **Router capability와 engine connector capability는 별도로 검증**해야 한다.

현재 고객사 baseline은 Mooncake다.

---

## 8. Mooncake control-plane

### 8.1 Prefill bootstrap

Prefill에는 다음 env가 들어간다.

```text
VLLM_MOONCAKE_BOOTSTRAP_PORT=9001+prefill_index
VLLM_MOONCAKE_ABORT_REQUEST_TIMEOUT=600
```

P1이면:

```text
P0 bootstrap :9001
```

P2면:

```text
P0 bootstrap :9001
P1 bootstrap :9002
```

### 8.2 Router startup

Mooncake direct URL mode에서 Router는 시작 시 Prefill bootstrap을 조회한다.

정상 로그 기대값:

```text
kv_connector: Mooncake
Mooncake connector enabled, querying prefill bootstrap servers...
Querying Mooncake bootstrap at http://127.0.0.1:9001
Got Mooncake engine_ids for http://127.0.0.1:8101: {...}
Mooncake bootstrap query complete for all prefill nodes
```

이 단계가 실패하면 inference smoke test를 진행하지 않는다.

### 8.3 Router -> Prefill

정상 request metadata:

```json
{
  "kv_transfer_params": {
    "do_remote_decode": true,
    "do_remote_prefill": false,
    "transfer_id": "xfer-<uuid>"
  }
}
```

다음 경고는 절대 정상 상태가 아니다.

```text
Missing transfer_id in kv_transfer_params from router!
```

### 8.4 Router -> Decode

정상 request metadata:

```json
{
  "kv_transfer_params": {
    "do_remote_decode": false,
    "do_remote_prefill": true,
    "transfer_id": "xfer-<same uuid>",
    "remote_bootstrap_addr": "http://127.0.0.1:9001",
    "remote_engine_id": "<prefill engine id>"
  }
}
```

이 metadata가 없으면 Decode가 정상 응답을 반환해도 실제 P/D KV handoff가 아닌 local recompute일 수 있다.

---

## 9. Mooncake transport와 GPU namespace contract

현재 source-built image baseline:

```text
vLLM          0.26.0 CUDA 12.9
Mooncake      0.3.10-post2
```

Mooncake `nvlink_intra`의 실제 data plane은 Mooncake Transfer Engine의
`IntraNodeNvlinkTransport`다.

```text
Decode KV allocation
  -> cudaIpcGetMemHandle()
  -> serialized cudaIpcMemHandle_t
  -> Mooncake metadata
  -> Prefill cudaIpcOpenMemHandle()
  -> mapped remote VA
  -> cudaMemcpy / NVLink
```

따라서 Linux `/dev/shm` 파일 공유나 Pod PID namespace와 동일한 개념이 아니다.
실제 장애 분석에서 `hostIPC=true`, `shareProcessNamespace=true`, 공용
`/dev/shm`을 모두 적용해도 `cudaIpcOpenMemHandle`의
`invalid argument` / `invalid device context`는 해소되지 않았다.

### 9.1 왜 기존 container별 GPU request를 제거했는가

NVIDIA Device Plugin의 legacy allocation은 container 단위다.

예를 들어 P2/D2를 각각 직접 요청하면:

```text
Prefill container
  physical GPU A,B
  local CUDA ordinal 0,1

Decode container
  physical GPU C,D
  local CUDA ordinal 0,1
```

처럼 서로 다른 physical GPU가 각 container에서 동일 local ordinal로 재매핑될 수
있다. Mooncake 0.3.10 `nvlink_intra`의 CUDA IPC 경로에서 이 분리된 device
namespace가 실제 runtime blocker로 관찰되었다.

MooncakeConnector P/D Cell에서는 이 문제를 피하기 위해 다음 contract를 사용한다.

```text
requestGPU topology
  P0: 2
  P1: 2
  D0: 4
       |
       v
Chart total = 8
       |
       v
gpu-reservation container
  nvidia.com/gpu: 8
       |
       +--> allocated GPU UUIDs sorted by PCI bus
       +--> /var/run/pd-gpu/gpus
                    |
        +-----------+-----------+
        |           |           |
       P0          P1          D0
 NVD=all      NVD=all      NVD=all
 CVD=A..H     CVD=A..H     CVD=A..H
 ids=0,1     ids=2,3      ids=4,5,6,7
```

운영자는 GPU index/range를 values에 적지 않는다. Chart가 기존
`count * requestGPU`만으로 자동 계산한다.

### 9.2 runtime 변수 역할

Engine manifest에는 다음을 명시한다.

```text
NVIDIA_VISIBLE_DEVICES=all
```

이는 container 생성 시 NVIDIA runtime이 peer GPU device를 inject할 수 있게 한다.

Chart-owned launcher는 reservation file을 읽은 다음 **모든 P/D engine에서 동일하게**:

```bash
CUDA_VISIBLE_DEVICES=<reservation 전체 UUID 목록>
```

을 설정한다. 이후 engine별 자동 index를:

```bash
vllm serve --device-ids <CNTR_GPU_IDX> ...
```

로 전달한다.

vLLM 0.26.0은 `CUDA_VISIBLE_DEVICES`가 있을 때 integer `--device-ids`를
그 visible list의 index로 resolve하고, UUID CVD도 NVML physical ID로 변환한다.
따라서 모든 engine은 동일 CUDA ordinal namespace를 유지하면서 실제 compute GPU는
서로 겹치지 않게 고정된다.

### 9.3 isolation trade-off

이 방식은 Kubernetes scheduler accounting은 유지한다. 실제 GPU extended resource는
`gpu-reservation` container가 Cell 전체 합계를 독점하므로 다른 정상 workload가 그
GPU를 재할당받지 않는다.

다만 P/D engine container는 `NVIDIA_VISIBLE_DEVICES=all`이므로 Linux device-level
hard isolation은 아니다. Chart-owned launcher가 CVD를 reservation UUID로 좁혀
정상 CUDA application의 runtime visibility를 제한한다.

```text
Scheduler GPU accounting                 YES
Cell 내부 compute partition              YES
CUDA runtime에서 다른 workload GPU 숨김  YES
/dev/nvidia* hard security boundary       NO
```

driver/device-plugin을 DRA로 전환할 수 없는 현재 환경의 bridge contract이며,
strict device security boundary가 필요하면 향후 DRA/NRI 계층으로 재설계한다.

### 9.4 강제 protocol

P/D Cell은 동일 Pod이므로 exact `MooncakeConnector`에서는 Chart가 최종
KVTransferConfig에 다음을 강제한다.

```json
{
  "kv_connector_extra_config": {
    "mooncake_protocol": "nvlink_intra"
  }
}
```

values의 공통/Prefill/Decode 어느 merge layer에서든 사용자가
`mooncake_protocol`을 지정하면 Helm render를 fail한다.

Mooncake Transfer Engine binary가 `nvlink_intra` support 없이 build되었다면
runtime startup/transfer가 실패하는 것이 의도된 fail-fast 동작이다.

`hostIPC`, `shareProcessNamespace`는 이 contract의 요구사항이 아니다.
Engine의 기존 `/dev/shm` mount는 vLLM/NCCL/multiprocessing 용도로 유지한다.

## 10. KVTransferConfig

모델별 설정:

```yaml
kvTransfer:
  connector: MooncakeConnector
  bootstrapPortBase: 9001
  abortRequestTimeout: 600
  config:
    kv_load_failure_policy: fail
    kv_connector_extra_config:
      num_workers: 16

# mooncake_protocol은 values에 쓰지 않는다.
# Chart가 exact MooncakeConnector에 nvlink_intra를 강제한다.
```

Helm은 phase별로 최종 설정을 merge한 뒤 다음 필드를 강제로 설정한다.

Prefill:

```json
{
  "kv_connector": "MooncakeConnector",
  "kv_role": "kv_producer"
}
```

Decode:

```json
{
  "kv_connector": "MooncakeConnector",
  "kv_role": "kv_consumer"
}
```

`kv_load_failure_policy` 기본 운영 권장은 `fail`이다.

`recompute`는 Decode에서 긴 Prefill fallback을 유발해 tail latency isolation을 깨뜨릴 수 있고, connector 실패를 정상 응답으로 가릴 수 있으므로 certification에서는 사용하지 않는다.

---

## 11. served model / alias

P/D engine에는 모든 공개 이름을 vLLM `--served-model-name`으로 전달한다.

```yaml
servedModelNames:
  - Qwen3.6-27B-PD
  - test-alias
```

렌더 결과 의미:

```bash
vllm serve ... \
  --served-model-name Qwen3.6-27B-PD test-alias
```

vLLM Router의 `/v1/models`는 자체 LMStack-style static alias catalog를 만들지 않고 Prefill의 `/v1/models`를 proxy한다.

따라서 기대 contract는:

```text
Prefill /v1/models
  primary + alias
        |
        v
Cell vLLM Router /v1/models
  primary + alias
        |
        v
Global Router discovery
  primary + alias
```

Runtime certification에서 반드시 다음 둘을 확인한다.

- Cell Router `/v1/models`에 primary + alias 모두 노출
- Global Router에서도 alias discovery 및 alias 요청 성공

---

## 12. API surface

vLLM Router는 명시적인 OpenAI endpoint 외에도 transparent proxy를 활성화하며 unmatched POST request를 P/D two-stage pipeline으로 보낼 수 있다.

따라서 underlying vLLM server가 지원하는 경우 다음도 runtime 검증 대상이다.

```text
/v1/chat/completions
/v1/responses
/v1/messages
```

특히 `/v1/messages`는 전용 Router endpoint라서 지원되는 것이 아니라 **transparent P/D proxy path**를 타는 것이므로 streaming/tool-use까지 실제 사용 패턴으로 검증한다.

---

## 13. API key contract

기존 stack의 engine secret이 `servingEngineSpec.vllmApiKey`로 설정되면 P/D engine에는 `VLLM_API_KEY`가 전달된다.

vLLM Router direct-URL P/D path는 backend Authorization을 `OPENAI_API_KEY`에서 읽는 코드 path가 있으므로 Cell Router에는 같은 secret을 두 이름으로 주입한다.

```text
VLLM_API_KEY=<same secret>
OPENAI_API_KEY=<same secret>
```

이렇게 해야 engine API-key 인증을 활성화한 환경에서도 Router -> P/D 요청이 401로 실패하지 않는다.

---

## 14. Port model

기본 port layout:

| Component | 기본 port |
|---|---:|
| Cell Router API | 8000 |
| Cell Router Prometheus | 29000 |
| Prefill HTTP | 8101 + index |
| Decode HTTP | 8201 + index |
| Prefill Mooncake bootstrap | 9001 + index |
| Prefill vLLM internal | 20000 + stride |
| Decode vLLM internal | 30000 + stride |
| Prefill DP master | 24000 + index |
| Decode DP master | 34000 + index |
| Prefill NIXL side channel | 5600 + index |
| Decode NIXL side channel | 5700 + index |

한 Pod에서 모든 container가 network namespace를 공유하므로 port collision은 금지한다.

---

## 15. Health / startup

vLLM Router Mooncake direct mode는 Router listener가 준비되기 전에 P/D health와 bootstrap query를 기다릴 수 있다.

대형 모델 startup 때문에 Router를 조기 재시작하지 않도록 기본 Router startup budget을 늘렸다.

```yaml
startupProbePeriodSeconds: 5
startupProbeFailureThreshold: 180
```

즉 약 15분이다.

Router health values는 실제 vLLM Router CLI로 전달한다.

```text
healthCheckInterval -> --health-check-interval-secs
healthCheckTimeout  -> --health-check-timeout-secs
```

---

## 15.1 Mooncake GPU reservation runtime 검증 완료 — 2026-08-28

PR #4의 node-local Mooncake GPU reservation 구조는 실제 GPU node에서 성공 검증했다.

검증 topology:

```text
P1D1
Prefill TP2 = 2 GPU
Decode  TP2 = 2 GPU
Cell total  = 4 GPU
```

관측 결과:

```text
gpu-reservation
  -> Chart 계산대로 nvidia.com/gpu 4개 요청
  -> 할당 GPU UUID 파일 생성 성공

Prefill / Decode container
  -> nvidia-smi에서 node GPU 8개 전체 접근 가능
  -> vLLM PID1 environment의 CUDA_VISIBLE_DEVICES는
     reservation container가 확보한 GPU UUID 4개로 제한
  -> Chart가 계산한 --device-ids가 P/D에 비중복으로 주입
  -> 실제 engine 초기화/메모리 점유도 지정 GPU에서만 발생
  -> "Using Intra-Node NVLink transport" 확인
```

실제 추론에서 Prefill Mooncake Transfer Engine:

```text
[REQUEST] submitTransferTask
[CTX] relocateSharedMemoryAddress:before-ipc-open
[IMPORT_SUCCESS]
...
KV Transfer metrics:
  Num successful transfers = 4
  Num failed transfers     = 0
  Avg xfer time            ~= 0.77 ms
  Avg MB per transfer      ~= 122.5 MB
  Throughput               ~= 159 GB/s
  Avg descriptors          ~= 112
```

Decode engine은 INFO level에서 transfer metric이 동일하게 보이지 않았지만,
`VLLM_LOGGING_LEVEL=DEBUG`로 검증했을 때 Prefill producer TP rank별 remote KV
receive/load가 실제 수행되는 것을 확인했다.

따라서 다음 항목은 runtime validated 상태다.

```text
Pod-local aggregate GPU reservation          PASS
reservation UUID discovery                   PASS
all-GPU device injection                     PASS
Cell-wide common CUDA namespace              PASS
automatic non-overlapping --device-ids       PASS
vLLM compute GPU partition                   PASS
Mooncake nvlink_intra initialization         PASS
cudaIpcOpenMemHandle                         PASS (cold start)
actual KV transfer                           PASS
Decode remote KV receive/load                PASS
```

중요: 이 검증은 **cold-start 정상 topology**에 대한 것이다.
Prefill/Decode container 단독 restart resilience는 별도 lifecycle 문제이며 아래
Failure/restart 항목에서 계속 blocker로 관리한다.

---

## 16. Failure / restart 주의사항

Mooncake Router는 direct URL startup에서 Prefill bootstrap을 조회하고 `engine_id`를 저장한다.

Prefill process가 재시작되면서 `engine_id`가 바뀌었는데 Router process가 그대로 살아 있으면 stale metadata로 Decode가 remote KV를 기다릴 위험이 있다.

Kubernetes에서 같은 Pod의 한 container가 재시작된다고 sibling container가 자동으로 같이 재시작되는 것은 아니다.

따라서 runtime certification에서는 다음 failure case를 별도 blocker로 본다.

```text
1. Prefill container restart
2. Router가 새로운 engine_id를 다시 얻는지 확인
3. 그렇지 않으면 Cell 전체 restart 정책 또는 Router refresh 기능 필요
```

단기 baseline에서 자동 refresh가 검증되지 않으면 **P/D engine restart 시 Cell Pod 전체 재생성**을 운영 정책으로 두는 것이 안전하다.

---

## 17. Metrics

Cell Router metrics:

```text
:29000/metrics
```

P/D engine metrics:

```text
Prefill :8101/metrics
Decode  :8201/metrics
Decode1 :8202/metrics
```

기존 Prometheus discovery가 `container_port == 8000`만 선택하면 다음이 누락된다.

- Router metrics :29000
- Prefill metrics :8101+
- Decode metrics :8201+

따라서 production readiness 전에 scrape discovery를 수정해야 한다.

Mooncake actual transfer 검증에서는 단순 request 성공뿐 아니라 producer-side transfer latency/bytes/descriptors 또는 equivalent connector metrics/log를 확인한다.

---

## 18. P1D2 비대칭 topology

예:

```text
8 GPU node

Prefill  TP4 -> 4 GPU
Decode0  TP2 -> 2 GPU
Decode1  TP2 -> 2 GPU
```

합계 8 GPU이므로 Kubernetes resource 예약은 가능하다.

```yaml
prefill:
  count: 1
  requestGPU: 4

decode:
  count: 2
  requestGPU: 2
```

`requestGPU`는 Mooncake P/D에서 **topology sizing source-of-truth**다. 실제 GPU
extended resource는 engine container가 아니라 reservation sidecar에 합산된다.

또한 각 engine의 `requestGPU`는 profile이 생성하는 **local GPU worker 수**와 반드시
일치해야 한다. 예를 들어 TP4 단일 local engine이면 requestGPU=4여야 한다.

heterogeneous TP의 KV connector 지원 여부는 vLLM/Mooncake 버전과 model architecture에 종속되므로 별도 runtime 검증 대상이다.

---

## 19. Runtime certification 순서

### Gate 1 — image

- vLLM version
- Mooncake package version
- import
- shared library
- CUDA ABI
- Mooncake init

### Gate 2 — Helm / GPU contract

- Cell Router image가 vllm-router인지
- command가 `vllm-router`인지
- `--vllm-pd-disaggregation`
- `--kv-connector mooncake`
- `--prefill URL BOOTSTRAP_PORT`
- `--decode URL`
- `kv_producer` / `kv_consumer`
- `gpu-reservation`만 `nvidia.com/gpu = total`을 갖는지
- P/D engine resource에는 `nvidia.com/gpu`가 없는지
- P/D engine에 `NVIDIA_VISIBLE_DEVICES=all`이 있는지
- 자동 `CNTR_GPU_IDX`가 중복 없이 전체 reservation range를 정확히 분할하는지
- 최종 KV JSON에 `mooncake_protocol=nvlink_intra`가 주입되는지

### Gate 3 — startup / device namespace

각 P/D engine에서:

```text
reservation UUID list 동일
CUDA_VISIBLE_DEVICES 동일
selected CNTR_GPU_IDX는 engine별 비중복
selected UUID가 기대 topology와 일치
```

를 먼저 확인한다.

그 다음 Router log에서:

```text
Querying Mooncake bootstrap
Got Mooncake engine_ids
```

확인.

### Gate 4 — API/catalog

- Router `/health`
- P/D `/health`
- P/D `/v1/models`
- Cell Router `/v1/models`
- 기본 contract에서는 profile에 정의한 model name/alias가 노출되는지 확인
- `servedModelNames`를 명시한 경우에만 Chart CLI override 결과 확인

### Gate 5 — actual P/D

한 요청에서:

```text
same transfer_id
P do_remote_decode=true
D do_remote_prefill=true
D remote_engine_id present
D remote_bootstrap_addr present
```

확인.

그리고:

- P actual KV send
- D actual KV receive/load
- prolonged `WAITING_FOR_REMOTE_KVS` 없음
- Decode prompt recompute 없음
- stream interruption 없음

### Gate 6 — Global Router

- primary discovery
- alias discovery
- primary request
- alias request

### Gate 7 — resilience

- P restart
- D restart
- Router restart
- stale Mooncake engine_id 여부
- Cell whole-restart policy 검증

### Gate 8 — metrics/performance

- Router/P/D scrape
- transfer metrics
- TTFT/TPOT/ITL
- concurrency
- long prompt
- soak

Gate 5 전에는 성능 benchmark로 넘어가지 않는다.

---

## 20. 결론

P/D Cell의 Router abstraction을 단순화한다.

```text
Global routing     = LMStack Router 가능
Cell orchestration = vllm-project/router 고정
KV transfer        = model-local connector
```

이 구조는 Global routing concern과 connector-aware P/D orchestration concern을 분리한다.

특히 Mooncake의 `transfer_id + bootstrap + engine_id` control-plane을 Router가 직접 이해하므로, LMStack의 generic response-driven orchestration에서 발생했던 silent Decode recompute 위험을 제거한다.
