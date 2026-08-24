# P/D Cell values 속성 레퍼런스

이 문서는 `deployment-pd-cell.yaml`과 `service-pd-cell.yaml`이 직접 참조하는 값을 정리한 레퍼런스다. 최소 배포 예제는 `helm/examples/pd-cell-values.yaml`, 모든 옵션을 포함한 교보재는 `helm/examples/pd-cell-values-full.yaml`을 사용한다.

## 표 읽는 법

- 필수 여부의 `조건부`는 특정 기능을 선택했을 때만 필수라는 뜻이다.
- 기본값의 `상속`은 같은 행의 설명에 적힌 기존 values 또는 상위 scope에서 가져온다.
- Kubernetes 원시 map/list는 chart가 내부 field를 제한하지 않고 그대로 manifest에 출력한다.
- 명시적 우선순위는 `global/기존 spec → pdCellSpec → models[] → router 또는 prefill/decode → Helm 강제 runtime 값`이다.
- `extraVolumes`는 Pod 단위라 router/prefill/decode 아래에 선언해도 하나의 Pod volume 집합으로 합쳐진다. 같은 `name`을 여러 scope에서 쓰면 더 구체적인 scope가 덮어쓴다.

## 기존 values에서 상속하는 값

### `global`

| 속성 경로 | 필수 | 기본값, 타입 | 용도·설명 |
|---|---|---|---|
| `global.env` | 아니오 | `[]`, `EnvVar[]` | 모든 Cell container의 첫 env layer. 같은 `name`은 하위 scope가 덮어쓴다. |
| `global.envFrom` | 아니오 | `[]`, `EnvFromSource[]` | 모든 Cell container에 순서대로 추가한다. |
| `global.extraVolumes` | 아니오 | `[]`, `Volume[]` | 모든 Cell Pod의 공통 volume. |
| `global.extraVolumeMounts` | 아니오 | `[]`, `VolumeMount[]` | 모든 Cell container의 첫 mount layer. |

### `servingEngineSpec`

| 속성 경로 | 필수 | 기본값, 타입 | 용도·설명 |
|---|---|---|---|
| `servingEngineSpec.labels` | 아니오 | `{}`, `map[string]string` | Deployment와 Pod, Service에 `chart.engineLabels`로 추가한다. |
| `servingEngineSpec.imagePullPolicy` | 아니오 | chart values의 `Always`, `string` | Prefill/Decode image pull policy의 최하위 기본값. |
| `servingEngineSpec.runtimeClassName` | 아니오 | chart values의 `nvidia`, `string` | Cell Pod RuntimeClass 기본값. |
| `servingEngineSpec.schedulerName` | 아니오 | chart values 값, `string` | Cell Pod scheduler 기본값. |
| `servingEngineSpec.tolerations` | 아니오 | chart values의 GPU toleration, `Toleration[]` | 모든 Cell Pod에 항상 먼저 추가한다. `pdCellSpec`과 model toleration은 뒤에 append한다. |
| `servingEngineSpec.servicePort` | 아니오 | chart values의 `80`, `integer` | Cell Service port의 최하위 기본값. |
| `servingEngineSpec.securityContext` | 아니오 | `{}`, `PodSecurityContext` | Cell Pod security context의 최하위 기본값. |
| `servingEngineSpec.containerSecurityContext` | 아니오 | chart values 값, `SecurityContext` | Prefill/Decode security context의 최하위 기본값. |
| `servingEngineSpec.startupProbe` | 아니오 | chart values map, `Probe` | P/D engine startup probe의 path/timing 기본값. 실제 port는 각 engine port로 강제한다. |
| `servingEngineSpec.livenessProbe` | 아니오 | chart values map, `Probe` | P/D engine liveness probe의 path/timing 기본값. |
| `servingEngineSpec.readinessProbe` | 아니오 | chart values map, `Probe` | P/D engine readiness probe의 path/timing 기본값. |
| `servingEngineSpec.vllmApiKey` | 아니오 | 없음, `string` 또는 `object` | string이면 release Secret, object면 `secretName`/`secretKey`를 router와 P/D engine의 `VLLM_API_KEY`로 주입한다. |

