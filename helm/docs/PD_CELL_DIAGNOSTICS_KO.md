# P/D Cell 장애 진단 로그

## GPU reservation 실행 위치

Mooncake/NIXL `gpu-reservation`의 시작·reservation ready 로그에 다음을 기록한다.
`NODE_NAME`은 Pod Downward API의 `spec.nodeName`이며 컨테이너 hostname이 아니다.

```text
[pd-gpu] reservation starting: node=h200-node-3 pod=inference/cell-abc uid=...
[pd-gpu] reservation ready: node=h200-node-3 pod=inference/cell-abc uid=... gpus=GPU-...,GPU-...
```

시작 로그가 reservation UUID 조회 전에 출력되므로 GPU discovery 실패 로그도
동일한 실행 노드/Pod에 연결할 수 있다. 예약 성공 로그의 GPU 순서는 PCI-bus 정렬이다.

## Guardian 진단 수집

Guardian은 초기 startup과 ARMED 이후 모두 감시 대상의 종료/restart 상태를 기록한다.
초기 NotReady 상태에서는 기존대로 kubelet backoff를 유지한다. 모든 대상이 Ready로
회복되었을 때 dirty generation을 recycle하고, ARMED 이후 restartCount 증가 시
전체 Cell을 recycle한다. 진단을 위해 별도 재시작 옵션을 추가하지 않는다.

| JSONL event | 내용 |
| --- | --- |
| `container_failure_detected` | 대상 이름, restart count, 종료 reason/message, exit code, signal, 시작/종료 시각, container ID 및 state/lastState |
| `container_log_snapshot` | 해당 종료 인스턴스의 timestamp 포함 stdout/stderr 최근 200줄, 최대 64KiB |
| `container_log_unavailable` | 로그 API 실패/timeout과 HTTP status; 다른 실행 인스턴스의 로그로 대체하지 않음 |
| `pod_event_snapshot` | 같은 Pod UID의 최근 Kubernetes 이벤트 최대 20개; `Unhealthy`/`Killing`의 probe 실패 설명 등 |
| `pod_events_unavailable` | 이벤트 조회 실패/timeout |
| `whole_cell_recycle_requested` | Cell 재생성 판단과 trigger container, 전체 대상 status |
| `pod_delete_accepted` / `pod_delete_failed` | UID precondition을 사용한 DELETE 결과 |

현재 `state.terminated`이면 current 로그, 재시작 후 `lastState.terminated`이면
`previous=true` 로그를 조회한다. 여러 대상이 동시에 실패하면 각각 기록하고 동일한
관찰 상태는 반복 수집하지 않는다. 빠른 연속 restart에서 Kubernetes가 보존한 최신
이전 인스턴스만 조회할 수 있으며 모든 중간 인스턴스의 로그를 복구할 수는 없다.

수집은 DELETE 요청 전에 수행한다. 요청별 timeout은 최대 2초이며 한 실패 묶음의
진단 예산은 10초이다. 남은 예산이 없으면 조회 실패를 기록하고 lifecycle 처리를
계속한다. 여러 실패가 겹치면 뒤쪽 대상의 로그/이벤트가 예산 내 수집되지 않을 수 있다.
API 실패와 hostPath 기록 실패는 Cell 재생성을 막지 않는다.

## 로그 위치와 수집

모든 audit event를 stdout의 JSON 한 줄과 기존 node-local hostPath JSONL에 기록한다.
각 event는 UTC timestamp, 실행 node, namespace, Pod 이름 및 UID를 포함한다.
`pod_status`는 별도 객체이며 Pod 이름 필드를 덮어쓰지 않는다.

```text
host default: /var/log/vllm-pd-cell/guardian
container default: /var/log/pd-cell-guardian
file: <namespace>_<pod>_<pod-uid>.jsonl
```

```bash
# 해당 node에서 실행; Alloy 수집도 같은 hostPath를 사용한다.
jq 'select(.event == "container_failure_detected") |
    {node,pod,container,restart_count,reason,exit_code,signal,finished_at}' \
  /var/log/vllm-pd-cell/guardian/*.jsonl
jq -r 'select(.event == "container_log_snapshot") |
       "\(.node) \(.pod) \(.container) [\(.log_source)]\n\(.log_tail)"' \
  /var/log/vllm-pd-cell/guardian/*.jsonl
```

JSON 안의 `log_tail` 줄바꿈은 escape되므로 Alloy는 JSON decode 후 분석한다.
파일은 Pod UID별로 남으며 수명/보관 정책은 node 로그 수집·정리 정책에서 관리한다.
애플리케이션 stdout/stderr에 포함된 요청 내용도 복사될 수 있으므로 기존 엔진 로그와
동일한 접근권한/보관 정책을 적용한다.

## 종료 이유 해석과 RBAC

`OOMKilled`는 Kubernetes가 보고한 종료 원인이다. `Error`와 exit code 1만으로는
구체적인 원인을 확정할 수 없으며 stack trace를 함께 본다. CUDA OOM은 애플리케이션
예외로 기록될 수 있으므로 항상 Kubernetes `OOMKilled`로 나타나는 것은 아니다.
exit code 137만 보고 OOM으로 단정하지 않는다. probe에 의한 재시작 설명은
Kubernetes 이벤트에 남을 수 있다. 이벤트는 지연되거나 만료될 수 있다.

chart guardian Role은 기존 `pods/get,delete` 외에 `pods/log/get`, `events/list`를
namespace 범위로 허용한다. Events 조회는 `involvedObject.uid` selector를 사용한다.
커스텀 ServiceAccount에도 기존 RoleBinding을 통해 같은 권한을 부여한다.
`guardian.enabled=false`이면 Role/RoleBinding 및 credential mount를 생성하지 않는다.

노드 장애로 guardian 자체가 실행되지 못하거나 runtime이 이전 로그를 제거한 경우에는
이 진단만으로 원인을 복구할 수 없다. kubelet/node 이벤트와 Alloy/Loki를 함께 확인한다.

## 검증 범위

Helm 렌더링/RBAC와 모의 Kubernetes API 테스트로 종료 로그 수집, 초기 CrashLoop,
동시 실패, API 오류, 진단 예산 소진, Pod UID DELETE 보호를 검증한다. 실제 K3s의
OOM/probe failure 및 로그 API 권한 검증은 클러스터 배포 후 확인해야 한다.

참고: [Kubernetes 종료 원인](https://kubernetes.io/docs/tasks/debug/debug-application/determine-reason-pod-failure/),
[이전 컨테이너 로그](https://kubernetes.io/docs/concepts/cluster-administration/logging/).
