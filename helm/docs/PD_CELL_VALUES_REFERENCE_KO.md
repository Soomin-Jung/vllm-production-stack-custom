# P/D Cell Values Reference — custom vLLM Production Stack 0.1.8

## 핵심 규칙

P/D Cell 내부 orchestrator는 **항상 `vllm-project/router`**다.

다음 field는 더 이상 지원하지 않는다.

```yaml
pdCellSpec.router.type
pdCellSpec.router.args
pdCellSpec.router.kvConnector
```

- Global `routerSpec`는 기존 LMStack Router용으로 유지 가능
- Cell Router image는 `pdCellSpec.router.repository/tag`에서 별도로 명시
- KV connector는 `models[].kvTransfer.connector`에서 선언
- Helm이 vLLM Router `--kv-connector`를 자동 결정

---

## 최상위 구조

```yaml
pdCellSpec:
  enabled: true
  hostPID: true
  router: {...}
  models:
    - name: ...
      repository: ...
      tag: ...
      kvTransfer: {...}
      prefill: {...}
      decode: {...}
```

---

## `pdCellSpec`

| field | 기본값 | 설명 |
|---|---|---|
| `enabled` | `false` | P/D Cell 리소스 생성 여부 |
| `imagePullPolicy` | `servingEngineSpec.imagePullPolicy` → 최종 fallback `IfNotPresent` | P/D engine image pull policy |
| `runtimeClassName` | `servingEngineSpec.runtimeClassName` | Pod RuntimeClass |
| `schedulerName` | `servingEngineSpec.schedulerName` | Kubernetes scheduler |
| `imagePullSecret` | empty | image pull secret |
| `serviceAccountName` | guardian 활성 시 Chart 전용 SA | ServiceAccount. 명시하면 guardian RoleBinding도 해당 SA에 연결 |
| `priorityClassName` | empty | PriorityClass |
| `progressDeadlineSeconds` | `1800` | Deployment progress deadline |
| `terminationGracePeriodSeconds` | `60` | Pod termination grace |
| `strategy` | RollingUpdate, maxSurge 0/maxUnavailable 1 | Deployment strategy |
| `podAnnotations` | `{}` | Pod annotations |
| `securityContext` | `{}` | Pod securityContext |
| `hostPID` | `true` (Mooncake/NIXL), 그 외 `false` | Pod가 host PID namespace를 사용. 현재 Mooncake `nvlink_intra` 검증 baseline에서는 `true` 필요 |
| `hostIPC` | `false` | Pod가 host IPC namespace를 사용. Issue #6 final A/B에서는 요구되지 않음 |
| `shareProcessNamespace` | `false` | Pod 내부 container끼리 process namespace 공유. `hostPID`와는 별개이며 Issue #6 fix가 아님 |
| `containerSecurityContext` | `{}` | P/D engine common container securityContext |
| `env` | `[]` | Cell common env |
| `envFrom` | `[]` | Cell common envFrom |
| `extraVolumes` | `[]` | Cell common volumes |
| `extraVolumeMounts` | `[]` | Cell common mounts |
| `nodeName` | empty | direct node pin |
| `nodeSelectorTerms` | `[]` | required node affinity terms |
| `affinity` | `{}` | explicit affinity; 있으면 nodeSelectorTerms보다 우선 |
| `tolerations` | `[]` | tolerations |
| `serviceType` | `ClusterIP` | Cell service type |
| `servicePort` | `servingEngineSpec.servicePort` | Cell service port |
| `serviceAnnotations` | `{}` | Cell service annotations |

### `pdCellSpec.guardian`

P/D Cell은 기본적으로 하나의 failure domain으로 동작한다.

```yaml
pdCellSpec:
  guardian:
    enabled: true
    pollIntervalSeconds: 2
    deleteGracePeriodSeconds: 5
    logHostPath: /var/log/vllm-pd-cell/guardian
    resources:
      requests:
        cpu: 20m
        memory: 64Mi
      limits:
        cpu: 100m
        memory: 128Mi
```