### `routerSpec`

| 속성 경로 | 필수 | 기본값, 타입 | 용도·설명 |
|---|---|---|---|
| `routerSpec.repository` | 조건부 | chart values 값, `string` | `pdCellSpec.router.repository`를 생략했을 때 Cell router image repository. 둘 다 없으면 필수 오류. |
| `routerSpec.tag` | 조건부 | chart values 값, `string` | Cell router image tag 기본값. 운영에서는 검증한 tag를 pin한다. |
| `routerSpec.imagePullPolicy` | 아니오 | `IfNotPresent`, `string` | Cell router image pull policy 기본값. |
| `routerSpec.resources` | 아니오 | chart values map, `ResourceRequirements` | Cell router resource 기본값. 비어 있으면 request `1000m` CPU와 `5Gi` memory. |
| `routerSpec.containerSecurityContext` | 아니오 | `{}`, `SecurityContext` | Cell router security context 기본값. |

## `pdCellSpec` 공통 속성

| 속성 경로 | 필수 | 기본값, 타입 | 용도·설명 |
|---|---|---|---|
| `pdCellSpec.enabled` | 예 | `false`, `boolean` | `true`일 때만 P/D Deployment와 Service를 렌더링한다. |
| `pdCellSpec.models` | 예 | `[]`, `object[]` | 모델 topology 목록. 항목마다 Deployment와 Service 한 세트를 만든다. |
| `pdCellSpec.imagePullPolicy` | 아니오 | `servingEngineSpec.imagePullPolicy`, `string` | 모든 P/D engine 공통 image pull policy. |
| `pdCellSpec.runtimeClassName` | 아니오 | `servingEngineSpec.runtimeClassName`, `string` | 모든 Cell Pod 공통 RuntimeClass. 빈 문자열로 상속을 끌 수 있다. |
| `pdCellSpec.schedulerName` | 아니오 | `servingEngineSpec.schedulerName`, `string` | 모든 Cell Pod 공통 scheduler. |
| `pdCellSpec.imagePullSecret` | 아니오 | 없음, `string` | private registry Secret 이름. |
| `pdCellSpec.serviceAccountName` | 아니오 | 없음, `string` | Cell Pod ServiceAccount 공통값. |
| `pdCellSpec.priorityClassName` | 아니오 | 없음, `string` | Cell Pod PriorityClass 공통값. |
| `pdCellSpec.progressDeadlineSeconds` | 아니오 | `1800`, `integer` | Deployment progress deadline 공통값. |
| `pdCellSpec.terminationGracePeriodSeconds` | 아니오 | `60`, `integer` | Cell Pod 종료 유예 시간 공통값. |
| `pdCellSpec.strategy` | 아니오 | `RollingUpdate(maxSurge=0,maxUnavailable=1)`, `DeploymentStrategy` | 모든 Cell Deployment의 기본 strategy. |
| `pdCellSpec.podAnnotations` | 아니오 | `{}`, `map[string]string` | Cell Pod annotation 공통값. model annotation과 merge한다. |
| `pdCellSpec.securityContext` | 아니오 | 상속, `PodSecurityContext` | Cell Pod security context 공통 override. |
| `pdCellSpec.containerSecurityContext` | 아니오 | 상속, `SecurityContext` | Prefill/Decode security context 공통 override. |
| `pdCellSpec.env` | 아니오 | `[]`, `EnvVar[]` | 모든 Cell container env. `global.env` 뒤에 merge한다. |
| `pdCellSpec.envFrom` | 아니오 | `[]`, `EnvFromSource[]` | 모든 Cell container envFrom. |
| `pdCellSpec.extraVolumes` | 아니오 | `[]`, `Volume[]` | 모든 Cell Pod 공통 volume. |
| `pdCellSpec.extraVolumeMounts` | 아니오 | `[]`, `VolumeMount[]` | 모든 Cell container 공통 mount. |
| `pdCellSpec.nodeName` | 아니오 | 없음, `string` | Pod를 특정 Node에 직접 고정. 설정하면 nodeSelectorTerms를 사용하지 않는다. |
| `pdCellSpec.nodeSelectorTerms` | 아니오 | `[]`, `NodeSelectorTerm[]` | affinity가 비어 있고 nodeName이 없을 때 required node affinity로 변환한다. |
| `pdCellSpec.affinity` | 아니오 | `{}`, `Affinity` | Cell Pod affinity. 비어 있지 않으면 nodeSelectorTerms보다 우선한다. |
| `pdCellSpec.tolerations` | 아니오 | `[]`, `Toleration[]` | servingEngineSpec toleration 뒤에 append하는 공통 toleration. |
| `pdCellSpec.serviceType` | 아니오 | `ClusterIP`, `string` | Cell Service type 공통값. |
| `pdCellSpec.servicePort` | 아니오 | `servingEngineSpec.servicePort`, `integer` | Cell Service 노출 port 공통값. target은 항상 router named port다. |
| `pdCellSpec.serviceAnnotations` | 아니오 | `{}`, `map[string]string` | Cell Service annotation 공통값. |
| `pdCellSpec.router` | 조건부 | `{}`, `object` | 모든 model의 Cell router 기본값. image는 routerSpec에서 상속 가능하다. |
| `pdCellSpec.kvTransfer` | 조건부 | `{}`, `object` | 모든 model의 KV transfer 기본값. 최소 `connector`가 필요하다. |

