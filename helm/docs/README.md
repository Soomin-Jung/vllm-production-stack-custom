# 커스텀 배포 문서 안내

현재 설정 계약은 Values Reference와 차트 구현을 기준으로 확인한다. 실험 기록의
버전·backend·장비 조건을 다른 배포에 그대로 적용하지 않는다.

| 목적 | 문서 | 범위 |
| --- | --- | --- |
| P/D 설정 | [Values Reference](PD_CELL_VALUES_REFERENCE_KO.md) | Mooncake/NIXL 공통 GPU reservation, PID, topology, guardian |
| 운영 구조와 검증 이력 | [P/D Cell 운영](PD_CELL_0.1.8_KO.md) | Mooncake 현장 A/B 및 lifecycle |
| Qwen3.8 실행 예제 | [TP2 프로파일](../examples/Qwen3.8-27B/README.md) | v0.30.0 Mooncake/NIXL; GPU runtime 미인증 범위 명시 |
| 모델 실험 계획 | [Qwen3.6 벤치](QWEN3_6_27B_PD_BENCHMARK_MATRIX_KO.md), [Qwen3.8 튜닝](QWEN3_8_27B_PD_TUNING_KO.md) | 측정 계획; production 성능 보증 아님 |
| Router 이미지 빌드 | [실행 절차](../../docker/vllm-router/README.md), [한국어 설명](VLLM_ROUTER_IMAGE_BUILD_KO.md) | plain v0.1.15와 PR234 패치 소스 구분 |
| Mooncake 이미지 빌드 | [폐쇄망 빌드](../../docker/mooncake/README_KO.md) | 버전별 source/build/runtime |
| Agentic API | [빌드](../../docker/agentic-api/README_KO.md), [배포](../../deploy/agentic-api/README_KO.md), [라우팅](../../deploy/agentic-api/ROUTING_CONTRACT_KO.md) | 이 저장소의 v0.5.0 배포 기준 |
| 과거 디버깅 | [Issue #6 Driver IPC 후보](../../debug/issue-6/mooncake-v0.3.10/MINIMAL_DRIVER_IPC_FIX.md) | 보관 기록; 최종 해결은 hostPID=true |

루트의 `deploy-template-pdcell-*`와 `helm/examples/Qwen3.8-27B` values는 기존 사용
경로를 위해 유지한다. 공유 엔진 프로파일과 예제 설명은 Qwen3.8 디렉터리에서 관리한다.
