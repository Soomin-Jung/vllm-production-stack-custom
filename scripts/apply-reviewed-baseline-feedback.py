from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def replace_once(text: str, old: str, new: str, label: str) -> str:
    count = text.count(old)
    if count != 1:
        raise RuntimeError(f"{label}: expected 1 match, found {count}")
    return text.replace(old, new, 1)


def replace_all(text: str, old: str, new: str, expected: int, label: str) -> str:
    count = text.count(old)
    if count != expected:
        raise RuntimeError(f"{label}: expected {expected} matches, found {count}")
    return text.replace(old, new)


# ---------------------------------------------------------------------------
# values.yaml: annotate every downstream value with its upstream 0.1.8 default.
# ---------------------------------------------------------------------------
path = ROOT / "helm/values.yaml"
text = path.read_text()
replacements = [
    ('    environment: "vllm-production-stack"\n', '    environment: "vllm-production-stack" # default: "test"\n', 'labels.environment'),
    ('    release: "0.1.8"\n', '    release: "0.1.8" # default: "test"\n', 'labels.release'),
    ('    failureThreshold: 360\n', '    failureThreshold: 360 # default: 60\n', 'startupProbe.failureThreshold'),
    ('  tolerations:\n    - key: nvidia.com/gpu\n', '  tolerations: # default: []\n    - key: nvidia.com/gpu\n', 'servingEngineSpec.tolerations'),
    ('  schedulerName: "gpu-binpack-scheduler"\n', '  schedulerName: "gpu-binpack-scheduler" # default: ""\n', 'schedulerName'),
    ('  tag: "0.1.9.dev9-g37bafbcf5.d20260107"\n', '  tag: "0.1.9.dev9-g37bafbcf5.d20260107" # default: "latest"\n', 'routerSpec.tag'),
    ('  imagePullPolicy: "IfNotPresent"\n', '  imagePullPolicy: "IfNotPresent" # default: "Always"\n', 'routerSpec.imagePullPolicy'),
    ('  replicaCount: 2\n', '  replicaCount: 2 # default: 1\n', 'routerSpec.replicaCount'),
    ('    enabled: true\n    minReplicas: 2\n    maxReplicas: 5\n', '    enabled: true # default: false\n    minReplicas: 2 # default: 1\n    maxReplicas: 5 # default: 3\n', 'routerSpec.autoscaling'),
    ('  serviceType: LoadBalancer\n', '  serviceType: LoadBalancer # default: ClusterIP\n', 'routerSpec.serviceType'),
    ('  servicePort: 9400\n', '  servicePort: 9400 # default: 80\n', 'routerSpec.servicePort'),
    ('      cpu: 1000m\n      memory: 5Gi\n', '      cpu: 1000m # default: unset\n      memory: 5Gi # default: 500Mi\n', 'routerSpec.resources.limits'),
]
for old, new, label in replacements:
    text = replace_once(text, old, new, label)
path.write_text(text)


# ---------------------------------------------------------------------------
# deployment-router.yaml: document why the router tolerates GPU nodes.
# ---------------------------------------------------------------------------
path = ROOT / "helm/templates/deployment-router.yaml"
text = path.read_text()
old = '''      serviceAccountName: {{ .Release.Name }}-router-service-account
      tolerations:
        - key: nvidia.com/gpu
'''
new = '''      serviceAccountName: {{ .Release.Name }}-router-service-account
      # CPU 노드 리소스 부족으로 GPU 서버 활용
      tolerations:
        - key: nvidia.com/gpu
'''
text = replace_once(text, old, new, 'router GPU toleration comment')
path.write_text(text)


# ---------------------------------------------------------------------------
# deployment-vllm-multi.yaml: improve readability of downstream Go-template
# blocks and restore the production LMCache KV transfer contract.
# ---------------------------------------------------------------------------
path = ROOT / "helm/templates/deployment-vllm-multi.yaml"
text = path.read_text()

