#!/usr/bin/env python3
import os
import re
import sys
from pathlib import Path

import patch_ng
import requests
import yaml
import json
from openai import OpenAI


try:
    from xai_sdk import Grok as XAIGrok
    HAS_XAI_SDK = True
except ImportError:  # fallback to OpenAI client for compatibility layer
    XAIGrok = None
    HAS_XAI_SDK = False

# ANSI Colors for UX
class Colors:
    HEADER = '\033[95m'
    OKBLUE = '\033[94m'
    OKCYAN = '\033[96m'
    OKGREEN = '\033[92m'
    WARNING = '\033[93m'
    FAIL = '\033[91m'
    ENDC = '\033[0m'
    BOLD = '\033[1m'

print(f"{Colors.HEADER}{Colors.BOLD}🤖 Grok Audit Agent - Interactive Mode{Colors.ENDC}")

# Config
API_KEY = os.getenv("XAI_API_KEY")
if not API_KEY:
    API_KEY = input(f"{Colors.WARNING}Enter XAI_API_KEY (or export XAI_API_KEY): {Colors.ENDC}").strip()
    os.environ["XAI_API_KEY"] = API_KEY


def make_grok_client(api_key):
    if HAS_XAI_SDK:
        return XAIGrok(api_key=api_key)
    # OpenAI-compatible fallback must use xAI endpoint.
    return OpenAI(api_key=api_key, base_url="https://api.x.ai/v1")


GROK_CLIENT = make_grok_client(API_KEY)

SCRATCH_FILE = Path("./tmp/grok-transfer-scratch.txt")
DIFF_DIR = Path("./tmp/grok/diffs")
DEFAULT_PROMPT = """
Audit robustness/code quality/failures. Suggest fixes as UNIFIED DIFFS (`diff --git` format in ```diff blocks).
Files: {files}
Contents at: {scratch_url}
Output: Markdown report + diffs.
"""
PROMPT_INLINE_LIMIT = 300_000

def print_step(msg):
    print(f"{Colors.OKCYAN}📋 {msg}{Colors.ENDC}")

def find_files(names, root="."):
    root = Path(root)
    files = []
    for name in names:
        name = name.strip()
        if not name:
            continue
        is_glob = any(ch in name for ch in "*?[]")
        has_path = "/" in name or "\\" in name or name.startswith(".")
        if has_path and not is_glob:
            candidate = Path(name)
            candidate_alt = root / candidate
            if candidate.exists():
                files.append(candidate)
                continue
            if candidate_alt.exists():
                files.append(candidate_alt)
                continue
            print(f"{Colors.WARNING}Path not found: {name}{Colors.ENDC}")
            continue
        matches = list(root.rglob(name))
        files.extend(matches)
    return sorted(set(files))  # Dedupe

def build_scratch(files):
    SCRATCH_FILE.parent.mkdir(exist_ok=True)
    with open(SCRATCH_FILE, "w") as f:
        f.write(f"# Interactive Grok Scratch ({len(files)} files)\n\n")
        for fp in files:
            try:
                rel = fp.relative_to(SCRATCH_FILE.parent)
            except ValueError:
                rel = Path(fp).name
            f.write(f"=== {rel} ===\n")
            f.write(fp.read_text(encoding="utf-8", errors="ignore"))
            f.write("\n\n---\n\n")
    return SCRATCH_FILE.read_text()

def _local_scratch_url():
    return SCRATCH_FILE.resolve().as_uri()


def upload_scratch(content):
    token = os.environ.get("GITHUB_TOKEN") or os.environ.get("GH_TOKEN")
    if not token:
        print(f"{Colors.WARNING}No GitHub token set (GITHUB_TOKEN/GH_TOKEN). Using local scratch file.{Colors.ENDC}")
        return _local_scratch_url()

    headers = {"Authorization": f"token {token}"}
    resp = requests.post(
        "https://api.github.com/gists",
        headers=headers,
        json={
            "description": "Grok interactive scratch",
            "public": True,
            "files": {"grok-interactive-scratch.txt": {"content": content[:1_000_000]}},
        },
    )
    data = resp.json()
    if resp.status_code != 201 or "message" in data:
        print(f"{Colors.WARNING}Gist failed ({resp.status_code}): {data.get('message', 'unknown')}. Using local scratch path.{Colors.ENDC}")
        return _local_scratch_url()
    return data["files"]["grok-interactive-scratch.txt"]["raw_url"]