| field | 기본값 | 설명 |
|---|---:|---|
| `enabled` | `true` | whole-cell guardian 활성화 |
| `pollIntervalSeconds` | `2` | 자기 Pod status 조회 주기 |
| `deleteGracePeriodSeconds` | `5` | failure 감지 후 Pod DELETE grace period |
| `logHostPath` | `/var/log/vllm-pd-cell/guardian` | node-local guardian JSONL audit directory. absolute host path |
| `resources` | 20m/64Mi request, 100m/128Mi limit | guardian sidecar resource |

동작:

```text
모든 target status 관찰
  -> 하나라도 NotReady이면 kubelet startup/CrashLoopBackOff 유지

모든 target이 Ready로 회복
  -> restartCount > 0이면 partial-restart generation으로 판정
  -> ARMED 전에 whole-cell DELETE

restart 없이 P/D/Router/(공유 gpu-reservation) 모두 Ready
  -> restartCount=0 baseline 저장
  -> ARMED

이후 대상 container 중 하나 restartCount 증가
  -> guardian이 자기 Pod UID를 precondition으로 DELETE
  -> Deployment가 fresh P/D Cell Pod 생성
```

guardian lifecycle event는 다음 hostPath에 Pod UID별 JSONL 파일로 영속화한다.

```text
host:      /var/log/vllm-pd-cell/guardian
container: /var/log/pd-cell-guardian
file:      <namespace>_<pod>_<pod-uid>.jsonl
```

각 recycle record에는 trigger container와 Kubernetes container status의
`state` / `lastState`가 포함되므로 OOM/exit code/reason/finishedAt 같은 직전 종료
정보를 Pod 교체 뒤에도 분석할 수 있다. Alloy 같은 node-level collector는 hostPath의
`*.jsonl`을 수집 대상으로 사용한다. 감사로그 write 실패는 guardian의 lifecycle
보호 자체를 중단시키지 않는다.

guardian은 Kubernetes API를 호출할 수 있는 projected ServiceAccount token만 자기
container에 mount한다. Engine/Router에는 Pod delete token을 자동 mount하지 않도록
`automountServiceAccountToken: false`를 적용한다.

`serviceAccountName`을 생략하면 Chart가 release 전용 guardian ServiceAccount를 만들고,
명시한 경우 해당 ServiceAccount에 Pod `get/delete` RoleBinding을 추가한다.

### Guardian 비활성화와 Helm upgrade

`guardian.enabled=false`는 guardian sidecar, projected token, `pd-failure-domain` annotation,
Role/RoleBinding을 비활성화한다.

단, `pdCellSpec.enabled=true`인 동안에는 Chart 전용 guardian ServiceAccount와
guardian script ConfigMap을 **inert compatibility resource**로 유지한다. 이유는
`guardian=true -> false` RollingUpdate 중 old ReplicaSet이 잠시 기존 Pod template을
사용할 수 있기 때문이다. 이 두 리소스를 같은 Helm upgrade에서 즉시 삭제하면 old RS가
`ServiceAccount not found` 또는 ConfigMap mount 실패로 `FailedCreate`에 걸려 새
generation으로 전환하지 못할 수 있다.

비활성 상태의 전용 ServiceAccount는 `automountServiceAccountToken: false`이며
Role/RoleBinding이 렌더되지 않으므로 Pod delete 권한을 갖지 않는다. 해당 compatibility
리소스는 `pdCellSpec.enabled=false` 또는 release uninstall 시 제거된다.

상속 우선순위는 일반적으로:

```text
global / servingEngineSpec
  -> pdCellSpec
  -> models[]
  -> router/prefill/decode
```

단, **Global `routerSpec`의 image/tag는 Cell Router로 상속하지 않는다.** Global Router와 Cell orchestrator 구현이 다르기 때문이다.

---

## `pdCellSpec.router`

Cell Router는 vLLM Router 전용 설정이다.

```yaml
router:
  repository: registry.internal/vllm/vllm-router
  # NIXL rollout에서는 v0.1.15 + upstream PR #234가 반영된 사내 image를 pin한다.
  tag: v0.1.15-pr234
  port: 8000
  policy: round_robin
  prometheusPort: 29000
```

### 필수

| field | 설명 |
|---|---|
| `repository` | vllm-project/router 기반 image repository |
| `tag` | 검증한 version/tag. Mooncake baseline은 `v0.1.15`; NIXL rollout에서는 `v0.1.15 + PR #234` 소스 build를 별도 immutable tag/digest로 pin |