old = '''{{/* Downstream: shared environment, volumes and mounts from global-values.yaml. */}}
{{- $global := .Values.global | default dict }}
{{- $globalEnv := $global.env | default (list) }}
{{- $globalEnvMap := dict }}
{{- range $e := $globalEnv }}
{{- $_ := set $globalEnvMap $e.name $e }}
{{- end }}
{{- $globalMounts := $global.extraVolumeMounts | default (list) }}
{{- $globalMountMap := dict }}
{{- range $e := $globalMounts }}
{{- $_ := set $globalMountMap $e.name $e }}
{{- end }}
{{- $globalVolumes := $global.extraVolumes | default (list) }}
{{- $globalVolumeMap := dict }}
{{- range $e := $globalVolumes }}
{{- $_ := set $globalVolumeMap $e.name $e }}
{{- end }}
'''
new = '''{{/* Downstream: shared environment, volumes and mounts from global-values.yaml. */}}
{{- $global := .Values.global | default dict }}
{{- $globalEnv := $global.env | default (list) }}
{{- $globalEnvMap := dict }}
{{- range $e := $globalEnv }}
{{-   $_ := set $globalEnvMap $e.name $e }}
{{- end }}
{{- $globalMounts := $global.extraVolumeMounts | default (list) }}
{{- $globalMountMap := dict }}
{{- range $e := $globalMounts }}
{{-   $_ := set $globalMountMap $e.name $e }}
{{- end }}
{{- $globalVolumes := $global.extraVolumes | default (list) }}
{{- $globalVolumeMap := dict }}
{{- range $e := $globalVolumes }}
{{-   $_ := set $globalVolumeMap $e.name $e }}
{{- end }}
'''
text = replace_once(text, old, new, 'vllm global maps formatting')

old = '''          {{- with $modelSpec.vllmConfig }}
          {{- if .extraArgs }}
          {{- range .extraArgs }}
          - {{ . | quote }}
          {{- end }}
          {{- end }}
          {{- end }}
          {{- if $modelSpec.lmcacheConfig }}
          {{-   if $modelSpec.lmcacheConfig.enabled }}
          {{-     if hasKey $modelSpec.lmcacheConfig "enablePD" }}
          - "--kv-transfer-config"
          - '{"kv_connector":"LMCacheConnectorV1","kv_role":"{{ $kv_role }}","kv_connector_extra_config":{"discard_partial_chunks": false, "lmcache_rpc_port": {{ $modelSpec.lmcacheConfig.nixlRole | quote }}}}'
          {{-     else if and (hasKey $modelSpec.vllmConfig "v0") (eq (toString $modelSpec.vllmConfig.v0) "1") }}
          - "--kv-transfer-config"
          - '{"kv_connector":"LMCacheConnector","kv_role":"{{ $kv_role }}"}'
          {{-     else }}
          - "--kv-transfer-config"
          - '{"kv_connector":"LMCacheConnectorV1","kv_role":"{{ $kv_role }}"}'
          {{-     end }}
          {{-   end }}
          {{- end }}
'''
new = '''          {{- with $modelSpec.vllmConfig }}
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
text = replace_once(text, old, new, 'vllm extraArgs and KV transfer block')

old = '''          {{/* Downstream: global env is the default; per-model env wins by name. */}}
          {{- $modelEnvMap := dict }}
          {{- if hasKey $modelSpec "env" }}
          {{- range $e := $modelSpec.env }}
          {{- $_ := set $modelEnvMap $e.name $e }}
          {{- end }}
          {{- end }}
          {{- $mergedEnvMap := mergeOverwrite (deepCopy $globalEnvMap) $modelEnvMap }}
          {{- $mergedEnvList := list }}
          {{- range $k, $v := $mergedEnvMap }}
          {{- $mergedEnvList = append $mergedEnvList $v }}
          {{- end }}
          {{- with $mergedEnvList }}
          {{- toYaml . | nindent 10 }}
          {{- end }}
