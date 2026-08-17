from pathlib import Path
import re

ROOT = Path(__file__).resolve().parents[1]


def replace_once(path: str, old: str, new: str) -> None:
    p = ROOT / path
    text = p.read_text()
    if new in text:
        return
    count = text.count(old)
    if count != 1:
        raise RuntimeError(f"{path}: expected exactly 1 old fragment, found {count}")
    p.write_text(text.replace(old, new, 1))


def replace_all(path: str, old: str, new: str, expected: int) -> None:
    p = ROOT / path
    text = p.read_text()
    if old not in text:
        # Idempotence: if replacement already happened expected times, accept it.
        if text.count(new) >= expected:
            return
        raise RuntimeError(f"{path}: old fragment not found")
    count = text.count(old)
    if count != expected:
        raise RuntimeError(f"{path}: expected {expected} old fragments, found {count}")
    p.write_text(text.replace(old, new))


def regex_once(path: str, pattern: str, replacement: str) -> None:
    p = ROOT / path
    text = p.read_text()
    if replacement in text:
        return
    updated, count = re.subn(pattern, replacement, text, count=1, flags=re.S)
    if count != 1:
        raise RuntimeError(f"{path}: regex expected 1 match, found {count}: {pattern}")
    p.write_text(updated)


# ---------------------------------------------------------------------------
# helm/values.yaml
# ---------------------------------------------------------------------------
replace_once(
    "helm/values.yaml",
    '  labels:\n    environment: "test"\n    release: "test"',
    '  labels:\n    environment: "vllm-production-stack"\n    release: "0.1.8"',
)

replace_once(
    "helm/values.yaml",
    '    failureThreshold:\n      60\n      # -- Configuration of the Kubelet http request on the server',
    '    failureThreshold: 360\n      # -- Configuration of the Kubelet http request on the server',
)

replace_once(
    "helm/values.yaml",
    '  tolerations: []\n\n  # -- RuntimeClassName configuration, set to "nvidia" if the model requires GPU',
    '  tolerations:\n    - key: nvidia.com/gpu\n      operator: Exists\n      effect: NoSchedule\n\n  # -- RuntimeClassName configuration, set to "nvidia" if the model requires GPU',
)

replace_once(
    "helm/values.yaml",
    '  # -- SchedulerName configuration\n  schedulerName: ""',
    '  # -- SchedulerName configuration\n  schedulerName: "gpu-binpack-scheduler"',
)

replace_once(
    "helm/values.yaml",
    '  repository: "lmcache/lmstack-router"\n  tag: "latest"\n  imagePullPolicy: "Always"',
    '  repository: "lmcache/lmstack-router"\n  tag: "0.1.9.dev9-g37bafbcf5.d20260107"\n  imagePullPolicy: "IfNotPresent"',
)

replace_once(
    "helm/values.yaml",
    '  # -- Number of replicas\n  replicaCount: 1\n\n  # -- autoscaling configuration\n  autoscaling:\n    enabled: false\n    minReplicas: 1\n    maxReplicas: 3',
    '  # -- Number of replicas\n  replicaCount: 2\n\n  # -- autoscaling configuration\n  autoscaling:\n    enabled: true\n    minReplicas: 2\n    maxReplicas: 5',
)

replace_once(
    "helm/values.yaml",
    '  # -- Service type\n  serviceType: ClusterIP',
    '  # -- Service type\n  serviceType: LoadBalancer',
)

replace_once(
    "helm/values.yaml",
    '  # -- Service port\n  servicePort: 80',
    '  # -- Service port\n  servicePort: 9400',
)

replace_once(
    "helm/values.yaml",
    '    limits:\n      memory: 500Mi',
    '    limits:\n      cpu: 1000m\n      memory: 5Gi',
)


# ---------------------------------------------------------------------------
# helm/templates/deployment-vllm-multi.yaml
# ---------------------------------------------------------------------------
deployment = "helm/templates/deployment-vllm-multi.yaml"

replace_once(
    deployment,
    '{{- if .Values.servingEngineSpec.enableEngine -}}\n{{- range $modelSpec := .Values.servingEngineSpec.modelSpec }}',
    '''{{- if .Values.servingEngineSpec.enableEngine -}}
{{/* Downstream: shared environment, volumes and mounts from global-values.yaml. */}}
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
{{- range $modelSpec := .Values.servingEngineSpec.modelSpec }}''',
)

replace_once(
    deployment,
    '  name: "{{ .Release.Name }}-{{$modelSpec.name}}-deployment-vllm"',
    '  name: "{{ .Release.Name }}-{{$modelSpec.name}}"',
)

replace_once(deployment, '          - {{ $modelSpec.modelURL | quote }}\n', '')

regex_once(
    deployment,
    r'''          \{\{- if \$modelSpec\.enableLoRA \}\}.*?          \{\{- end \}\}\n          \{\{- with \$modelSpec\.vllmConfig \}\}''',
    '          {{- with $modelSpec.vllmConfig }}',
)

regex_once(
    deployment,
    r'''          \{\{-   if hasKey \. "enableChunkedPrefill" \}\}.*?(?=          \{\{-   if \.extraArgs \}\})''',
    '',
)

regex_once(
    deployment,
    r'''          \{\{- if \$modelSpec\.chatTemplate \}\}\n          - "--chat-template"\n          - "/templates/\{\{ \$modelSpec\.chatTemplate \}\}"\n          \{\{- end \}\}\n''',
    '',
)

replace_once(
    deployment,
    '''          - name: PYTHONHASHSEED
            value: "123"
          - name: HF_HOME
            {{- if hasKey $modelSpec "pvcStorage" }}
            value: /data
            {{- else }}
            value: /tmp
            {{- end }}
''',
    '',
)

