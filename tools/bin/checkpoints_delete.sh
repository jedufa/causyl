#!/usr/bin/env bash

# ------------------------------------------------------------
# checkpoint_delete.sh
#
# Deletes all "checkpoint_bundles_v2" either:
#   - From folders inside a directory tree
#   - From zip files inside a directory tree
#   - Or both
#
# Usage:
#   ./checkpoints_delete.sh --folders
#   ./checkpoints_delete.sh --zips
#   ./checkpoints_delete.sh --folders --zips
#   ./checkpoints_delete.sh --dir runs --folders --zips
#
# Requirements:
#   - At least one of --folders or --zips must be provided
#   - --dir is optional (default: runs)
# ------------------------------------------------------------

set -e

TARGET_DIR="runs"
MODE_FOLDERS=false
MODE_ZIPS=false

print_usage() {
    echo "Usage:"
    echo "  $0 [--dir <directory>] [--folders] [--zips]"
    echo
    echo "Options:"
    echo "  --dir <directory>   Root directory (default: runs)"
    echo "  --folders           Delete checkpoint_bundles_v2 folders"
    echo "  --zips              Delete checkpoint_bundles_v2 from zip files"
    echo
    echo "At least one of --folders or --zips must be specified."
    exit 1
}

# ----------------------------
# Parse arguments
# ----------------------------

while [[ $# -gt 0 ]]; do
    case "$1" in
        --dir)
            TARGET_DIR="$2"
            shift 2
            ;;
        --folders)
            MODE_FOLDERS=true
            shift
            ;;
        --zips)
            MODE_ZIPS=true
            shift
            ;;
        *)
            echo "Unknown argument: $1"
            print_usage
            ;;
    esac
done

# ----------------------------
# Validate input
# ----------------------------

if [[ "$MODE_FOLDERS" == false && "$MODE_ZIPS" == false ]]; then
    echo "Error: You must specify at least one of --folders or --zips."
    exit 1
fi

if [[ ! -d "$TARGET_DIR" ]]; then
    echo "Error: Directory '$TARGET_DIR' does not exist."
    exit 1
fi

# ----------------------------
# Folder mode
# ----------------------------

if [[ "$MODE_FOLDERS" == true ]]; then
    echo "Deleting checkpoint_bundles_v2 folders under $TARGET_DIR..."
    find "$TARGET_DIR" -type d -name "checkpoint_bundles_v2" -exec rm -rf {} +
    echo "Folder cleanup done."
fi

# ----------------------------
# Zip mode
# ----------------------------

if [[ "$MODE_ZIPS" == true ]]; then
    echo "Cleaning checkpoint_bundles_v2 entries from zip files under $TARGET_DIR..."

    find "$TARGET_DIR" -type f -name "*.zip" | while read -r zipfile; do
        echo "Processing $zipfile"
        zip -d "$zipfile" "*/checkpoint_bundles_v2/*" >/dev/null 2>&1 || true
    done

    echo "Zip cleanup done."
fi

echo "All requested operations completed."