## `pdCellSpec.models[]`

| 속성 경로 | 필수 | 기본값, 타입 | 용도·설명 |
|---|---|---|---|
| `pdCellSpec.models[].name` | 예 | 없음, `string` | Kubernetes resource identity. 한 Helm release의 models 안에서 고유해야 한다. |
| `pdCellSpec.models[].servedModelNames` | 아니오 | `[name]`, `string[]` | vLLM profile의 `served-model-name` 순서와 맞출 model ID 목록. 첫 항목이 primary이고 나머지는 alias다. Helm이 하나의 `--served-model-name name alias...` CLI로 P/D 양쪽에 주입한다. |
| `pdCellSpec.models[].servedModelName` | 아니오 | `name`, `string` 또는 `string[]` | 이전 values 호환 필드. `servedModelNames`가 있으면 무시한다. 배열도 허용한다. |
| `pdCellSpec.models[].repository` | 예 | 없음, `string` | 모든 Prefill/Decode container image repository. |
| `pdCellSpec.models[].tag` | 예 | 없음, `string` | 모든 Prefill/Decode container image tag. |
| `pdCellSpec.models[].replicaCount` | 아니오 | `1`, `integer >= 0` | Cell Pod replica 수. `0`이면 Deployment/Service는 유지하고 Pod만 0개로 둔다. |
| `pdCellSpec.models[].modelType` | 아니오 | `chat`, `string` | LMStack Router static health check model type. |
| `pdCellSpec.models[].prefill` | 예 | 없음, `object` | Prefill container topology와 runtime. |
| `pdCellSpec.models[].decode` | 예 | 없음, `object` | Decode container topology와 runtime. |
| `pdCellSpec.models[].router` | 아니오 | `pdCellSpec.router`, `object` | 이 model만 Cell router 값을 deep-merge override. |
| `pdCellSpec.models[].kvTransfer` | 아니오 | `pdCellSpec.kvTransfer`, `object` | 이 model만 KV transfer 값을 deep-merge override. |
| `pdCellSpec.models[].imagePullPolicy` | 아니오 | 상속, `string` | 이 model의 P/D engine image policy override. |
| `pdCellSpec.models[].runtimeClassName` | 아니오 | 상속, `string` | 이 Cell Pod RuntimeClass override. |
| `pdCellSpec.models[].schedulerName` | 아니오 | 상속, `string` | 이 Cell Pod scheduler override. |
| `pdCellSpec.models[].imagePullSecret` | 아니오 | 상속, `string` | 이 Cell Pod registry Secret override. |
| `pdCellSpec.models[].serviceAccountName` | 아니오 | 상속, `string` | 이 Cell Pod ServiceAccount override. |
| `pdCellSpec.models[].priorityClassName` | 아니오 | 상속, `string` | 이 Cell Pod PriorityClass override. |
| `pdCellSpec.models[].progressDeadlineSeconds` | 아니오 | 상속, `integer` | 이 Deployment progress deadline override. |
| `pdCellSpec.models[].terminationGracePeriodSeconds` | 아니오 | 상속, `integer` | 이 Pod 종료 유예 시간 override. |
| `pdCellSpec.models[].strategy` | 아니오 | 상속, `DeploymentStrategy` | 이 Deployment strategy override. |
| `pdCellSpec.models[].podAnnotations` | 아니오 | `{}`, `map[string]string` | 공통 podAnnotations와 merge. |
| `pdCellSpec.models[].securityContext` | 아니오 | 상속, `PodSecurityContext` | 이 Cell Pod security context override. |
| `pdCellSpec.models[].containerSecurityContext` | 아니오 | 상속, `SecurityContext` | 이 model의 P/D container security context override. |
| `pdCellSpec.models[].env` | 아니오 | `[]`, `EnvVar[]` | 이 model의 router, Prefill, Decode 공통 env. |
| `pdCellSpec.models[].envFrom` | 아니오 | `[]`, `EnvFromSource[]` | 이 model의 모든 container 공통 envFrom. |
| `pdCellSpec.models[].envFromSecret.name` | 아니오 | 없음, `string` | 기존 단축 문법. 지정 Secret을 모든 container envFrom에 추가한다. |
| `pdCellSpec.models[].extraVolumes` | 아니오 | `[]`, `Volume[]` | 이 Cell Pod 공통 volume. |
| `pdCellSpec.models[].extraVolumeMounts` | 아니오 | `[]`, `VolumeMount[]` | 이 model의 모든 container 공통 mount. |
| `pdCellSpec.models[].nodeName` | 아니오 | 상속, `string` | 이 Cell Pod nodeName override. |
| `pdCellSpec.models[].nodeSelectorTerms` | 아니오 | 상속, `NodeSelectorTerm[]` | 이 Cell Pod selector terms override. 빈 배열로 공통값을 끌 수 있다. |
| `pdCellSpec.models[].affinity` | 아니오 | 상속, `Affinity` | 이 Cell Pod affinity override. |
| `pdCellSpec.models[].tolerations` | 아니오 | `[]`, `Toleration[]` | servingEngineSpec와 pdCellSpec toleration 뒤에 append. |
| `pdCellSpec.models[].serviceType` | 아니오 | 상속, `string` | 이 model Service type override. |
| `pdCellSpec.models[].servicePort` | 아니오 | 상속, `integer` | 이 model Service port override. |
| `pdCellSpec.models[].serviceAnnotations` | 아니오 | `{}`, `map[string]string` | 공통 serviceAnnotations와 merge. |