replace_once(
    deployment,
    '''          {{- with $modelSpec.env }}
          {{- toYaml . | nindent 10 }}
          {{- end }}''',
    '''          {{/* Downstream: global env is the default; per-model env wins by name. */}}
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
          {{- end }}''',
)

replace_once(
    deployment,
    '''          {{- if or (hasKey $modelSpec "pvcStorage") (and $modelSpec.vllmConfig (hasKey $modelSpec.vllmConfig "tensorParallelSize")) (hasKey $modelSpec "chatTemplate") (hasKey $modelSpec "extraVolumeMounts") }}
          volumeMounts:
          {{- end }}''',
    '''          volumeMounts:''',
)

replace_once(
    deployment,
    '''          {{- with $modelSpec.vllmConfig }}
          {{- if hasKey $modelSpec.vllmConfig "tensorParallelSize"}}
          - name: shm
            mountPath: /dev/shm
          {{- end}}
          {{- end}}''',
    '''          - name: shm
            mountPath: /dev/shm''',
)

replace_once(
    deployment,
    '''          {{- if hasKey $modelSpec "extraVolumeMounts" }}
          {{- toYaml $modelSpec.extraVolumeMounts | nindent 10 }}
          {{- end }}''',
    '''          {{- $modelMountMap := dict }}
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
          {{- end }}''',
)

replace_once(
    deployment,
    '''      {{- if or (hasKey $modelSpec "pvcStorage") (and $modelSpec.vllmConfig (hasKey $modelSpec.vllmConfig "tensorParallelSize")) (hasKey $modelSpec "chatTemplate") (hasKey $modelSpec "extraVolumes") (hasKey $.Values "sharedPvcStorage") }}
      volumes:
      {{- end}}''',
    '''      volumes:''',
)

replace_once(
    deployment,
    '''        {{- with $modelSpec.vllmConfig }}
        {{- if hasKey $modelSpec.vllmConfig "tensorParallelSize"}}
        - name: shm
          emptyDir:
            medium: Memory
            sizeLimit: {{ default "20Gi" $modelSpec.shmSize }}
        {{- end}}
        {{- end}}''',
    '''        - name: shm
          hostPath:
            path: /dev/shm
            type: Directory''',
)

replace_once(
    deployment,
    '''        {{- if hasKey $modelSpec "extraVolumes" }}
        {{- toYaml $modelSpec.extraVolumes | nindent 8 }}
        {{- end}}''',
    '''        {{- $modelVolumeMap := dict }}
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
        {{- end}}''',
)


# ---------------------------------------------------------------------------
# helm/templates/ray-cluster.yaml
# ---------------------------------------------------------------------------
ray = "helm/templates/ray-cluster.yaml"

replace_once(
    ray,
    '{{- if .Values.servingEngineSpec.enableEngine }}\n{{- range $modelSpec := .Values.servingEngineSpec.modelSpec }}',
    '''{{- if .Values.servingEngineSpec.enableEngine }}
{{/* Downstream: shared environment, volumes and mounts from global-values.yaml. */}}
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
{{- range $modelSpec := .Values.servingEngineSpec.modelSpec }}''',
)

replace_once(
    ray,
    '''              {{- with $modelSpec.env }}
              {{- toYaml . | nindent 14 }}
              {{- end }}''',
    '''              {{/* Downstream: global env is the default; per-model env wins by name. */}}
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
              {{- end }}''',
)

replace_once(
    ray,
    '''                {{- with $modelSpec.env }}
                {{- toYaml . | nindent 16 }}
                {{- end }}''',
    '''                {{/* Downstream: global env is the default; per-model env wins by name. */}}
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
                {{- toYaml . | nindent 16 }}
                {{- end }}''',
)

replace_all(
    ray,
    '(hasKey $modelSpec "extraVolumeMounts") }}',
    '(hasKey $modelSpec "extraVolumeMounts") (gt (len $globalMounts) 0) }}',
    2,
)

replace_all(
    ray,
    '(hasKey $modelSpec "extraVolumes") }}',
    '(hasKey $modelSpec "extraVolumes") (gt (len $globalVolumes) 0) }}',
    2,
)

replace_once(
    ray,
    '''              {{- if hasKey $modelSpec "extraVolumeMounts" }}
              {{- toYaml $modelSpec.extraVolumeMounts | nindent 14 }}
              {{- end }}''',
    '''              {{- $modelMountMap := dict }}
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
              {{- end }}''',
)

replace_once(
    ray,
    '''          {{- if hasKey $modelSpec "extraVolumes" }}
          {{- toYaml $modelSpec.extraVolumes | nindent 10 }}
          {{- end}}''',
    '''          {{- $modelVolumeMap := dict }}
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
          {{- end}}''',
)

replace_once(
    ray,
    '''            {{- if hasKey $modelSpec "extraVolumes" }}
            {{- toYaml $modelSpec.extraVolumes | nindent 12 }}
            {{- end}}''',
    '''            {{- $modelVolumeMap := dict }}
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
            {{- toYaml . | nindent 12 }}
            {{- end}}''',
)

replace_all(
    ray,
    'sizeLimit: {{ default "20Gi" $modelSpec.shmSize }}',
    'sizeLimit: {{ default "100Gi" $modelSpec.shmSize }}',
    2,
)

replace_once(
    ray,
    '      "{{ $modelSpec.modelURL | quote }}"\n',
    '      # Downstream: model URL is supplied by the model profile via extraArgs.\n',
)

print("Production 0.1.8 baseline rewrites validated and applied.")
