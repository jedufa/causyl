#!/usr/bin/env bash

MESSAGE="$*"

curl -s -X POST http://localhost:8787/api/agents/send \
  -H 'Content-Type: application/json' \
  -d "{
    \"role\": \"planner\",
    \"binding\": {\"type\": \"thread_id\", \"id\": \"jeff-cli\"},
    \"message\": \"$MESSAGE\"
  }" | jq