## `pdCellSpec.models[].prefill` / `decode`

아래 표의 `{phase}`는 `prefill` 또는 `decode`다. 두 scope는 같은 field를 지원하지만 port 기본값과 KV role이 다르다.

| 속성 경로 | 필수 | 기본값, 타입 | 용도·설명 |
|---|---|---|---|
| `pdCellSpec.models[].{phase}.count` | 예 | 없음, `integer >= 1` | Cell Pod 안 해당 phase container 수. vLLM TP/DP 크기가 아니다. |
| `pdCellSpec.models[].{phase}.profile` | 예 | 없음, `string` | `vllm serve --config`에 전달할 profile 경로. |
| `pdCellSpec.models[].{phase}.requestGPU` | 예 | 없음, `integer >= 0` | container당 GPU request/limit. profile의 실제 local GPU 사용량과 맞춘다. |
| `pdCellSpec.models[].{phase}.requestCPU` | 아니오 | `4000m × requestGPU`, `string` | container CPU request. |
| `pdCellSpec.models[].{phase}.requestMemory` | 아니오 | `10Gi × requestGPU`, `string` | container memory request. |
| `pdCellSpec.models[].{phase}.requestGPUType` | 아니오 | `nvidia.com/gpu`, `string` | GPU extended resource key. |
| `pdCellSpec.models[].{phase}.requestGPUMem` | 아니오 | 없음, `string` | HAMi GPU memory request. |
| `pdCellSpec.models[].{phase}.requestGPUMemPercentage` | 아니오 | 없음, `string` | HAMi GPU memory percentage request. |
| `pdCellSpec.models[].{phase}.requestGPUCores` | 아니오 | 없음, `string` | HAMi GPU core percentage request. |
| `pdCellSpec.models[].{phase}.limitCPU` | 아니오 | 없음, `string` | container CPU limit. |
| `pdCellSpec.models[].{phase}.limitMemory` | 아니오 | 없음, `string` | container memory limit. |
| `pdCellSpec.models[].{phase}.limitGPUMem` | 아니오 | 없음, `string` | HAMi GPU memory limit. |
| `pdCellSpec.models[].{phase}.limitGPUMemPercentage` | 아니오 | 없음, `string` | HAMi GPU memory percentage limit. |
| `pdCellSpec.models[].{phase}.limitGPUCores` | 아니오 | 없음, `string` | HAMi GPU core percentage limit. |
| `pdCellSpec.models[].prefill.portBase` | 아니오 | `8101`, `integer` | Prefill HTTP port는 `portBase + container index`. |
| `pdCellSpec.models[].decode.portBase` | 아니오 | `8201`, `integer` | Decode HTTP port는 `portBase + container index`. |
| `pdCellSpec.models[].{phase}.internalPortMode` | 아니오 | `vllm`, `vllm\|dp\|auto` | `vllm`은 VLLM_PORT, `dp`는 VLLM_DP_MASTER_PORT, `auto`는 둘 다 주입하지 않는다. |
| `pdCellSpec.models[].prefill.internalPortBase` | 아니오 | `20000`, `integer` | Prefill VLLM_PORT base. |
| `pdCellSpec.models[].decode.internalPortBase` | 아니오 | `30000`, `integer` | Decode VLLM_PORT base. |
| `pdCellSpec.models[].{phase}.internalPortStride` | 아니오 | `100`, `integer` | VLLM_PORT container 간 간격. |
| `pdCellSpec.models[].prefill.dpMasterPortBase` | 아니오 | `24000`, `integer` | Prefill DP master port base. |
| `pdCellSpec.models[].decode.dpMasterPortBase` | 아니오 | `34000`, `integer` | Decode DP master port base. |
| `pdCellSpec.models[].prefill.sideChannelPortBase` | 아니오 | `5600`, `integer` | Prefill NIXL side-channel port base. |
| `pdCellSpec.models[].decode.sideChannelPortBase` | 아니오 | `5700`, `integer` | Decode NIXL side-channel port base. |
| `pdCellSpec.models[].{phase}.command` | 아니오 | image별 `vllm serve`, `string[]` | P/D container command 전체 override. generated args는 유지한다. |
| `pdCellSpec.models[].{phase}.extraArgs` | 아니오 | `[]`, `string[]` | generated vLLM args 뒤에 순서대로 append. |
| `pdCellSpec.models[].{phase}.env` | 아니오 | `[]`, `EnvVar[]` | 해당 phase container에만 merge하는 env. |
| `pdCellSpec.models[].{phase}.envFrom` | 아니오 | `[]`, `EnvFromSource[]` | 해당 phase container에만 append하는 envFrom. |
| `pdCellSpec.models[].{phase}.extraVolumes` | 아니오 | `[]`, `Volume[]` | Cell Pod volume 집합에 추가. 이름은 다른 scope와 충돌하지 않게 쓴다. |
| `pdCellSpec.models[].{phase}.extraVolumeMounts` | 아니오 | `[]`, `VolumeMount[]` | 해당 phase container에만 적용하는 mount. |
| `pdCellSpec.models[].{phase}.containerSecurityContext` | 아니오 | 상속, `SecurityContext` | 해당 phase container security context 최종 override. |
| `pdCellSpec.models[].{phase}.kvTransferConfig` | 아니오 | `{}`, `KVTransferConfig map` | 해당 phase의 최종 raw KV config override. Helm이 마지막에 connector와 role을 강제한다. |