### 선택

| field | 기본값 | 설명 |
|---|---|---|
| `imagePullPolicy` | `IfNotPresent` | Router image pull policy |
| `port` | `8000` | Router API port |
| `policy` | `consistent_hash` | vLLM Router routing policy |
| `prometheusPort` | `29000` | Router metrics port. `0`이면 별도 metrics port 비활성 |
| `healthPath` | `/health` | Kubernetes probe path |
| `healthCheckInterval` | `30` | `--health-check-interval-secs` |
| `healthCheckTimeout` | `5` | `--health-check-timeout-secs` |
| `startupProbeInitialDelaySeconds` | `5` | Router startup probe |
| `startupProbePeriodSeconds` | `5` | Router startup probe |
| `startupProbeFailureThreshold` | `180` | Mooncake bootstrap wait 포함 약 15분 기본 budget |
| `livenessProbePeriodSeconds` | `10` | liveness period |
| `livenessProbeFailureThreshold` | `3` | liveness failures |
| `readinessProbePeriodSeconds` | `5` | readiness period |
| `readinessProbeFailureThreshold` | `3` | readiness failures |
| `resources` | CPU 1000m / memory 5Gi request | Router resources |
| `command` | `[vllm-router]` | 실행파일 path override만 허용 |
| `extraArgs` | `[]` | fixed P/D args 뒤에 추가할 Router option |
| `env` | `[]` | Router env |
| `envFrom` | `[]` | Router envFrom |
| `extraVolumes` | `[]` | Router-only volume definition |
| `extraVolumeMounts` | `[]` | Router-only mount |
| `containerSecurityContext` | `{}` | Router securityContext |

### 제거된 field

`router.type`:

```text
제거 이유: Cell 내부 Router implementation을 vllm-router로 고정
```

`router.args`:

```text
제거 이유: full override가 --vllm-pd-disaggregation / connector contract를 우회할 수 있음
```

`router.kvConnector`:

```text
제거 이유: engine connector와 Router connector가 분리 설정되면 protocol mismatch 가능
```

---

## 생성되는 Router command

Mooncake P1D2 예:

```text
command: [vllm-router]

--host 0.0.0.0
--port 8000
--policy consistent_hash
--vllm-pd-disaggregation
--kv-connector mooncake
--health-check-interval-secs 30
--health-check-timeout-secs 5
--prefill http://127.0.0.1:8101 9001
--decode http://127.0.0.1:8201
--decode http://127.0.0.1:8202
--prometheus-host 0.0.0.0
--prometheus-port 29000
```

NIXL에서는 bootstrap port를 `--prefill` 뒤에 붙이지 않는다.

NIXL P1D1에서 `router.policy: round_robin`을 지정하면 고객사 공유 command와
핵심 orchestration argument가 동일하게 렌더된다.

```text
--policy round_robin
--vllm-pd-disaggregation
--kv-connector nixl
--prefill http://127.0.0.1:8101
--decode http://127.0.0.1:8201
--host 0.0.0.0
--port 8000
```

`prefill.count` / `decode.count`가 증가하면 동일한 `--prefill` /
`--decode` option을 반복 생성하므로 P1D3, P3D5 등 비대칭 topology도
manifest 수준에서 지원한다. 단, 이 Chart의 P/D Cell은 한 Pod/한 Node이므로
`P_count * P_requestGPU + D_count * D_requestGPU`에 해당하는 전체 GPU resource가
한 Node에 실제로 존재해야 schedule된다.

Router upstream issue #233 / PR #234는 NIXL protocol bug가 아니다. Router가
`reasoning_effort`를 역직렬화할 때 `none/minimal/xhigh/max`를 enum에 포함하지 않아
backend 도달 전에 HTTP 400을 반환하던 protocol parsing 문제다. 따라서
KV connector 전환과 Router patch는 독립적인 변경축으로 관리한다.

---

## `models[]`