def extract_diffs(report):
    DIFF_DIR.mkdir(parents=True, exist_ok=True)
    fence_pattern = r'```([a-zA-Z0-9_-]*)\s*\n(.*?)\n```'
    fenced_blocks = re.findall(fence_pattern, report, re.DOTALL | re.IGNORECASE)
    diff_blocks = []
    non_diff_code_blocks = []
    for lang, block in fenced_blocks:
        normalized_lang = (lang or "").strip().lower()
        text = block.strip()
        looks_like_diff = (
            normalized_lang in {"diff", "patch"}
            or "diff --git " in text
            or (text.startswith("--- ") and "\n+++ " in text)
        )
        if looks_like_diff:
            diff_blocks.append(text)
        elif normalized_lang:
            non_diff_code_blocks.append((normalized_lang, text))

    extracted = 0
    for block in diff_blocks:
        m = re.search(r'diff --git [ab]/([^ \n/]+)', block)
        fname = m.group(1) if m else f"unknown_{extracted}.diff"
        diff_path = DIFF_DIR / f"{fname}"
        diff_path.write_text(block.strip())
        print_step(f"💾 Diff saved: {diff_path.name}")
        extracted += 1
    if extracted == 0 and non_diff_code_blocks:
        print(
            f"{Colors.WARNING}No diff/patch blocks found. Model returned code blocks only; cannot auto-apply as patches.{Colors.ENDC}"
        )
        for idx, (lang, block) in enumerate(non_diff_code_blocks[:3]):
            out = DIFF_DIR / f"non_diff_block_{idx}.{lang}.txt"
            out.write_text(block)
            print_step(f"📝 Saved non-diff block for review: {out.name}")
    return extracted

def apply_diffs(dry_run=True):
    if not DIFF_DIR.exists():
        print(f"{Colors.WARNING}No diff directory found at {DIFF_DIR}. Nothing to apply.{Colors.ENDC}")
        return 0
    applied = 0
    for diff_file in DIFF_DIR.glob("*.diff"):
        # Infer target from diff header
        with open(diff_file) as f:
            first_line = f.readline()
        target_match = re.search(r'a/(.+?)(?:\s|$)', first_line)
        target = Path(target_match.group(1)) if target_match else None
        if target and target.exists():
            print_step(f"🔧 {'[DRY-RUN]' if dry_run else '[APPLIED]'} {target} <- {diff_file.name}")
            if not dry_run:
                p = patch_ng.fromfile(diff_file)
                p.apply(target)
            applied += 1
        else:
            print(f"{Colors.WARNING}Skipped {diff_file.name} (no target){Colors.ENDC}")
    return applied