Helm이 최종 강제하는 값은 다음과 같다.

| 대상 | 강제 값 |
|---|---|
| Prefill KV role | `kv_producer` |
| Decode KV role | `kv_consumer` |
| `PROMETHEUS_MULTIPROC_DIR` | `/tmp` |
| NIXL side channel | connector와 port 설정에 따라 `VLLM_NIXL_SIDE_CHANNEL_PORT` |
| Mooncake Prefill | `VLLM_MOONCAKE_BOOTSTRAP_PORT` |
| Engine API port | phase `portBase + index` |
| Engine served names | `models[].servedModelNames`를 `--served-model-name` 인자 목록으로 주입 |

## `pdCellSpec.router` / `models[].router`

두 경로는 같은 field를 지원한다. model router가 공통 router를 deep-merge override한다.

| 속성 경로 | 필수 | 기본값, 타입 | 용도·설명 |
|---|---|---|---|
| `router.type` | 아니오 | `lmstack`, `lmstack\|vllm\|custom` | image별 generated CLI 계약을 선택한다. |
| `router.repository` | 조건부 | `routerSpec.repository`, `string` | Cell router image repository. |
| `router.tag` | 조건부 | `routerSpec.tag`, `string` | Cell router image tag. |
| `router.imagePullPolicy` | 아니오 | `routerSpec.imagePullPolicy`, `string` | Cell router image pull policy. |
| `router.port` | 아니오 | `8000`, `integer` | Router HTTP listen port와 Service target. |
| `router.healthPath` | 아니오 | `/health`, `string` | Router startup/liveness/readiness HTTP path. |
| `router.healthCheckInterval` | 아니오 | `30`, `integer` | LMStack static backend health check interval. |
| `router.healthCheckTimeout` | 아니오 | `5`, `integer` | LMStack static backend health check timeout. |
| `router.startupProbeInitialDelaySeconds` | 아니오 | `5`, `integer` | Router startup probe initial delay. |
| `router.startupProbePeriodSeconds` | 아니오 | `5`, `integer` | Router startup probe period. |
| `router.startupProbeFailureThreshold` | 아니오 | `60`, `integer` | Router startup probe failure threshold. |
| `router.livenessProbePeriodSeconds` | 아니오 | `10`, `integer` | Router liveness probe period. |
| `router.livenessProbeFailureThreshold` | 아니오 | `3`, `integer` | Router liveness probe failure threshold. |
| `router.readinessProbePeriodSeconds` | 아니오 | `5`, `integer` | Router readiness probe period. |
| `router.readinessProbeFailureThreshold` | 아니오 | `3`, `integer` | Router readiness probe failure threshold. |
| `router.resources` | 아니오 | `routerSpec.resources`, `ResourceRequirements` | Router CPU/memory request와 limit. |
| `router.containerSecurityContext` | 아니오 | `routerSpec.containerSecurityContext`, `SecurityContext` | Router container security context. |
| `router.env` | 아니오 | `[]`, `EnvVar[]` | Router에만 적용하는 최종 env layer. |
| `router.envFrom` | 아니오 | `[]`, `EnvFromSource[]` | Router에만 append하는 envFrom. |
| `router.extraVolumes` | 아니오 | `[]`, `Volume[]` | Cell Pod volume 집합에 추가. |
| `router.extraVolumeMounts` | 아니오 | `[]`, `VolumeMount[]` | Router에만 적용하는 mount. |
| `router.command` | 아니오 | image ENTRYPOINT, `string[]` | Router container command 전체 override. |
| `router.args` | 조건부 | type별 자동 생성, `string[]` | 지정하면 generated args를 완전히 대체한다. `type=custom`에서는 필수다. |
| `router.extraArgs` | 아니오 | `[]`, `string[]` | generated 또는 직접 지정한 args 뒤에 append. |
| `router.policy` | 아니오 | `consistent_hash`, `string` | `type=vllm`의 `--policy`. |
| `router.prometheusPort` | 아니오 | `29000`, `integer` | `type=vllm` metrics port. `0`이면 metrics args/port를 만들지 않는다. |
| `router.kvConnector` | 조건부 | Nixl→`nixl`, Mooncake→`mooncake`, `string` | `type=vllm`의 `--kv-connector`. 자동 매핑되지 않는 connector면 직접 지정한다. |