| field | 필수 | 설명 |
|---|---|---|
| `name` | O | Kubernetes resource identity. release 안에서 unique |
| `repository` | O | P/D vLLM image |
| `tag` | O | P/D image tag |
| `servedModelNames` | X | 선택적 CLI override. 설정할 때만 P/D에 `--served-model-name` 주입 |
| `servedModelName` | X | 구 values 호환용 단일 이름 override |
| `replicaCount` | X | 기본 1, 0 이상 |
| `modelType` | X | metadata용, 기본 chat |
| `kvTransfer` | O | 모델별 KV transfer 설정 |
| `prefill` | O | Prefill topology |
| `decode` | O | Decode topology |
| `router` | X | 이 모델에서만 Router image/resource/probe/env override |

모델 단위로 `imagePullPolicy`, scheduling, service, env/mount/security 값을 override할 수 있다.

---

## `models[].kvTransfer`

KV transfer는 모델/profile/topology에 종속되므로 `pdCellSpec` 공통값으로 상속하지 않는다.

```yaml
kvTransfer:
  connector: MooncakeConnector
  bootstrapPortBase: 9001
  abortRequestTimeout: 600
  config:
    kv_load_failure_policy: fail
    kv_connector_extra_config:
      num_workers: 16

# mooncake_protocol은 입력하지 않는다.
# node-local P/D Cell이 nvlink_intra를 강제한다.
  prefillConfig: {}
  decodeConfig: {}
```

### field

| field | 기본값 | 설명 |
|---|---|---|
| `connector` | 필수 | vLLM engine KV connector class |
| `config` | `{}` | P/D 공통 `KVTransferConfig` |
| `prefillConfig` | `{}` | Prefill merge override |
| `decodeConfig` | `{}` | Decode merge override |
| `bootstrapPortBase` | `9001` | Mooncake Prefill bootstrap base |
| `abortRequestTimeout` | `600` | Mooncake abort timeout env |
| `nixlSideChannelEnabled` | connector 자동 판정 | MultiConnector 등에서 NIXL side channel 강제 시 사용. NIXL connector 자체에서는 `false` 금지 |
| `nixl.sideChannelHost` | `localhost` | `VLLM_NIXL_SIDE_CHANNEL_HOST`. 현재 node-local/same-Pod Cell에서는 localhost 권장 |
| `nixl.ucxTls` | 미설정 | 설정 시 `UCX_TLS`를 모든 P/D NIXL engine에 주입 |
| `nixl.ucxNetDevices` | 미설정 | 설정 시 `UCX_NET_DEVICES`를 모든 P/D NIXL engine에 주입 |

Helm이 마지막에 강제로 설정:

```text
Prefill kv_role = kv_producer
Decode  kv_role = kv_consumer
kv_connector    = connector field
```

---

## Router connector 자동 매핑

| `models[].kvTransfer.connector` | Router mode |
|---|---|
| 이름에 `Mooncake` 포함 | `mooncake` |
| 이름에 `Nixl` 포함 | `nixl` |
| 이름에 `Mori` 또는 `MoRI` 포함 | `moriio` |
| 그 외 | Helm render fail |

현재 고객사 사용값:

```yaml
connector: MooncakeConnector
```

---

## Mooncake 전용 env

Prefill:

```text
VLLM_MOONCAKE_BOOTSTRAP_PORT=9001+index
VLLM_MOONCAKE_ABORT_REQUEST_TIMEOUT=600
```

Decode:

```text
VLLM_MOONCAKE_ABORT_REQUEST_TIMEOUT=600
```

Decode에는 bootstrap server를 띄우지 않는다.

---

## 공유 GPU reservation / launcher (Mooncake 및 NIXL 공통)

exact `MooncakeConnector`에서는 GPU allocation semantics가 다른 connector와 다르다.

운영자는 기존 topology field만 선언한다.

```yaml
prefill:
  count: 2
  requestGPU: 2

decode:
  count: 1
  requestGPU: 4
```

Chart는 자동으로:

```text
total GPU = 2*2 + 1*4 = 8

gpu-reservation -> nvidia.com/gpu: 8
prefill-0       -> CNTR_GPU_IDX=0,1
prefill-1       -> CNTR_GPU_IDX=2,3
decode-0        -> CNTR_GPU_IDX=4,5,6,7
```

을 만든다.

Mooncake engine container에는 GPU extended resource를 직접 붙이지 않는다. 대신:

```text
NVIDIA_VISIBLE_DEVICES=all
```

