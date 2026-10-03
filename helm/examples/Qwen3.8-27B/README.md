# Qwen3.8-27B: H200 P1D1 / TP2+TP2

사용자가 제공한 기존 Mooncake 테스트 프로파일과 두 KV backend 배포 예제를 함께 보관한다.
검토 기준은 **vLLM v0.30.0**, 소스 태그 커밋
`ced6857afa0ea7b2e3f0846a62e1394e90f15607`이다.
GPU에서의 NIXL 실행 성공이나 MTP 정확성을 인증한 예제가 아니다.

## 파일과 실행 경로

| 파일 | 역할 | 컨테이너 내 경로 |
| --- | --- | --- |
| `deploy-template-pdcell-mooncake.yaml` | Helm values: 기존 Mooncake P1D1 | `helm ... -f`로 전달 |
| `deploy-template-pdcell-nixl.yaml` | Helm values: NIXL P1D1 | `helm ... -f`로 전달 |
| `k8s-qwen38-27b-prefill-h200-tp2.yaml` | 공통 Prefill vLLM `--config` | `/profiles/k8s-qwen38-27b-prefill-h200-tp2.yaml` |
| `k8s-qwen38-27b-decode-h200-tp2.yaml` | 공통 Decode vLLM `--config` | `/profiles/k8s-qwen38-27b-decode-h200-tp2.yaml` |

두 배포 values는 루트의 같은 이름 파일을 복사해 모아둔 것이다. 루트 파일도 유지한다.
프로파일은 backend별로 복제하지 않는다. 두 values 모두 위의 동일한 P/D 프로파일을 참조한다.
이 디렉터리의 엔진 프로파일은 **Helm values가 아니므로 `helm -f`로 전달하지 않는다.**
Helm chart가 이 파일들을 자동으로 ConfigMap에 포함하거나 마운트하지는 않는다.
기존 운영 `global-values.yaml`의 `/profiles` 마운트 원본(NAS/PVC 등)에 두 파일을 배치하고,
`/models/Qwen3.8-27B`와 기존 `chat_template_PDtest.jinja`도 접근 가능하게 해야 한다.
실제 Jinja 파일 내용은 이번 요청에 제공되지 않았으므로 변경하거나 검증하지 않았다.

저장소 루트에서 렌더링 예시:

```bash
helm template llm ./helm -n inference \
  -f ./helm/values.yaml \
  -f /path/to/global-values.yaml \
  -f /path/to/existing/deploy-models.yaml \
  -f ./helm/examples/Qwen3.8-27B/deploy-template-pdcell-nixl.yaml
```

Mooncake 비교 시 마지막 `-f`만 Mooncake values로 교체한다.
두 values를 동시에 전달하면 `pdCellSpec.models` 목록이 병합되는 것이 아니라 뒤의 목록으로 대체된다.
이미 운영 중인 다른 PD Cell이 있으면 전체 `models` 목록을 포함하는 운영 values에서 변경해야 한다.
템플릿의 이미지 태그는 기존 커스텀 빌드 태그다. 내부 레지스트리 주소/실제 빌드 태그는 운영 값으로 맞춘다.

리소스 이름 `qwen38-27b-test`와 API의 `served-model-name`은 별개다.
요청에는 프로파일에 등록한 `Qwen3.8-27B-test`, `Qwen3.8-27B`, `standard` 중 하나를 사용한다.

## 입력 오타 정규화

| 제공한 표기 | 저장한 표기 | 설명 |
| --- | --- | --- |
| `truest-remote-code` | `trust-remote-code` | 실제 CLI 이름 |
| `gpu-memory-utilizaiton` | `gpu-memory-utilization` | 실제 CLI 이름 |
| `ture` | `true` | YAML boolean |
| `enable-chunked-prefil` | `enable-chunked-prefill` | 실제 CLI 이름 |
| `enable-log-request` | `enable-log-requests` | v0.30.0은 복수형 |
| `tensor-parallel-size:2` | `tensor-parallel-size: 2` | YAML key/value 구분 |
| 스마트 따옴표/닫히지 않은 JSON | YAML mapping | v0.30.0 config loader가 nested mapping을 JSON으로 변환 |
| `256K`, `32K`, `2K` | `262144`, `32768`, `2048` | 대문자 K는 1024 배수; 의미를 보존하고 정수로 명시 |

원본의 MTP3, APC, TP2, 파서, 메모리 비율, 배칭, CUDA graph 설정은 보존했다.
원본에 없는 `kv-cache-dtype`, `block-size`, attention backend를 임의로 추가하지 않았다.
따라서 FP8 KV를 명시한 프로파일이 아니다. 기존 실행 시 `extraArgs`나 이미지에서 추가했다면
그 설정도 함께 확인해야 한다.

