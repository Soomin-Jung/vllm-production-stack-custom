from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def one(text, old, new, label):
    count = text.count(old)
    if count != 1:
        raise RuntimeError(f"{label}: expected 1 match, found {count}")
    return text.replace(old, new, 1)


def exact_all(text, old, new, expected, label):
    count = text.count(old)
    if count != expected:
        raise RuntimeError(f"{label}: expected {expected} matches, found {count}")
    return text.replace(old, new)


# values.yaml
path = ROOT / "helm/values.yaml"
text = path.read_text()
serving, router = text.split("routerSpec:\n", 1)
for old, new, label in [
    ('    environment: "vllm-production-stack"\n', '    environment: "vllm-production-stack" # default: "test"\n', 'environment'),
    ('    release: "0.1.8"\n', '    release: "0.1.8" # default: "test"\n', 'release'),
    ('    failureThreshold: 360\n', '    failureThreshold: 360 # default: 60\n', 'startup failureThreshold'),
    ('  tolerations:\n    - key: nvidia.com/gpu\n', '  tolerations: # default: []\n    - key: nvidia.com/gpu\n', 'serving tolerations'),
    ('  schedulerName: "gpu-binpack-scheduler"\n', '  schedulerName: "gpu-binpack-scheduler" # default: ""\n', 'schedulerName'),
]:
    serving = one(serving, old, new, label)
for old, new, label in [
    ('  tag: "0.1.9.dev9-g37bafbcf5.d20260107"\n', '  tag: "0.1.9.dev9-g37bafbcf5.d20260107" # default: "latest"\n', 'router tag'),
    ('  imagePullPolicy: "IfNotPresent"\n', '  imagePullPolicy: "IfNotPresent" # default: "Always"\n', 'router imagePullPolicy'),
    ('  replicaCount: 2\n', '  replicaCount: 2 # default: 1\n', 'router replicaCount'),
    ('    enabled: true\n    minReplicas: 2\n    maxReplicas: 5\n', '    enabled: true # default: false\n    minReplicas: 2 # default: 1\n    maxReplicas: 5 # default: 3\n', 'router autoscaling'),
    ('  serviceType: LoadBalancer\n', '  serviceType: LoadBalancer # default: ClusterIP\n', 'router serviceType'),
    ('  servicePort: 9400\n', '  servicePort: 9400 # default: 80\n', 'router servicePort'),
    ('      cpu: 1000m\n      memory: 5Gi\n', '      cpu: 1000m # default: unset\n      memory: 5Gi # default: 500Mi\n', 'router limits'),
]:
    router = one(router, old, new, label)
path.write_text(serving + "routerSpec:\n" + router)

# deployment-router.yaml
path = ROOT / "helm/templates/deployment-router.yaml"
text = path.read_text()
text = one(
    text,
    '      serviceAccountName: {{ .Release.Name }}-router-service-account\n      tolerations:\n',
    '      serviceAccountName: {{ .Release.Name }}-router-service-account\n      # CPU 노드 리소스 부족으로 GPU 서버 활용\n      tolerations:\n',
    'router toleration comment',
)
path.write_text(text)


def format_merge_tokens(text, expected_env, expected_mount, expected_volume):
    rules = [
        ('{{- range $e := $modelSpec.env }}', '{{-   range $e := $modelSpec.env }}', expected_env),
        ('{{- $_ := set $modelEnvMap $e.name $e }}', '{{-     $_ := set $modelEnvMap $e.name $e }}', expected_env),
        ('{{- $mergedEnvList = append $mergedEnvList $v }}', '{{-   $mergedEnvList = append $mergedEnvList $v }}', expected_env),
        ('{{- range $e := $modelSpec.extraVolumeMounts }}', '{{-   range $e := $modelSpec.extraVolumeMounts }}', expected_mount),
        ('{{- $_ := set $modelMountMap $e.name $e }}', '{{-     $_ := set $modelMountMap $e.name $e }}', expected_mount),
        ('{{- $mergedMountList = append $mergedMountList $v }}', '{{-   $mergedMountList = append $mergedMountList $v }}', expected_mount),
        ('{{- range $e := $modelSpec.extraVolumes }}', '{{-   range $e := $modelSpec.extraVolumes }}', expected_volume),
        ('{{- $_ := set $modelVolumeMap $e.name $e }}', '{{-     $_ := set $modelVolumeMap $e.name $e }}', expected_volume),
        ('{{- $mergedVolumeList = append $mergedVolumeList $v }}', '{{-   $mergedVolumeList = append $mergedVolumeList $v }}', expected_volume),
    ]
    for old, new, expected in rules:
        text = exact_all(text, old, new, expected, old)
    return text