을 manifest에 넣고, Chart launcher가 reservation sidecar의 PCI-bus 정렬 UUID 목록을 읽어
모든 P/D engine에 동일한 `CUDA_VISIBLE_DEVICES`를 설정한다. 실제 compute GPU는
vLLM 0.26.0 `--device-ids=<CNTR_GPU_IDX>`로 선택한다.

따라서 `requestGPU`의 의미는 Mooncake에서:

```text
engine topology / local worker GPU count
+ CPU/memory sizing 기준
+ pod reservation 합계 계산 기준
```

이다.

다음 값은 Chart-owned이므로 사용자가 설정하면 안 된다.

```text
kv_connector_extra_config.mooncake_protocol
--device-ids
```

`mooncake_protocol`은 `nvlink_intra`로 강제된다.

`prefill.command` / `decode.command`를 Mooncake에서 지정해야 한다면
`[<vllm-binary>, serve]` 형태만 허용되며, 실제 container command는 Chart launcher로
override된다.

Issue #6의 controlled A/B에서 `hostPID=false`는 Runtime/Driver CUDA IPC 양쪽에서
intermittent `201 (invalid device context)`를 재현했고, `hostPID=true` 단독 적용 후
pristine Mooncake v0.3.10 Runtime IPC에서도 재현되지 않았다.

따라서 현재 Mooncake `nvlink_intra` 검증 baseline은:

```yaml
pdCellSpec:
  hostPID: true
```

이다. `hostIPC`와 `shareProcessNamespace`는 final fix의 요구사항으로 확인되지 않았다.
특히 `shareProcessNamespace=true`는 Pod 내부 PID namespace 공유일 뿐 `hostPID=true`
와 동일하지 않다. `hostPID`는 process isolation을 약화시키므로 connector 공통
기본값은 계속 `false`로 유지하고 Mooncake deployment profile에서 명시한다.
기존 engine `/dev/shm` mount는 별도로 유지한다.

주의: engine container는 node GPU device가 inject될 수 있으므로 Linux `/dev` 수준의
hard isolation은 아니다. CUDA runtime에서는 reservation UUID만 CVD로 노출한다.

또한 vLLM `--device-ids`는 Ray executor에서 효과가 없으므로 이 contract는
native multiprocessing 기준이다.

---

## NIXL 전용 env / UCX contract

NIXL connector일 때 Chart는 각 P/D engine에 최소 다음 값을 주입한다.

```text
VLLM_NIXL_SIDE_CHANNEL_PORT
VLLM_NIXL_SIDE_CHANNEL_HOST
```

side-channel port는 phase/index별로 고유하게 생성한다.

```text
Prefill i = prefill.sideChannelPortBase + i * prefill.sideChannelPortStride
Decode  i = decode.sideChannelPortBase  + i * decode.sideChannelPortStride
```

기본값은 각각 `5600/5700`, stride는 `1`이다. vLLM DP를 한 engine process 안에서
사용하면 NIXL이 base port에서 DP rank만큼 추가 port를 사용할 수 있으므로,
**다음 engine의 base port와 겹치지 않도록 stride를 DP rank span 이상으로 잡는다.**
예를 들어 DP=8이면 stride 16처럼 여유 있게 둔다.

현재 DS Cloud처럼 **single-node / non-IB / NVLink-capable** 환경을 우선 검증할 때는
다음 값을 명시적으로 사용할 수 있다.

```yaml
kvTransfer:
  connector: NixlConnector
  nixl:
    sideChannelHost: localhost
    ucxTls: cuda_ipc,cuda_copy,sm,self,tcp
    ucxNetDevices: lo
```

이 설정은 UCX의 IB RC transport를 목록에서 제외하고 node-local CUDA IPC 경로를
허용하기 위한 검증 profile이다. `cuda_ipc`는 같은 node의 GPU P2P 전송에 사용되며
실제 물리 경로는 topology에 따라 NVLink 또는 PCIe가 될 수 있다.
따라서 `UCX_NET_DEVICES=lo` 또는 NIXL log에 특정 문자열이 없다는 사실만으로
"NVLink를 강제/증명했다"고 해석하지 않는다. 실제 경로 확인은
`nvidia-smi topo -m`, UCX/NIXL debug log, 그리고 동일 payload 전송 성능으로
교차 검증한다.

