#!/bin/sh
# Copyright (c) 2026 OceanBase.
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
# http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

# Run the Server with scheduled-processing tracing enabled, for reproducing the
# `scheduled.process_source_window` / `scheduled.incubate_experience_candidates`
# span tree in Phoenix (or any OTLP backend).

set -eu

# Overridable via environment.
schedule_seconds=${POWERCONTEXT_SERVER_RUNTIME_SCHEDULE_SECONDS:-10}
experience_schedule_seconds=${POWERCONTEXT_SERVER_RUNTIME_EXPERIENCE_SCHEDULE_SECONDS:-}
generation_model=${POWERCONTEXT_SERVER_INFERENCE_GENERATION_MODEL:-openrouter:deepseek/deepseek-v4-pro}
otlp_endpoint=${OTEL_EXPORTER_OTLP_ENDPOINT:-http://localhost:6006}
service_name=${OTEL_SERVICE_NAME:-powercontext-server}

usage() {
    cat <<'EOF'
Run the PowerContext Server with scheduled-processing tracing enabled.

Usage:
  scripts/trace_scheduled_demo.sh [--phoenix]

Options:
  --phoenix, -p   Start (or restart) a local Phoenix container on :6006 first.
  --help, -h      Show this help.

Configuration is read from the environment with these defaults:
  POWERCONTEXT_SERVER_RUNTIME_SCHEDULE_SECONDS          (default 10)
  POWERCONTEXT_SERVER_RUNTIME_EXPERIENCE_SCHEDULE_SECONDS (default unset)
  POWERCONTEXT_SERVER_INFERENCE_GENERATION_MODEL        (default openrouter:deepseek/deepseek-v4-pro)
  OTEL_EXPORTER_OTLP_ENDPOINT                           (default http://localhost:6006)
  OTEL_SERVICE_NAME                                     (default powercontext-server)

Also set the provider credential your generation model needs (for example
OPENROUTER_API_KEY); PowerContext never records it.
EOF
}

api_key_env_for() {
    # Map a "provider:model" string to its API key variable, or "" when none is required.
    provider=${1%%:*}
    case "$provider" in
        openai) echo "OPENAI_API_KEY" ;;
        anthropic) echo "ANTHROPIC_API_KEY" ;;
        openrouter) echo "OPENROUTER_API_KEY" ;;
        deepseek) echo "DEEPSEEK_API_KEY" ;;
        groq) echo "GROQ_API_KEY" ;;
        mistral) echo "MISTRAL_API_KEY" ;;
        cohere) echo "CO_API_KEY" ;;
        google-gla | google-gemini | gemini | vertexai) echo "GEMINI_API_KEY" ;;
        ollama | test | mock) echo "" ;;
        *) echo "" ;;
    esac
}

start_phoenix=0
case "${1:-}" in
    --phoenix | -p) start_phoenix=1 ;;
    --help | -h) usage; exit 0 ;;
    "") : ;;
    *) usage >&2; exit 2 ;;
esac

if [ "$start_phoenix" = "1" ]; then
    docker rm -f powercontext-phoenix >/dev/null 2>&1 || true
    docker run -d --name powercontext-phoenix -p 6006:6006 arizephoenix/phoenix:20.1.0
    echo "Phoenix: http://localhost:6006 (project: default)"
fi

export POWERCONTEXT_SERVER_TRACING_ENABLED=true
export OTEL_EXPORTER_OTLP_ENDPOINT="$otlp_endpoint"
export OTEL_SERVICE_NAME="$service_name"
export POWERCONTEXT_SERVER_RUNTIME_SCHEDULE_SECONDS="$schedule_seconds"
if [ -n "$experience_schedule_seconds" ]; then
    export POWERCONTEXT_SERVER_RUNTIME_EXPERIENCE_SCHEDULE_SECONDS="$experience_schedule_seconds"
fi
export POWERCONTEXT_SERVER_INFERENCE_GENERATION_MODEL="$generation_model"

cat <<EOF

Scheduled tracing demo config:
  OTLP endpoint:     $otlp_endpoint (service "$service_name")
  source window:     every ${schedule_seconds}s
  experience:        ${experience_schedule_seconds:-disabled}
  generation model:  $generation_model

Capture a Source so the scheduler has something to process:
  curl -X POST http://localhost:8000/v1/sources/content \
    -H 'content-type: application/json' \
    -d '{"scope_id":"project:demo","source_id":"task-1","content":"I always book aisle seats."}'

Then open Phoenix and filter for the "scheduled.process_source_window" span.
EOF

key_env=$(api_key_env_for "$generation_model")
if [ -n "$key_env" ] && [ -z "$(printenv "$key_env")" ]; then
    cat >&2 <<EOF

Error: generation model "$generation_model" needs $key_env, which is not set.
Export it before starting (scheduled extraction requires a working generation model):
  export $key_env=sk-...

EOF
    exit 1
fi

# Assumes the repo's uv environment; alternatively `.venv/bin/powercontext server run`.
exec uv run powercontext server run