`type=lmstack`은 static backend/model/label/type와 `disaggregated_prefill_orchestrated` args를 생성한다. model alias가 있으면 `--static-aliases alias:primary`도 생성한다.

`type=vllm`은 `--vllm-pd-disaggregation`, 반복형 `--prefill`/`--decode`, connector와 policy args를 생성한다. 모델 이름은 router가 Prefill의 `/v1/models`를 proxy하고 요청의 `model`을 backend에 그대로 전달하므로 별도 alias CLI를 만들지 않는다.

## `pdCellSpec.kvTransfer` / `models[].kvTransfer`

두 경로는 같은 field를 지원한다. model 값이 공통값을 deep-merge override한다.

| 속성 경로 | 필수 | 기본값, 타입 | 용도·설명 |
|---|---|---|---|
| `kvTransfer.connector` | 예 | 없음, `string` | vLLM `kv_connector`. NixlConnector, MooncakeConnector, MultiConnector 또는 image에 등록된 connector. |
| `kvTransfer.config` | 아니오 | `{}`, `KVTransferConfig map` | Prefill/Decode 공통 raw vLLM KVTransferConfig. snake_case를 그대로 쓴다. |
| `kvTransfer.prefillConfig` | 아니오 | `{}`, `KVTransferConfig map` | Prefill에만 config 위로 deep-merge. |
| `kvTransfer.decodeConfig` | 아니오 | `{}`, `KVTransferConfig map` | Decode에만 config 위로 deep-merge. |
| `kvTransfer.bootstrapPortBase` | 아니오 | `9001`, `integer` | Mooncake Prefill bootstrap port base. vLLM Router Mooncake endpoint arg에도 사용한다. |
| `kvTransfer.abortRequestTimeout` | 아니오 | `600`, `integer` | Mooncake abort timeout env 값. |
| `kvTransfer.nixlSideChannelEnabled` | 아니오 | Nixl connector면 `true`, `boolean` | MultiConnector/custom connector에서 NIXL side-channel env를 강제로 켜거나 끈다. |

