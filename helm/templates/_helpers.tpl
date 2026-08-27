{{/*
Define ports for the pods
*/}}
{{- define "chart.container-port" -}}
{{-  default "8000" .Values.servingEngineSpec.containerPort }}
{{- end }}

{{/*
Define service port
*/}}
{{- define "chart.service-port" -}}
{{-  if .Values.servingEngineSpec.servicePort }}
{{-    .Values.servingEngineSpec.servicePort }}
{{-  else }}
{{-    include "chart.container-port" . }}
{{-  end }}
{{- end }}

{{/*
Define service port name
*/}}
{{- define "chart.service-port-name" -}}
"service-port"
{{- end }}

{{/*
Define container port name
*/}}
{{- define "chart.container-port-name" -}}
"container-port"
{{- end }}

{{/*
Define engine deployment strategy.
If .Values.engineStrategy is defined, use it.
Otherwise, fall back to the default rolling update strategy.
*/}}
{{- define "chart.engineStrategy" -}}
strategy:
{{- if .Values.servingEngineSpec.strategy }}
{{- toYaml .Values.servingEngineSpec.strategy | nindent 2 }}
{{- else }}
  rollingUpdate:
    maxSurge: 100%
    maxUnavailable: 0
{{- end }}
{{- end }}

{{/*
Define router deployment strategy.
If .Values.routerStrategy is defined, use it.
Otherwise, fall back to the default rolling update strategy.
*/}}
{{- define "chart.routerStrategy" -}}
strategy:
{{- if .Values.routerSpec.strategy }}
{{- toYaml .Values.routerSpec.strategy | nindent 2 }}
{{- else }}
  rollingUpdate:
    maxSurge: 100%
    maxUnavailable: 0
{{- end }}
{{- end }}

{{/*
Define additional ports
*/}}
{{- define "chart.extraPorts" }}
{{-   with .Values.servingEngineSpec.extraPorts }}
{{-     toYaml . }}
{{-   end }}
{{- end }}

{{/*
Define additional router ports
*/}}
{{- define "chart.routerExtraPorts" }}
{{-   with .Values.routerSpec.extraPorts }}
{{-     toYaml . }}
{{-   end }}
{{- end }}

{{/*
Define startup, liveness and readiness probes
*/}}
{{- define "chart.probes" -}}
{{-   if .Values.servingEngineSpec.startupProbe  }}
startupProbe:
{{-     with .Values.servingEngineSpec.startupProbe }}
{{-       toYaml . | nindent 2 }}
{{-     end }}
{{-   end }}
{{-   if .Values.servingEngineSpec.livenessProbe  }}
livenessProbe:
{{-     with .Values.servingEngineSpec.livenessProbe }}
{{-       toYaml . | nindent 2 }}
{{-     end }}
{{-   end }}
{{-   if .Values.servingEngineSpec.readinessProbe  }}
readinessProbe:
{{-     with .Values.servingEngineSpec.readinessProbe }}
{{-       toYaml . | nindent 2 }}
{{-     end }}
{{-   end }}
{{- end }}

{{- define "chart.hasLimits" -}}
{{- $modelSpec := . -}}
{{- or
    (hasKey $modelSpec "limitMemory")
    (hasKey $modelSpec "limitCPU")
    (gt (int $modelSpec.requestGPU) 0)
    (hasKey $modelSpec "limitGPUMem")
    (hasKey $modelSpec "limitGPUMemPercentage")
    (hasKey $modelSpec "limitGPUCores")
-}}
{{- end -}}