### NIXL CUDA IPC와 공유 GPU reservation

NIXL은 Mooncake와 동일하게 Cell 전체 GPU reservation/launcher를 항상 사용한다.
별도의 GPU 할당 방식 옵션은 없다.

```yaml
pdCellSpec:
  hostPID: true
  hostIPC: false
  models:
    - name: example
      kvTransfer:
        connector: NixlConnector
```

두 connector는 같은 예약/노출 계약을 사용한다. reservation container만
`sum(count * requestGPU)` GPU를 요청하고 P/D engine은 전체 예약 UUID를 공통으로
보며, launcher의 `--device-ids`로 서로 겹치지 않는 compute GPU를 선택한다.
NIXL backend, side-channel, connector role과 UCX 설정은 유지되며 Mooncake protocol은
주입하지 않는다. guardian도 reservation container를 감시한다.

공유 reservation의 `hostPID` 기본값은 `true`이다. `shareProcessNamespace=true`만
지정하면 hostPID 기본값은 false가 되어 Pod-scoped PID 공유를 사용한다.
`hostPID=true` 또는 `shareProcessNamespace=true`가 필요하며 둘 다 false이면 렌더링을 거부한다. 제공 예제는 기존 Mooncake 현장 A/B가 검증한 `hostPID=true`를
유지한다. Pod-scoped PID 공유는 대안으로 지원하지만 이 환경에서 NIXL 검증은 필요하다.
`hostIPC`나 `privileged`를 자동 활성화하지 않는다.