'''
new = '''          {{/* Downstream: global env is the default; per-model env wins by name. */}}
          {{- $modelEnvMap := dict }}
          {{- if hasKey $modelSpec "env" }}
          {{-   range $e := $modelSpec.env }}
          {{-     $_ := set $modelEnvMap $e.name $e }}
          {{-   end }}
          {{- end }}
          {{- $mergedEnvMap := mergeOverwrite (deepCopy $globalEnvMap) $modelEnvMap }}
          {{- $mergedEnvList := list }}
          {{- range $k, $v := $mergedEnvMap }}
          {{-   $mergedEnvList = append $mergedEnvList $v }}
          {{- end }}
          {{- with $mergedEnvList }}
          {{-   toYaml . | nindent 10 }}
          {{- end }}
'''
text = replace_once(text, old, new, 'vllm env formatting')

old = '''          {{- $modelMountMap := dict }}
          {{- if hasKey $modelSpec "extraVolumeMounts" }}
          {{- range $e := $modelSpec.extraVolumeMounts }}
          {{- $_ := set $modelMountMap $e.name $e }}
          {{- end }}
          {{- end }}
          {{- $mergedMountMap := mergeOverwrite (deepCopy $globalMountMap) $modelMountMap }}
          {{- $mergedMountList := list }}
          {{- range $k, $v := $mergedMountMap }}
          {{- $mergedMountList = append $mergedMountList $v }}
          {{- end }}
          {{- with $mergedMountList }}
          {{- toYaml . | nindent 10 }}
          {{- end }}
'''
new = '''          {{- $modelMountMap := dict }}
          {{- if hasKey $modelSpec "extraVolumeMounts" }}
          {{-   range $e := $modelSpec.extraVolumeMounts }}
          {{-     $_ := set $modelMountMap $e.name $e }}
          {{-   end }}
          {{- end }}
          {{- $mergedMountMap := mergeOverwrite (deepCopy $globalMountMap) $modelMountMap }}
          {{- $mergedMountList := list }}
          {{- range $k, $v := $mergedMountMap }}
          {{-   $mergedMountList = append $mergedMountList $v }}
          {{- end }}
          {{- with $mergedMountList }}
          {{-   toYaml . | nindent 10 }}
          {{- end }}
'''
text = replace_once(text, old, new, 'vllm mount formatting')

old = '''        {{- $modelVolumeMap := dict }}
        {{- if hasKey $modelSpec "extraVolumes" }}
        {{- range $e := $modelSpec.extraVolumes }}
        {{- $_ := set $modelVolumeMap $e.name $e }}
        {{- end }}
        {{- end }}
        {{- $mergedVolumeMap := mergeOverwrite (deepCopy $globalVolumeMap) $modelVolumeMap }}
        {{- $mergedVolumeList := list }}
        {{- range $k, $v := $mergedVolumeMap }}
        {{- $mergedVolumeList = append $mergedVolumeList $v }}
        {{- end }}
        {{- with $mergedVolumeList }}
        {{- toYaml . | nindent 8 }}
        {{- end}}
'''
new = '''        {{- $modelVolumeMap := dict }}
        {{- if hasKey $modelSpec "extraVolumes" }}
        {{-   range $e := $modelSpec.extraVolumes }}
        {{-     $_ := set $modelVolumeMap $e.name $e }}
        {{-   end }}
        {{- end }}
        {{- $mergedVolumeMap := mergeOverwrite (deepCopy $globalVolumeMap) $modelVolumeMap }}
        {{- $mergedVolumeList := list }}
        {{- range $k, $v := $mergedVolumeMap }}
        {{-   $mergedVolumeList = append $mergedVolumeList $v }}
        {{- end }}
        {{- with $mergedVolumeList }}
        {{-   toYaml . | nindent 8 }}
        {{- end }}