def main():
    print_step("Starting interactive audit...")
    
    # Load your xAI API key from environment variable
    api_key = os.getenv("XAI_API_KEY")
    if not api_key:
        raise ValueError("XAI_API_KEY environment variable not set")
    print("Calling Grok with key prefix:", api_key[:20] + "...")  # Safe log


    # 1. Custom Prompt
    prompt = input(f"{Colors.OKGREEN}Enter custom prompt (ENTER for default audit):\n{Colors.ENDC}").strip()
    if not prompt:
        prompt = DEFAULT_PROMPT

    # 2. File List
    files_str = input(f"{Colors.OKGREEN}Enter file names (comma/space sep, e.g. 'scheduler.py *.db'):\n{Colors.ENDC}").strip()
    file_names = [name for name in re.split(r'[,\s]+', files_str) if name]
    if not file_names:
        print(f"{Colors.FAIL}No files? Exiting.{Colors.ENDC}")
        return

    # 3. Search Root
    root = input(f"{Colors.OKGREEN}Search root dir (ENTER for ./): {Colors.ENDC}").strip() or "."

    # Find & Confirm
    files = find_files(file_names, root)
    if not files:
        print(f"{Colors.FAIL}No matching files found under '{root}'.{Colors.ENDC}")
        return
    print(f"{Colors.OKBLUE}Found {len(files)} files:{Colors.ENDC}")
    for f in files:
        print(f"  📄 {f}")
    confirm = input(f"{Colors.WARNING}Proceed? (y/n): {Colors.ENDC}").lower()
    if confirm != 'y':
        print("Aborted.")
        return

    # 4. Scratch & Upload
    print_step("Building scratch file...")
    scratch_content = build_scratch(files)
    scratch_url = upload_scratch(scratch_content)
    print(f"{Colors.OKGREEN}Scratch URL: {scratch_url}{Colors.ENDC}")

    # 5. Grok Call
    print_step("Calling Grok...")
    full_prompt = prompt.format(files=[f.name for f in files], scratch_url=scratch_url)
    if scratch_url.startswith("file://"):
        inline_content = scratch_content
        truncated = False
        if len(inline_content) > PROMPT_INLINE_LIMIT:
            inline_content = inline_content[:PROMPT_INLINE_LIMIT]
            truncated = True
        suffix = "\n\n[truncated due to size]" if truncated else ""
        full_prompt += (
            "\n\nScratch content is embedded below because local file:// URLs are not externally accessible.\n"
            f"```text\n{inline_content}{suffix}\n```"
        )
    try:
        resp = GROK_CLIENT.chat.completions.create(
            model="grok-code-fast-1",
            messages=[{"role": "user", "content": full_prompt}],
            max_tokens=16000,
            temperature=0.3,  # Precise for diffs
        )
    except Exception as e:
        msg = str(e)
        if any(token in msg for token in ("invalid_api_key", "Incorrect API key", "401", "authentication", "Unauthorized")):
            print(f"{Colors.FAIL}Grok auth failed. Check XAI_API_KEY and ensure this request is going to https://api.x.ai/v1.{Colors.ENDC}")
        elif "model" in msg and "not" in msg and "found" in msg:
            print(f"{Colors.FAIL}Grok model unavailable. Verify model name and account access (tried: grok-code-fast-1).{Colors.ENDC}")
        else:
            print(f"{Colors.FAIL}Grok call failed: {e}{Colors.ENDC}")
        return
    report = resp.choices[0].message.content



    # 6. Save Report
    timestamp = os.popen("date +%Y%m%d_%H%M%S").read().strip()
    REPORT_PATH = Path(f"./tmp/grok_audit_report_{timestamp}.md")
    REPORT_PATH.write_text(report)
    print_step(f"Full report saved: {REPORT_PATH}")

    # 7. Extract Diffs
    num_diffs = extract_diffs(report)
    print_step(f"Extracted {num_diffs} diffs to {DIFF_DIR}")

    # 8. Diffs Apply?
    apply_choice = input(f"{Colors.OKGREEN}Dry-run apply diffs? (y/n/force): {Colors.ENDC}").lower()
    if apply_choice == 'y':
        apply_diffs(dry_run=True)
    elif apply_choice == 'force':
        confirm_force = input(f"{Colors.FAIL}Really apply (irreversible)? y/n: {Colors.ENDC}")
        if confirm_force == 'y':
            apply_diffs(dry_run=False)

    print(f"{Colors.OKGREEN}🎉 Done! Check ./tmp/{Colors.ENDC}")

if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print(f"\n{Colors.WARNING}Cancelled.{Colors.ENDC}")
    except Exception as e:
        print(f"{Colors.FAIL}Error: {e}{Colors.ENDC}")
        sys.exit(1)