{{/*
Define resources with a variable model spec
*/}}
{{- define "chart.resources" -}}
{{- $modelSpec := . -}}
{{/*
25.12.01 Require GPU declaration and validate consistency.
requestGPU=0 is allowed for special shared-GPU workloads such as embedding/reranker.
*/}}
{{- if not (hasKey $modelSpec "requestGPU") -}}
{{- fail "Value 'modelSpec.requestGPU' must be defined!" -}}
{{- end -}}
{{- $gpuRaw := $modelSpec.requestGPU -}}
{{- $gpuCount := int $gpuRaw -}}
{{- if lt $gpuCount 0 -}}
{{- fail (printf "modelSpec.requestGPU must be a non-negative integer, got %d" $gpuCount) -}}
{{- end -}}
{{/* Default requests: 4 CPU cores and 10Gi memory per requested GPU. */}}
{{- $defaultCPU := printf "%dm" (mul $gpuCount 4000) -}}
{{- $defaultMemory := printf "%dGi" (mul $gpuCount 10) -}}
requests:
  {{- if $modelSpec.requestMemory }}
  memory: {{ $modelSpec.requestMemory | quote }}
  {{- else }}
  memory: {{ $defaultMemory | quote }}
  {{- end }}
  {{- if $modelSpec.requestCPU }}
  cpu: {{ $modelSpec.requestCPU | quote }}
  {{- else }}
  cpu: {{ $defaultCPU | quote }}
  {{- end }}
  {{- if (gt $gpuCount 0) }}
  {{- $gpuType := default "nvidia.com/gpu" $modelSpec.requestGPUType }}
  {{ $gpuType }}: {{ $gpuRaw | quote }}
  {{- end }}
  {{- if (hasKey $modelSpec "requestGPUMem") }}
  nvidia.com/gpumem: {{ $modelSpec.requestGPUMem | quote }}
  {{- end }}
  {{- if (hasKey $modelSpec "requestGPUMemPercentage") }}
  nvidia.com/gpumem-percentage: {{ $modelSpec.requestGPUMemPercentage | quote }}
  {{- end }}
  {{- if (hasKey $modelSpec "requestGPUCores") }}
  nvidia.com/gpucores: {{ $modelSpec.requestGPUCores | quote }}
  {{- end }}
{{- if (include "chart.hasLimits" $modelSpec | fromYaml) }}
limits:
  {{- if (hasKey $modelSpec "limitMemory") }}
  memory: {{ $modelSpec.limitMemory | quote }}
  {{- end }}
  {{- if (hasKey $modelSpec "limitCPU") }}
  cpu: {{ $modelSpec.limitCPU | quote }}
  {{- end }}
  {{- if (gt $gpuCount 0) }}
  {{- $gpuType := default "nvidia.com/gpu" $modelSpec.requestGPUType }}
  {{ $gpuType }}: {{ $gpuRaw | quote }}
  {{- end }}
  {{- if (hasKey $modelSpec "limitGPUMem") }}
  nvidia.com/gpumem: {{ $modelSpec.limitGPUMem | quote }}
  {{- end }}
  {{- if (hasKey $modelSpec "limitGPUMemPercentage") }}
  nvidia.com/gpumem-percentage: {{ $modelSpec.limitGPUMemPercentage | quote }}
  {{- end }}
  {{- if (hasKey $modelSpec "limitGPUCores") }}
  nvidia.com/gpucores: {{ $modelSpec.limitGPUCores | quote }}
  {{- end }}
{{- end }}
{{- end }}

{{/*
Define CPU/memory resources for Mooncake P/D engine containers whose GPU
devices are reserved by the pod-local gpu-reservation sidecar.

requestGPU remains the sizing source of truth for default CPU/memory, but no
GPU extended resource is attached to the engine container itself.
*/}}
{{- define "chart.pdEngineResourcesWithoutGpu" -}}
{{- $modelSpec := . -}}
{{- if not (hasKey $modelSpec "requestGPU") -}}
{{- fail "Value 'modelSpec.requestGPU' must be defined!" -}}
{{- end -}}
{{- $gpuCount := int $modelSpec.requestGPU -}}
{{- if lt $gpuCount 1 -}}
{{- fail (printf "Mooncake P/D engine requestGPU must be >= 1, got %d" $gpuCount) -}}
{{- end -}}
{{- if or
    (hasKey $modelSpec "requestGPUMem")
    (hasKey $modelSpec "requestGPUMemPercentage")
    (hasKey $modelSpec "requestGPUCores")
    (hasKey $modelSpec "limitGPUMem")
    (hasKey $modelSpec "limitGPUMemPercentage")
    (hasKey $modelSpec "limitGPUCores")
-}}
{{- fail "Mooncake P/D shared GPU reservation does not support per-container gpumem/gpucores extended resources" -}}
{{- end -}}
{{- $defaultCPU := printf "%dm" (mul $gpuCount 4000) -}}
{{- $defaultMemory := printf "%dGi" (mul $gpuCount 10) -}}
requests:
  memory: {{ default $defaultMemory $modelSpec.requestMemory | quote }}
  cpu: {{ default $defaultCPU $modelSpec.requestCPU | quote }}
{{- if or (hasKey $modelSpec "limitMemory") (hasKey $modelSpec "limitCPU") }}
limits:
  {{- if hasKey $modelSpec "limitMemory" }}
  memory: {{ $modelSpec.limitMemory | quote }}
  {{- end }}
  {{- if hasKey $modelSpec "limitCPU" }}
  cpu: {{ $modelSpec.limitCPU | quote }}
  {{- end }}
{{- end }}
{{- end }}

{{/*
  Define labels for serving engine and its service
*/}}
{{- define "chart.engineLabels" -}}
{{-   with .Values.servingEngineSpec.labels -}}
{{      toYaml . }}
{{-   end }}
{{- end }}

{{/*
  Define labels for router and its service
*/}}
{{- define "chart.routerLabels" -}}
{{-   with .Values.routerSpec.labels -}}
{{      toYaml . }}
{{-   end }}
{{- end }}

{{/*
  Define labels for cache server and its service
*/}}
{{- define "chart.cacheserverLabels" -}}
{{-   with .Values.cacheserverSpec.labels -}}
{{      toYaml . }}
{{-   end }}
{{- end }}

{{/*
  Define helper function to convert labels to a comma separated list
*/}}
{{- define "labels.toCommaSeparatedList" -}}
{{- $labels := . -}}
{{- $result := "" -}}
{{- range $key, $value := $labels -}}
  {{- if $result }},{{ end -}}
  {{ $key }}={{ $value }}
  {{- $result = "," -}}
{{- end -}}
{{- end -}}


{{/*
  Define helper function to format remote cache url
*/}}
{{- define "cacheserver.formatRemoteUrl" -}}
lm://{{ .service_name }}:{{ .port }}
{{- end -}}