'''
text = replace_once(text, old, new, 'vllm volume formatting')
path.write_text(text)


# ---------------------------------------------------------------------------
# ray-cluster.yaml: readability for downstream merge blocks and preserve the
# disabled modelURL line in the generated shell script as a comment.
# ---------------------------------------------------------------------------
path = ROOT / "helm/templates/ray-cluster.yaml"
text = path.read_text()

old = '''{{/* Downstream: shared environment, volumes and mounts from global-values.yaml. */}}
{{- $global := .Values.global | default dict }}
{{- $globalEnv := $global.env | default (list) }}
{{- $globalEnvMap := dict }}
{{- range $e := $globalEnv }}
{{- $_ := set $globalEnvMap $e.name $e }}
{{- end }}
{{- $globalMounts := $global.extraVolumeMounts | default (list) }}
{{- $globalMountMap := dict }}
{{- range $e := $globalMounts }}
{{- $_ := set $globalMountMap $e.name $e }}
{{- end }}
{{- $globalVolumes := $global.extraVolumes | default (list) }}
{{- $globalVolumeMap := dict }}
{{- range $e := $globalVolumes }}
{{- $_ := set $globalVolumeMap $e.name $e }}
{{- end }}
'''
new = '''{{/* Downstream: shared environment, volumes and mounts from global-values.yaml. */}}
{{- $global := .Values.global | default dict }}
{{- $globalEnv := $global.env | default (list) }}
{{- $globalEnvMap := dict }}
{{- range $e := $globalEnv }}
{{-   $_ := set $globalEnvMap $e.name $e }}
{{- end }}
{{- $globalMounts := $global.extraVolumeMounts | default (list) }}
{{- $globalMountMap := dict }}
{{- range $e := $globalMounts }}
{{-   $_ := set $globalMountMap $e.name $e }}
{{- end }}
{{- $globalVolumes := $global.extraVolumes | default (list) }}
{{- $globalVolumeMap := dict }}
{{- range $e := $globalVolumes }}
{{-   $_ := set $globalVolumeMap $e.name $e }}
{{- end }}
'''
text = replace_once(text, old, new, 'ray global maps formatting')

old = '''{{/* Downstream: global env is the default; per-model env wins by name. */}}
              {{- $modelEnvMap := dict }}
              {{- if hasKey $modelSpec "env" }}
              {{- range $e := $modelSpec.env }}
              {{- $_ := set $modelEnvMap $e.name $e }}
              {{- end }}
              {{- end }}
              {{- $mergedEnvMap := mergeOverwrite (deepCopy $globalEnvMap) $modelEnvMap }}
              {{- $mergedEnvList := list }}
              {{- range $k, $v := $mergedEnvMap }}
              {{- $mergedEnvList = append $mergedEnvList $v }}
              {{- end }}
              {{- with $mergedEnvList }}
              {{- toYaml . | nindent 14 }}
              {{- end }}
'''
new = '''{{/* Downstream: global env is the default; per-model env wins by name. */}}
              {{- $modelEnvMap := dict }}
              {{- if hasKey $modelSpec "env" }}
              {{-   range $e := $modelSpec.env }}
              {{-     $_ := set $modelEnvMap $e.name $e }}
              {{-   end }}
              {{- end }}
              {{- $mergedEnvMap := mergeOverwrite (deepCopy $globalEnvMap) $modelEnvMap }}
              {{- $mergedEnvList := list }}
              {{- range $k, $v := $mergedEnvMap }}
              {{-   $mergedEnvList = append $mergedEnvList $v }}
              {{- end }}
              {{- with $mergedEnvList }}
              {{-   toYaml . | nindent 14 }}
              {{- end }}
