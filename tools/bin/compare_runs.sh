#!/usr/bin/env bash
# compare_runs.sh
# Determinism / Compliance Comparison Tool (Strict Mode Aware)
#
# Usage:
#   ./compare_runs.sh <RUN_DIR_A> <RUN_DIR_B> [OUT_BASE_DIR]
#   ./compare_runs.sh --mode strict <RUN_DIR_A> <RUN_DIR_B> [OUT_BASE_DIR]
#   ./compare_runs.sh --mode all    <RUN_DIR_A> <RUN_DIR_B> [OUT_BASE_DIR]
#
# Options:
#   --mode strict|all         strict hashes determinism-critical artifacts only (recommended for Phase 2)
#   --ignore-regex <regex>    extra exclude regex applied to relative paths (both modes)
#   --no-normalize-checkpoint disable checkpoint JSON normalization (strict mode default is normalize ON)
#
# Strict mode includes:
#   - wal/*.jsonl*
#   - events/*.jsonl*
#   - rng_snapshot* (anywhere under run dir)
#   - checkpoint* files (anywhere under run dir) with created_utc stripped from JSON before hashing
#
# All mode hashes every file except common noise (logs/pycache) unless overridden.
#
set -euo pipefail

die() { echo "[compare_runs] ERROR: $*" >&2; exit 2; }
info() { echo "[compare_runs] $*"; }

need_cmd() { command -v "$1" >/dev/null 2>&1 || die "Missing required command: $1"; }

for cmd in find sort diff sha256sum sed awk head tail zip date grep mktemp; do
  need_cmd "$cmd"
done

MODE="all"
IGNORE_REGEX=""
NORMALIZE_CHECKPOINT="1"