# deployment-vllm-multi.yaml
path = ROOT / "helm/templates/deployment-vllm-multi.yaml"
text = path.read_text()
for old, new, label in [
    ('{{- $_ := set $globalEnvMap $e.name $e }}', '{{-   $_ := set $globalEnvMap $e.name $e }}', 'global env map'),
    ('{{- $_ := set $globalMountMap $e.name $e }}', '{{-   $_ := set $globalMountMap $e.name $e }}', 'global mount map'),
    ('{{- $_ := set $globalVolumeMap $e.name $e }}', '{{-   $_ := set $globalVolumeMap $e.name $e }}', 'global volume map'),
]:
    text = one(text, old, new, label)
text = format_merge_tokens(text, 1, 1, 1)
text = exact_all(text, '{{- toYaml . | nindent 10 }}', '{{-   toYaml . | nindent 10 }}', 2, 'vllm merged outputs')
start_marker = '          {{/* Downstream: model path and engine options are externalized to profile YAML. */}}\n'
start = text.index(start_marker) + len(start_marker)
end = text.index('          imagePullPolicy:', start)
new_runtime = '''          {{- with $modelSpec.vllmConfig }}
          {{-   if .extraArgs }}
          {{-     range .extraArgs }}
          - {{ . | quote }}
          {{-     end }}
          {{-   end }}
          {{- end }}
          {{- if $modelSpec.lmcacheConfig }}
          {{-   if $modelSpec.lmcacheConfig.enabled }}
          - "--kv-transfer-config"
          {{/* kv_role 및 실패 정책은 Helm 렌더 시 동적으로 결정되므로 KV transfer config는 profile이 아닌 template에서 관리 */}}
          - |
            {"kv_connector": "LMCacheConnectorV1",
             "kv_role": "{{ $kv_role }}"{{ if $modelSpec.lmcacheConfig.enable_kv_load_failure_policy }},
             "kv_load_failure_policy": "recompute"{{ end }}
            }
          {{-   end }}
          {{- end }}
'''
text = text[:start] + new_runtime + text[end:]
path.write_text(text)

# ray-cluster.yaml
path = ROOT / "helm/templates/ray-cluster.yaml"
text = path.read_text()
for old, new, label in [
    ('{{- $_ := set $globalEnvMap $e.name $e }}', '{{-   $_ := set $globalEnvMap $e.name $e }}', 'ray global env map'),
    ('{{- $_ := set $globalMountMap $e.name $e }}', '{{-   $_ := set $globalMountMap $e.name $e }}', 'ray global mount map'),
    ('{{- $_ := set $globalVolumeMap $e.name $e }}', '{{-   $_ := set $globalVolumeMap $e.name $e }}', 'ray global volume map'),
]:
    text = one(text, old, new, label)
text = format_merge_tokens(text, 2, 2, 2)
text = exact_all(text, '{{- toYaml . | nindent 14 }}', '{{-   toYaml . | nindent 14 }}', 4, 'ray nindent14 outputs')
text = one(text, '{{- toYaml . | nindent 16 }}', '{{-   toYaml . | nindent 16 }}', 'ray worker env output')
text = one(text, '{{- toYaml . | nindent 10 }}', '{{-   toYaml . | nindent 10 }}', 'ray head volume output')
text = one(text, '{{- toYaml . | nindent 12 }}', '{{-   toYaml . | nindent 12 }}', 'ray worker volume output')
text = one(
    text,
    '      # Downstream: model URL is supplied by the model profile via extraArgs.\n      "--host" "0.0.0.0"\n',
    '      # 모델 경로는 profile yaml에서 관리하므로 positional modelURL 전달을 비활성화함.\n      # {{ $modelSpec.modelURL | quote }}\n      "--host" "0.0.0.0"\n',
    'ray commented modelURL',
)
path.write_text(text)

print('Reviewed baseline feedback applied successfully.')
