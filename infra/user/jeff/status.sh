#!/usr/bin/env bash

curl -s http://localhost:8787/api/admin/lab_assistant/snapshot | jq '
{
  operator_state,
  summary,
  host_mode: .backend_projection.host_mode,
  host_status: .backend_projection.host_status,
  active_tasks: [.active_tasks[]? | {
    task_id,
    status,
    execution_surface: (.execution_surface // .surface // "")
  }],
  open_loops: [.open_loops[]? | {
    task_id,
    kind,
    next_action
  }]
}'