# -------------------------------------------
# Arg parsing (minimal, no external deps)
# -------------------------------------------
ARGS=()
while [[ $# -gt 0 ]]; do
  case "$1" in
    --mode)
      shift
      [[ $# -gt 0 ]] || die "--mode requires a value: strict|all"
      MODE="$1"
      [[ "$MODE" == "strict" || "$MODE" == "all" ]] || die "Invalid --mode '$MODE' (expected strict|all)"
      shift
      ;;
    --ignore-regex)
      shift
      [[ $# -gt 0 ]] || die "--ignore-regex requires a value"
      IGNORE_REGEX="$1"
      shift
      ;;
    --no-normalize-checkpoint)
      NORMALIZE_CHECKPOINT="0"
      shift
      ;;
    -h|--help)
      cat <<EOF
Usage:
  ./compare_runs.sh [--mode strict|all] [--ignore-regex <regex>] [--no-normalize-checkpoint] <RUN_DIR_A> <RUN_DIR_B> [OUT_BASE_DIR]

Modes:
  strict  Hash determinism-critical artifacts (Phase-2 friendly)
  all     Hash everything (noisy but comprehensive)

EOF
      exit 0
      ;;
    *)
      ARGS+=("$1")
      shift
      ;;
  esac
done

RUN_A="${ARGS[0]:-}"
RUN_B="${ARGS[1]:-}"
OUT_BASE="${ARGS[2]:-.}"

[[ -n "$RUN_A" && -n "$RUN_B" ]] || die "Usage: $0 [--mode strict|all] <RUN_DIR_A> <RUN_DIR_B> [OUT_BASE_DIR]"
[[ -d "$RUN_A" ]] || die "RUN_DIR_A not found: $RUN_A"
[[ -d "$RUN_B" ]] || die "RUN_DIR_B not found: $RUN_B"
[[ -d "$OUT_BASE" ]] || die "OUT_BASE_DIR not found: $OUT_BASE"

TS="$(date +%Y%m%d_%H%M%S)"
OUT_DIR="${OUT_BASE%/}/run_compare_${TS}"
ZIP_PATH="${OUT_BASE%/}/run_compare_${TS}.zip"

mkdir -p "$OUT_DIR"/{meta,hashes,diffs,artifacts,logs,notes,tmp}

# -------------------------------------------------
# Metadata capture (non-deterministic by design)
# -------------------------------------------------
info "Capturing metadata..."
{
  echo "timestamp_utc=$(date -u +%Y-%m-%dT%H:%M:%SZ)"
  echo "mode=$MODE"
  echo "normalize_checkpoint=$NORMALIZE_CHECKPOINT"
  echo "ignore_regex=${IGNORE_REGEX:-<none>}"
  echo "run_a=$RUN_A"
  echo "run_b=$RUN_B"
  echo "host=$(hostname 2>/dev/null || true)"
  echo "uname=$(uname -a 2>/dev/null || true)"
} > "$OUT_DIR/meta/meta.txt"

if command -v git >/dev/null 2>&1 && git rev-parse --is-inside-work-tree >/dev/null 2>&1; then
  {
    echo "git_commit=$(git rev-parse HEAD)"
    echo "git_branch=$(git rev-parse --abbrev-ref HEAD)"
  } > "$OUT_DIR/meta/git.txt"
fi

if command -v python >/dev/null 2>&1; then
  {
    python -c 'import sys; print("python_exec=" + sys.executable)'
    python -c 'import sys; print("python_version=" + sys.version.replace("\n"," "))'
  } > "$OUT_DIR/meta/python.txt"
fi

# -------------------------------------------------
# Determinism flag evidence (informational only)
# -------------------------------------------------
info "Checking determinism flag evidence (informational only)..."
check_determinism() {
  local root="$1"
  local label="$2"
  local out="$OUT_DIR/notes/determinism_flag_check.txt"

  echo "---- RUN $label ----" >> "$out"

  local hits
  hits=$(grep -R "DEBUG_DETERMINISM" "$root" 2>/dev/null || true)
  if [[ -n "$hits" ]]; then
    echo "DEBUG_DETERMINISM reference found: YES" >> "$out"
  else
    echo "DEBUG_DETERMINISM reference found: NO" >> "$out"
  fi

  local strict_hits
  strict_hits=$(grep -Ri "determinism" "$root" 2>/dev/null || true)
  if [[ -n "$strict_hits" ]]; then
    echo "Any artifact mentions 'determinism': YES" >> "$out"
  else
    echo "Any artifact mentions 'determinism': NO" >> "$out"
  fi
  echo "" >> "$out"
}
> "$OUT_DIR/notes/determinism_flag_check.txt"
check_determinism "$RUN_A" "A"
check_determinism "$RUN_B" "B"

# -------------------------------------------------
# Helpers
# -------------------------------------------------
should_exclude_path() {
  local rel="$1"

  # Always exclude obvious noise unless user explicitly wants to compare it.
  # (still can be forced back in by using --mode all and a custom ignore-regex that doesn't match these)
  if [[ "$rel" =~ (^|/)(__pycache__|\.pytest_cache)(/|$) ]]; then return 0; fi
  if [[ "$rel" =~ \.pyc$ ]]; then return 0; fi

  # In all mode, exclude common log-ish junk that often contains timestamps unless user wants it.
  if [[ "$MODE" == "all" ]]; then
    if [[ "$rel" =~ (^|/)(logs?|log)(/|$) ]]; then return 0; fi
    if [[ "$rel" =~ \.log$ ]]; then return 0; fi
  fi

  # User-specified ignore regex (applies in both modes)
  if [[ -n "$IGNORE_REGEX" ]]; then
    if echo "$rel" | grep -Eq "$IGNORE_REGEX"; then return 0; fi
  fi

  return 1
}

list_files_strict() {
  local root="$1"
  ( cd "$root" && {
      # Use find + path filters to include only determinism-critical artifacts.
      # Note: Keep order stable: sort after listing.
      find . -type f \( \
        -path './wal/*' -o \
        -path './events/*' -o \
        -name 'rng_snapshot*' -o \
        -name 'checkpoint*' \
      \) -print \
      | sed 's#^\./##' \
      | while read -r rel; do
          should_exclude_path "$rel" && continue
          echo "$rel"
        done \
      | sort
    } )
}

list_files_all() {
  local root="$1"
  ( cd "$root" && {
      find . -type f -print \
      | sed 's#^\./##' \
      | while read -r rel; do
          should_exclude_path "$rel" && continue
          echo "$rel"
        done \
      | sort
    } )
}

normalize_checkpoint_json_if_needed() {
  # Outputs a path to a temp normalized file, or empty if no normalization applied.
  # Args: root, relpath
  local root="$1"
  local rel="$2"

  [[ "$NORMALIZE_CHECKPOINT" == "1" ]] || { echo ""; return 0; }
  [[ "$MODE" == "strict" ]] || { echo ""; return 0; }

  # Only normalize obvious checkpoint JSON files.
  # If your checkpoint format changes, adjust this predicate.
  if [[ "$rel" =~ checkpoint ]] && [[ "$rel" =~ \.json$ ]]; then
    command -v python >/dev/null 2>&1 || { echo ""; return 0; }
    local tmp
    tmp="$(mktemp "$OUT_DIR/tmp/ckpt_norm_XXXXXX.json")"
    python - "$root/$rel" "$tmp" <<'PY'
import json, sys
src, dst = sys.argv[1], sys.argv[2]

def scrub(obj):
    if isinstance(obj, dict):
        # Remove known nondeterministic metadata fields
        obj.pop("created_utc", None)
        obj.pop("timestamp_utc", None)
        # Recurse
        for k, v in list(obj.items()):
            obj[k] = scrub(v)
        return obj
    if isinstance(obj, list):
        return [scrub(x) for x in obj]
    return obj

with open(src, "r", encoding="utf-8") as f:
    data = json.load(f)

data = scrub(data)

with open(dst, "w", encoding="utf-8") as f:
    json.dump(data, f, sort_keys=True, separators=(",", ":"))
PY
    echo "$tmp"
    return 0
  fi

  echo ""
}

hash_manifest() {
  # Build sha256 manifest for a given run dir from its file list.
  # Emits: "<sha256>  <relpath>"
  local root="$1"
  local files_list="$2"
  local out_manifest="$3"

  : > "$out_manifest"
  while IFS= read -r rel; do
    [[ -n "$rel" ]] || continue

    local norm_path=""
    norm_path="$(normalize_checkpoint_json_if_needed "$root" "$rel")"
    if [[ -n "$norm_path" ]]; then
      # Mark normalized files explicitly in the manifest path
      local sha
      sha="$(sha256sum "$norm_path" | awk '{print $1}')"
      printf "%s  %s\n" "$sha" "${rel} (normalized)" >> "$out_manifest"
    else
      local sha
      sha="$(sha256sum "$root/$rel" | awk '{print $1}')"
      printf "%s  %s\n" "$sha" "$rel" >> "$out_manifest"
    fi
  done < "$files_list"

  # Sort by path for stable diffs
  sort -k2 -o "$out_manifest" "$out_manifest"
}

# -------------------------------------------------
# Structural inventory + hashing
# -------------------------------------------------
info "Building file inventories (mode=$MODE)..."
FILES_A="$OUT_DIR/hashes/files_A.txt"
FILES_B="$OUT_DIR/hashes/files_B.txt"

if [[ "$MODE" == "strict" ]]; then
  list_files_strict "$RUN_A" > "$FILES_A"
  list_files_strict "$RUN_B" > "$FILES_B"
else
  list_files_all "$RUN_A" > "$FILES_A"
  list_files_all "$RUN_B" > "$FILES_B"
fi

info "Computing SHA256 manifests..."
SHA_A="$OUT_DIR/hashes/sha256_A.txt"
SHA_B="$OUT_DIR/hashes/sha256_B.txt"
hash_manifest "$RUN_A" "$FILES_A" "$SHA_A"
hash_manifest "$RUN_B" "$FILES_B" "$SHA_B"

diff -u "$FILES_A" "$FILES_B" > "$OUT_DIR/diffs/file_list.diff" || true
diff -u "$SHA_A" "$SHA_B" > "$OUT_DIR/diffs/sha256.diff" || true

# -------------------------------------------------
# Human-friendly summary
# -------------------------------------------------
info "Writing summary..."
SUMMARY="$OUT_DIR/notes/summary.txt"
{
  echo "mode=$MODE"
  echo "run_a=$RUN_A"
  echo "run_b=$RUN_B"
  echo "diff_file_list=$(wc -l < "$OUT_DIR/diffs/file_list.diff" | awk '{print $1}') lines"
  echo "diff_sha256=$(wc -l < "$OUT_DIR/diffs/sha256.diff" | awk '{print $1}') lines"
  echo ""
  echo "Tip:"
  echo "  - For Phase-2 determinism, prefer: --mode strict"
  echo "  - If diffs are only in checkpoint created_utc, strict mode normalization should eliminate them."
} > "$SUMMARY"

# -------------------------------------------------
# Create zip
# -------------------------------------------------
info "Creating zip bundle..."
( cd "$OUT_BASE" && zip -r "$(basename "$ZIP_PATH")" "$(basename "$OUT_DIR")" >/dev/null )

info "Done."
echo "Bundle: $ZIP_PATH"
