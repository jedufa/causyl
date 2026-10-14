#!/usr/bin/env bash
set -euo pipefail

script_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
repo_root="$(cd "${script_dir}/../.." && pwd)"

cd "${repo_root}"
exec python infra/bin/scheduler_set_all_scheduled_jobs_enabled.py --enabled false "$@"