이 판단은 [CUDA IPC Kubernetes 실험](https://harche.github.io/2025/10/15/kubernetes-cuda-ipc-permissions.html)과
그 [단일 Pod CUDA IPC 코드](https://github.com/harche/cuda-ipc-debugging/tree/main/single-pod-example),
[UCX 컨테이너 GPU 가시성 가이드](https://docs.nvidia.com/cudf-spark/latest/additional-functionality/rapids-shuffle.html)에
근거한다. 자료의 privileged 예제가 reservation 필요성을 직접 증명하는 것은 아니다.
우리의 기존 NVIDIA runtime에서 검증한 GPU 노출 구조를 재사용하는 선택이다.

`NVIDIA_VISIBLE_DEVICES=all`이 실제 장치 접근을 제공하는지는 NVIDIA runtime/device
plugin 설정에 달려 있다. launcher는 NVML UUID 가시성을 확인하지만 CUDA IPC 권한이나
NVLink 경로까지 증명하지 않는다. 컨테이너별 device node/cgroup 접근, 실제 UCX 선택 경로,
장시간 전송은 GPU 노드에서 확인해야 한다. broad device exposure는 hard isolation이 아니며,
MIG/time-slicing/DRA 계약으로 사용하면 안 된다.

`internalPortMode`:

| 값 | env |
|---|---|
| `vllm` | `VLLM_PORT` |
| `dp` | `VLLM_DP_MASTER_PORT` |
| `auto` | 둘 다 강제하지 않음 |

---

## API key

`servingEngineSpec.vllmApiKey`가 있으면:

P/D engine:

```text
VLLM_API_KEY
```

Cell Router:

```text
VLLM_API_KEY
OPENAI_API_KEY
```

두 Router env는 같은 secret을 가리킨다.

---

## `prefill` / `decode`

공통 핵심 field:

| field | 기본값 | 설명 |
|---|---|---|
| `count` | 필수 | phase container 수 |
| `requestGPU` | 필수 | engine local GPU worker 수. 공유 reservation에서는 reservation sidecar 합계/자동 device index 계산 기준 |
| `profile` | 필수 | vLLM `--config` path |
| `portBase` | P 8101 / D 8201 | HTTP base |
| `internalPortMode` | `vllm` | `vllm\|dp\|auto` |
| `internalPortBase` | P 20000 / D 30000 | internal port |
| `internalPortStride` | `100` | index stride |
| `dpMasterPortBase` | P 24000 / D 34000 | DP master |
| `sideChannelPortBase` | P 5600 / D 5700 | NIXL side-channel base port |
| `sideChannelPortStride` | `1` | engine index 간 NIXL side-channel base port 간격. DP 사용 시 DP rank port span보다 크게 설정 |
| `command` | `[vllm, serve]` 계열 | engine command override. 공유 reservation에서는 `[<binary>, serve]`만 허용 |
| `extraArgs` | `[]` | engine extra flags |
| `env` | `[]` | phase env |
| `envFrom` | `[]` | phase envFrom |
| `extraVolumes` | `[]` | phase volumes |
| `extraVolumeMounts` | `[]` | phase mounts |
| `containerSecurityContext` | `{}` | phase security |
| `kvTransferConfig` | `{}` | final phase KV override |

기타 connector는 기존 `chart.resources` helper를 사용한다.

MooncakeConnector와 NIXL은 `requestGPU`에 비례한 CPU/memory sizing은 유지하지만 engine
container의 GPU extended resource를 제거하고, 모든 P/D GPU 합계를
`gpu-reservation` container에 한 번만 요청한다.

---

## served model name 소유권

기본 contract에서는 Chart가 served model name을 주입하지 않는다.

```text
servedModelNames 미설정
  -> Prefill/Decode에 --served-model-name 없음
  -> 각 vLLM --config/profile의 served_model_name 사용
  -> Cell vLLM Router /v1/models
  -> 첫 Prefill worker의 /v1/models를 그대로 proxy
```

따라서 운영 baseline에서는 모델명/alias를 vLLM profile에서 관리하는 것을 권장한다.

Chart 수준에서 명시적으로 덮어써야 할 때만 다음 선택 옵션을 사용한다.

```yaml
servedModelNames:
  - Qwen3.6-27B-PD
  - test-alias
```

이 경우에만 Prefill/Decode 양쪽에 동일한:

```text
--served-model-name Qwen3.6-27B-PD test-alias
```

가 주입된다.

구 values 호환을 위해 `servedModelName` 단일 값도 선택 override로 유지한다.

---

## Service

모델마다:

```text
<release>-<name>-pd-engine-service
```

를 생성한다.

Service target:

```text
pd-router named port -> Cell Router :8000
```

P/D engine port를 외부 service로 직접 노출하지 않는다.

---

## Metrics

Cell Router:

```text
29000/metrics
```

Prefill/Decode:

```text
8101+/metrics
8201+/metrics
```

기존 Prometheus relabel이 `container_port=8000`만 선택하면 P/D와 Router metrics port 29000을 놓치므로 별도 scrape rule 보완이 필요하다.

---

## 운영 금지/주의

### 금지

```yaml
router:
  type: lmstack
```

Cell 내부 LMStack orchestrator는 Mooncake contract mismatch 때문에 제거했다.

### 주의

Mooncake direct URL Router와 Mooncake CUDA IPC state는 partial engine restart에서 stale generation 문제가 발생할 수 있다.

현재 운영 contract는 partial restart 복구가 아니라 **P/D Cell 전체를 하나의 failure domain으로 취급**하는 것이다. Chart guardian은 startup 중 restart가 있었더라도 아직 NotReady인 동안에는 kubelet backoff를 방해하지 않고, 모든 target이 Ready로 회복된 시점에 restartCount > 0이면 ARMED 전에 stale generation을 recycle한다. ARMED 이후 P/D/Router/gpu-reservation의 restartCount 증가 역시 자기 Pod 전체 recycle로 연결한다.

---

## 권장 Mooncake baseline

```yaml
pdCellSpec:
  enabled: true
  router:
    repository: registry.internal/vllm/vllm-router
    tag: v0.1.15
    policy: consistent_hash
    prometheusPort: 29000

  models:
    - name: qwen-p1d2
      # served model name은 기본적으로 profile에서 관리
      repository: registry.internal/vllm/vllm-openai
      tag: v0.26.0-cu129-mooncake-0.3.10-post2
      kvTransfer:
        connector: MooncakeConnector
        bootstrapPortBase: 9001
        config:
          kv_load_failure_policy: fail
          # mooncake_protocol is chart-managed
          kv_connector_extra_config: {}
      prefill:
        count: 1
        requestGPU: 4
        profile: /profiles/qwen/prefill-tp4.yaml
      decode:
        count: 2
        requestGPU: 2
        profile: /profiles/qwen/decode-tp2.yaml
```