| 항목 | Prefill | Decode |
| --- | --- | --- |
| TP | 2 | 2 |
| GPU memory utilization | 0.90 | 0.94 |
| Maximum model length | 262144 | 262144 |
| Chunked prefill | true | true |
| Batched tokens | 32768 | 2048 |
| Maximum sequences | 4 | 25 |
| CUDA graph | PIECEWISE | FULL_DECODE_ONLY |
| Speculative | MTP3 | MTP3 |
| Prefix caching | true | true |

## Q1: qwen3_coder와 qwen3_xml

**v0.30.0에서는 두 이름이 동일한 구현의 별칭이다.**
`vllm/tool_parsers/__init__.py`에서 둘 다
`qwen3_engine_tool_parser.Qwen3EngineToolParser`를 등록한다.
이 클래스는 `Qwen3ParserToolAdapter`를 상속하며 `structural_tag_model = "qwen_3_coder"`를 지정한다.
따라서 동일한 v0.30.0 이미지에서 이름만 바꾸면 기능, streaming 처리, 관련 버그가 달라지지 않는다.
기존 테스트를 보존하기 위해 프로파일에는 `qwen3_coder`를 유지했다.

처리하는 모델 출력 형식은 다음과 같은 Qwen XML 계열이다.

```text
<tool_call>
<function=get_weather>
<parameter=city>Seoul</parameter>
</function>
</tool_call>
```

파서는 이를 API의 `tool_calls[].function.name`과 JSON 문자열 `arguments`로 변환한다.
현재 `vllm/parser/qwen3.py`의 grammar/state engine이 reasoning 경계와 tool 경계를 처리하며,
parameter 변환 이후 엔진이 요청 tool schema에 따라 타입을 보정한다.
이 이름 선택은 모델이 tool 호출을 잘 생성하도록 학습시키거나 chat template 형식을 바꾸는 옵션이 아니다.
커스텀 chat template이 다른 tool 출력 문법을 요구하면 둘 중 이름을 바꾸는 것으로 해결되지 않는다.

과거에는 별도 구현이었다. v0.20.0 태그에서 확인하면:

| 이름 | 당시 클래스 | 구현 차이 |
| --- | --- | --- |
| `qwen3_coder` | `Qwen3CoderToolParser` | regex와 자체 streaming state로 XML 계열 출력 처리 |
| `qwen3_xml` | `Qwen3XMLToolParser` | `StreamingXMLToolCallParser`와 Expat XML callback을 이용한 처리 |

서로 다른 tool 호출 표준을 뜻한 것이 아니라 같은 계열 문법을 다른 코드로 처리하던 것이다.
현재도 이름이 두 개 남은 것은 기존 명령/설정 호환성을 유지하는 별칭으로 해석할 수 있다.
이 유지 목적은 등록 소스에서의 추론이며, v0.30.0에서 두 구현이 분리되어 있다는 뜻은 아니다.

### 보고된 이슈와 v0.30.0 판단

조회 기준일: 2026-10-03. 이슈가 열린 상태라는 사실만으로 v0.30.0 재현을 단정하지 않는다.