`config`, `prefillConfig`, `decodeConfig`, phase `kvTransferConfig`에는 vLLM 0.27.1 `KVTransferConfig`의 모든 field를 넣을 수 있다.

| raw config key | vLLM 기본값 | 타입·설명 |
|---|---|---|
| `engine_id` | 자동 UUID | `string`; 일반적으로 직접 고정하지 않는다. |
| `kv_buffer_device` | platform device | `string`; `cuda`, `cpu`, `xpu` 등 connector buffer device. |
| `kv_buffer_size` | `1e9` | `number`; connector buffer byte 크기. |
| `kv_rank` | `null` | `integer`; rank 기반 connector용. |
| `kv_parallel_size` | `1` | `integer`; rank 기반 KV transfer parallel size. |
| `kv_ip` | `127.0.0.1` | `string`; connector가 공통 endpoint field를 사용할 때만 지정. |
| `kv_port` | `14579` | `integer`; connector가 공통 port field를 사용할 때만 지정. |
| `kv_connector_extra_config` | `{}` | `map`; connector-specific option 전체. |
| `kv_connector_module_path` | `null` | `string`; 외부 V1 connector Python module path. |
| `enable_permute_local_kv` | `false` | `boolean`; NIXL HND/NHD layout permute. |
| `kv_load_failure_policy` | `fail` | `fail\|recompute`; KV load 실패 처리. |

`kv_connector`와 `kv_role`은 values에서 지정해도 Helm이 선택한 connector와 phase role로 마지막에 덮어쓴다.

## 생성되는 Service

각 model마다 `<release>-<name>-engine-service`를 만든다. selector는 Kubernetes identity인 `models[].name`을 사용하며 target은 `pd-router` named port다. 따라서 같은 `servedModelNames`와 profile을 사용하는 P1D1, P2D1, P2D2 topology를 동시에 정의해도 `name`만 고유하면 리소스가 충돌하지 않는다.