'''
text = replace_once(text, old, new, 'ray head env formatting')

old_worker = old.replace('              ', '                ').replace('nindent 14', 'nindent 16')
new_worker = new.replace('              ', '                ').replace('nindent 14', 'nindent 16')
text = replace_once(text, old_worker, new_worker, 'ray worker env formatting')

mount_blocks = [
    (14, 14, 'ray head mount formatting'),
    (14, 14, 'ray worker mount formatting'),
]
old_mount = '''{{- $modelMountMap := dict }}
              {{- if hasKey $modelSpec "extraVolumeMounts" }}
              {{- range $e := $modelSpec.extraVolumeMounts }}
              {{- $_ := set $modelMountMap $e.name $e }}
              {{- end }}
              {{- end }}
              {{- $mergedMountMap := mergeOverwrite (deepCopy $globalMountMap) $modelMountMap }}
              {{- $mergedMountList := list }}
              {{- range $k, $v := $mergedMountMap }}
              {{- $mergedMountList = append $mergedMountList $v }}
              {{- end }}
              {{- with $mergedMountList }}
              {{- toYaml . | nindent 14 }}
              {{- end }}'''
new_mount = '''{{- $modelMountMap := dict }}
              {{- if hasKey $modelSpec "extraVolumeMounts" }}
              {{-   range $e := $modelSpec.extraVolumeMounts }}
              {{-     $_ := set $modelMountMap $e.name $e }}
              {{-   end }}
              {{- end }}
              {{- $mergedMountMap := mergeOverwrite (deepCopy $globalMountMap) $modelMountMap }}
              {{- $mergedMountList := list }}
              {{- range $k, $v := $mergedMountMap }}
              {{-   $mergedMountList = append $mergedMountList $v }}
              {{- end }}
              {{- with $mergedMountList }}
              {{-   toYaml . | nindent 14 }}
              {{- end }}'''
text = replace_once(text, old_mount, new_mount, 'ray head mount formatting')
old_mount_worker = old_mount.replace('              ', '              ', 1)
# Worker mount block uses the same template indentation/content as head in this chart.
text = replace_once(text, old_mount, new_mount, 'ray worker mount formatting')

old_volume_head = '''{{- $modelVolumeMap := dict }}
          {{- if hasKey $modelSpec "extraVolumes" }}
          {{- range $e := $modelSpec.extraVolumes }}
          {{- $_ := set $modelVolumeMap $e.name $e }}
          {{- end }}
          {{- end }}
          {{- $mergedVolumeMap := mergeOverwrite (deepCopy $globalVolumeMap) $modelVolumeMap }}
          {{- $mergedVolumeList := list }}
          {{- range $k, $v := $mergedVolumeMap }}
          {{- $mergedVolumeList = append $mergedVolumeList $v }}
          {{- end }}
          {{- with $mergedVolumeList }}
          {{- toYaml . | nindent 10 }}
          {{- end}}'''
new_volume_head = '''{{- $modelVolumeMap := dict }}
          {{- if hasKey $modelSpec "extraVolumes" }}
          {{-   range $e := $modelSpec.extraVolumes }}
          {{-     $_ := set $modelVolumeMap $e.name $e }}
          {{-   end }}
          {{- end }}
          {{- $mergedVolumeMap := mergeOverwrite (deepCopy $globalVolumeMap) $modelVolumeMap }}
          {{- $mergedVolumeList := list }}
          {{- range $k, $v := $mergedVolumeMap }}
          {{-   $mergedVolumeList = append $mergedVolumeList $v }}
          {{- end }}
          {{- with $mergedVolumeList }}
          {{-   toYaml . | nindent 10 }}
          {{- end }}'''
text = replace_once(text, old_volume_head, new_volume_head, 'ray head volume formatting')

old_volume_worker = old_volume_head.replace('          ', '            ').replace('nindent 10', 'nindent 12')
new_volume_worker = new_volume_head.replace('          ', '            ').replace('nindent 10', 'nindent 12')
text = replace_once(text, old_volume_worker, new_volume_worker, 'ray worker volume formatting')

old = '''      # Downstream: model URL is supplied by the model profile via extraArgs.
      "--host" "0.0.0.0"
'''
new = '''      # 모델 경로는 profile yaml에서 관리하므로 positional modelURL 전달을 비활성화함.
      # {{ $modelSpec.modelURL | quote }}
      "--host" "0.0.0.0"
'''
text = replace_once(text, old, new, 'ray commented modelURL')
path.write_text(text)

print('Reviewed baseline feedback applied successfully.')