| 이슈 | 보고 증상 | v0.30.0 판단 |
| --- | --- | --- |
| [#54808](https://github.com/vllm-project/vllm/issues/54808) | v0.28.0에서 reasoning qwen3와 병용 시 `required`/named tool 선택 제약이 붙지 않음 | Closed. 댓글이 [#52830](https://github.com/vllm-project/vllm/pull/52830), v0.29.0+에서 수정됐다고 명시. v0.30.0 소스도 `DelegatingParser` 경로와 structural tag 적용을 확인. |
| [#58147](https://github.com/vllm-project/vllm/issues/58147) | v0.28.0에서 설명/예시로 인용한 XML을 실제 tool call로 처리; 미등록 이름/중복 호출 | Open. v0.30.0 `qwen3_config`도 `validate_tool_names`를 활성화하지 않고 XML 경계를 인식. 해당 코드 위험은 남아 있으나 같은 모델/프롬프트의 GPU 재현은 하지 않음. |
| [#55495](https://github.com/vllm-project/vllm/issues/55495) | v0.28.0에서 잘린 streaming `arguments`/XML 유출로 invalid JSON, Responses 후속 턴 400 | Open. malformed/잘린 tool 출력 관련 회귀 후보. v0.30.0 재현이나 완전 해결은 이번 정적 검토로 확인하지 못함. |

`required`는 요청의 tool 선택 정책이고 tool schema의 `required` property와 별개다.
비교할 때는 `auto`/`required`/named, streaming/non-streaming, 같은 도구의 반복 호출,
숫자/boolean/배열/중첩 object, 코드 문자열의 줄바꿈/들여쓰기,
XML 예시를 인용하는 답변, `max_tokens`로 끊긴 tool call을 실제 사용 API 경로에서 확인한다.
고객이 `qwen3_xml`로 바꿨다는 사실만으로 위 이슈가 해소되지는 않는다.

## Q2: Mooncake 프로파일을 NIXL에서도 재사용할 수 있는가

**이 두 P/D 프로파일 파일 자체는 그대로 재사용한다.**
Helm `deployment-pd-cell.yaml`이 각 engine의 `--config` 뒤에
`--kv-transfer-config`를 붙이고 connector/role을 지정한다.
따라서 엔진 프로파일에 Mooncake bootstrap, `num_workers`, UCX,
NIXL side-channel 등을 넣지 않는다. 환경변수 역시 Helm env로 주입한다.

다만 같은 프로파일을 재사용한다는 것은 runtime 조건까지 동일하다는 뜻은 아니다.

| 범위 | Mooncake 예제 | NIXL 예제 |
| --- | --- | --- |
| 엔진 profile | 위의 공통 P/D 파일 | 위의 공통 P/D 파일 |
| KV connector | MooncakeConnector | NixlConnector (pull/READ 별칭) |
| backend/transport | chart가 `nvlink_intra` 주입 | UCX, `cuda_ipc,cuda_copy,sm,self,tcp`, device `lo` |
| SSM conv state layout | 명시 없음; vLLM 기본 SD | DS 명시 필요 |
| Attention KV layout | 자동 | HND 명시; NIXL도 자동으로 LBHNC/HND 계열 선호 |
| Router | v0.1.15, chart 기본 정책 | v0.1.15-pr234, round_robin |
| hostPID | true | false |
| GPU 할당 | pod-local aggregate reservation/launcher | P/D container별 2GPU 요청 |

### DS 필수조건 정정

`VLLM_SSM_CONV_STATE_LAYOUT`은 connector 전용 변수가 아니라 모델 conv state 메모리 배치를 바꾼다.
그러나 **v0.30.0 NIXL은 MambaSpec state를 전송할 때 DS를 요구한다.**
`vllm/distributed/kv_transfer/kv_connector/v1/nixl/base_worker.py`의
`NixlBaseConnectorWorker.__init__`는 `_has_mamba`이면 `is_conv_state_dim_first()`를 assert한다.
SD이면 `Set VLLM_SSM_CONV_STATE_LAYOUT=DS`라는 메시지로 초기화를 중단한다.
이는 TP2/TP2 같은 homogeneous 구성에도 적용되는 조건이다.
따라서 이전 대화의 **“DS는 NIXL 필수조건이 아니므로 unset으로 비교하자”는 설명은 정정한다.**
Qwen3.8 hybrid/GDN의 NIXL 예제에는 DS를 유지한다.
`VLLM_KV_CACHE_LAYOUT=HND`는 이 예제에서 고객 설정을 명시적으로 고정하는 역할이고,
unset이어도 non-MLA NIXL connector가 LBHNC를 선호한다. DS 요구와는 별개다.

### 호환성과 MTP3 검증 범위

v0.30.0 공식 NIXL matrix에서 Hybrid SSM/Mamba의 Basic PD는 supported,
speculative decoding은 unknown/not yet validated이다.
APC, chunked prefill, CUDA graph는 공통 지원 목록에 있다.
MTP3가 Mooncake에서 동작했다고 NIXL의 hybrid state+MTP도 검증된 것은 아니다.
기존 실험을 보존하기 위해 두 프로파일의 MTP3를 그대로 저장했다.
초기 NIXL correctness 확인이 목적이면 두 프로파일을 별도 테스트 경로에 복사하고
**P/D 모두에서 `speculative-config`를 제거한 뒤** 그 복사본을 참조하여 먼저 확인한다.
그 다음 공통 원본 MTP3로 비교한다. 이를 단순 오타 수정과 섞어 기본값으로 변경하지 않는다.

P/D에서 vLLM/NIXL connector 버전, 모델/실제 dtype, attention backend, KV dtype,
EAGLE/MTP method 및 draft model 설정, push/pull 모드가 호환돼야 한다.
이 예제는 **TP2/TP2와 같은 block size**로 비교한다. 공식 matrix는 hybrid homogeneous TP를
요구한다고 기술하지만, v0.30.0 `base_worker.py`에는 Mamba 3-read heterogeneous-TP
전송 구현도 존재한다. 문서와 구현의 진척이 다르므로 이를 단순히 “heterogeneous TP 불가능”으로
단정하지 않는다. 이번 예제/검증 범위는 homogeneous TP2/TP2다.
현재 프로파일의 P/D GPU memory utilization, MBT, max-num-seqs, CUDA graph mode 차이는
동일할 필요가 없다. GPU memory 비율 차이는 cache block 수와 동시성에 영향을 준다.
default로 남긴 backend/dtype/block size도 실제 시작 로그와 NIXL handshake에서 확인한다.

성능 A/B 시 같은 프로파일만으로 완전한 단일 변수 실험이 되지는 않는다.
DS/HND, Router PR234/정책, hostPID/GPU 노출 방식도 두 템플릿 사이에 다르다.
순수 connector 비용을 비교하려면 지원되는 공통 레이아웃과 Router 설정을 검증하여 맞추고,
캐시 warm/cold 상태와 요청 trace를 고정한다. 특히 NIXL에서 필수 DS를 제거해서 맞추면 안 된다.
`UCX_NET_DEVICES=lo`는 같은 Pod/network namespace를 전제로 하는 이 node-local Cell 설정이다.
별도 Pod/노드로 확장할 때 그대로 가져가면 안 된다.

## 수행한 검증과 제한

- Helm v3.17.3으로 두 values의 lint와 실제 manifest 렌더링 통과.
- P1D1/총 4GPU, 양쪽 profile 경로, connector와 producer/consumer role 주입 확인.
- NIXL DS/HND/UCX/side-channel env 및 Mooncake nvlink_intra 주입 확인.
- 두 프로파일의 33개 옵션명이 v0.30.0 소스의 인자/설정 정의에 존재함을 확인.
- v0.30.0 `FlexibleArgumentParser.load_config_file`의 실제 구현으로 YAML-to-argv와
  nested config JSON 변환, 대문자 K 해석을 확인. 로거를 stub하고 regex import를 표준 re로 대체한
  독립 config-loader 검사이며, 전체 vLLM CLI/engine 실행 검증은 아니다.
- P/D 프로파일의 차이가 메모리 비율/배칭/동시성/CUDA graph 네 항목뿐임을 확인.
- 실제 이미지, 모델 가중치, GPU가 없어 NIXL handshake/MTP/추론/커스텀 Jinja 검증은 수행하지 않음.

## 근거 소스

- [v0.30.0 parser registry](https://github.com/vllm-project/vllm/blob/v0.30.0/vllm/tool_parsers/__init__.py)
- [Qwen3EngineToolParser](https://github.com/vllm-project/vllm/blob/v0.30.0/vllm/tool_parsers/qwen3_engine_tool_parser.py)
- [Qwen3 grammar](https://github.com/vllm-project/vllm/blob/v0.30.0/vllm/parser/qwen3.py)
- [Parser manager](https://github.com/vllm-project/vllm/blob/v0.30.0/vllm/parser/parser_manager.py)
- [Delegating parser](https://github.com/vllm-project/vllm/blob/v0.30.0/vllm/parser/abstract_parser.py)
- [Qwen parser regression tests](https://github.com/vllm-project/vllm/blob/v0.30.0/tests/tool_parsers/test_qwen3coder_tool_parser.py)
- [v0.20.0 registry](https://github.com/vllm-project/vllm/blob/v0.20.0/vllm/tool_parsers/__init__.py)
- [v0.20.0 coder implementation](https://github.com/vllm-project/vllm/blob/v0.20.0/vllm/tool_parsers/qwen3coder_tool_parser.py)
- [v0.20.0 XML implementation](https://github.com/vllm-project/vllm/blob/v0.20.0/vllm/tool_parsers/qwen3xml_tool_parser.py)
- [v0.30.0 NIXL compatibility matrix](https://docs.vllm.ai/en/v0.30.0/features/nixl_connector_compatibility/)
- [NIXL worker / DS assertion](https://github.com/vllm-project/vllm/blob/v0.30.0/vllm/distributed/kv_transfer/kv_connector/v1/nixl/base_worker.py)
- [SSM conv layout](https://github.com/vllm-project/vllm/blob/v0.30.0/vllm/model_executor/layers/mamba/mamba_utils.py)
- [Config YAML conversion / K interpretation](https://github.com/vllm-project/vllm/blob/v0.30.0/vllm/utils/argparse_utils.py)
