"""G6N7: Deploy, Runtime Control, Live Refresh Boundaries, and Drift Contract.

This module tests runtime refresh semantics with explicit fail-closed behavior,
scope boundaries, and drift handling for unsupported deployments.

Acceptance criteria (from PIS-20260313-092604Z-G6N7):
- refresh and deploy semantics are explicit, safe, and operator-auditable
- unsupported live actions fail closed with concrete remediation steps
- in-flight work keeps pre-refresh policy context (scope boundary)
"""

from __future__ import annotations

import json
import unittest
from pathlib import Path
from typing import Any, Dict, Optional
from unittest.mock import MagicMock, Mock, patch

# Import the module under test
from infra.controlplane import plane_mission_contract as mission_contract
from infra.webui import server_lab_assistant_web_operations as web_ops


class TestG6N7RuntimeRefreshCapabilityCheck(unittest.TestCase):
    """Test capability check for live runtime refresh (drift detection)."""

    def test_refresh_capability_check_success(self):
        """Positive: capability check succeeds when refresh_live_runtime works."""
        with patch("infra.webui.server_lab_assistant_web_operations._agents.refresh_live_runtime") as mock_refresh:
            mock_refresh.return_value = {"ok": True, "backend": "test"}
            result = web_ops._refresh_capability_check()

            self.assertTrue(result.get("supported"))
            self.assertIsNone(result.get("reason"))
            self.assertIn("check_utc", result)

    def test_refresh_capability_check_404_not_found(self):
        """Negative: capability check fails closed when refresh endpoint returns 404."""
        with patch("infra.webui.server_lab_assistant_web_operations._agents.refresh_live_runtime") as mock_refresh:
            mock_refresh.side_effect = Exception("404: not found")
            result = web_ops._refresh_capability_check()

            self.assertFalse(result.get("supported"))
            self.assertEqual(result.get("reason"), "refresh_endpoint_not_found")
            self.assertIn("remediation", result)
            self.assertIn("Restart the server", result.get("remediation", ""))

    def test_refresh_capability_check_generic_error(self):
        """Negative: generic error treated as capability gap, fails closed."""
        with patch("infra.webui.server_lab_assistant_web_operations._agents.refresh_live_runtime") as mock_refresh:
            mock_refresh.side_effect = RuntimeError("unexpected error")
            result = web_ops._refresh_capability_check()

            self.assertFalse(result.get("supported"))
            self.assertEqual(result.get("reason"), "refresh_capability_check_failed")
            self.assertIn("remediation", result)


class TestG6N7RuntimeRefreshPayload(unittest.TestCase):
    """Test runtime refresh payload with explicit scope boundaries."""

    def test_payload_success_includes_scope_boundaries(self):
        """Positive: successful refresh includes explicit live/deploy boundaries."""
        with patch("infra.webui.server_lab_assistant_web_operations._agents.refresh_live_runtime") as mock_agents, \
             patch("infra.webui.server_lab_assistant_web_operations._agent_orch.current_runtime_defaults") as mock_orch, \
             patch("infra.webui.server_lab_assistant_web_operations._refresh_capability_check") as mock_check:

            mock_agents.return_value = {"ok": True, "backend": "test"}
            mock_orch.return_value = {"defaults": {}}
            mock_check.return_value = {"supported": True}

            payload = web_ops._runtime_refresh_payload()

            self.assertTrue(payload.get("ok"))
            scope = payload.get("scope", {})
            self.assertEqual(scope.get("applies_to"), "future_work_started_after_refresh")
            self.assertEqual(scope.get("does_not_apply_to"), "in_flight_turns_or_jobs")

            # G6N7: Explicit scope boundaries
            live_refreshable = scope.get("live_refreshable", [])
            deploy_gated = scope.get("deploy_gated", [])
            self.assertIn("runtime_policy.properties", live_refreshable)
            self.assertIn("prompt_files", live_refreshable)
            self.assertIn("server_code", deploy_gated)
            self.assertIn("android_apk", deploy_gated)
            self.assertIn("in_flight_job_state", deploy_gated)
            self.assertEqual(
                payload.get("message"),
                mission_contract.read_plane_mission_contract()["runtime_projection"]["refresh_scope_summary"],
            )
            self.assertEqual(
                scope.get("mission_claim_ceiling_summary"),
                mission_contract.read_plane_mission_contract()["runtime_projection"]["claim_ceiling_summary"],
            )

    def test_payload_failure_when_refresh_unsupported(self):
        """Negative: payload fails closed when refresh capability is not available."""
        with patch("infra.webui.server_lab_assistant_web_operations._agents.refresh_live_runtime") as mock_agents, \
             patch("infra.webui.server_lab_assistant_web_operations._refresh_capability_check") as mock_check:

            mock_check.return_value = {
                "supported": False,
                "reason": "refresh_endpoint_not_found",
                "detail": "404 not found",
                "remediation": "Restart required",
            }

            payload = web_ops._runtime_refresh_payload()

            self.assertFalse(payload.get("ok"))
            self.assertEqual(payload.get("reason"), "refresh_endpoint_not_found")
            self.assertIn("remediation", payload)
            self.assertEqual(payload.get("message"), "Live runtime refresh is not supported; server restart required for changes.")

            # G6N7: Drift semantics - unsupported refresh is explicit, not hidden
            scope = payload.get("scope", {})
            self.assertIn("server_code", scope.get("deploy_gated", []))
            self.assertEqual(scope.get("source_of_truth"), "capability_check_result")

    def test_payload_fails_closed_when_mission_contract_is_unavailable(self):
        """Negative: missing mission framing blocks refresh instead of falling back."""
        with patch(
            "infra.webui.server_lab_assistant_web_operations._plane_mission_contract.read_plane_mission_contract"
        ) as mock_contract:
            mock_contract.return_value = {
                "ok": False,
                "failure_class": "mission_contract_invalid",
                "summary": "Canonical plane mission contract failed validation.",
            }

            payload = web_ops._runtime_refresh_payload()

            self.assertFalse(payload.get("ok"))
            self.assertEqual(payload.get("reason"), "mission_contract_unavailable")
            self.assertEqual(payload.get("message"), "Live runtime refresh blocked because mission framing is unavailable.")
            self.assertEqual(payload.get("scope", {}).get("source_of_truth"), "mission_contract_failure")


class TestG6N7ConfirmationProjection(unittest.TestCase):
    """Test confirmation semantics for refresh actions (scope boundary verification)."""

    def test_confirmation_success_includes_scope_note(self):
        """Positive: confirmed refresh explicitly notes scope limitation."""
        event = {
            "action_id": "refresh_runtime",
            "requested_utc": "2026-03-14T10:00:00Z",
            "invocation": {
                "ok": True,
                "response": {
                    "ok": True,
                    "message": "Refreshed",
                    "scope": {
                        "applies_to": "future_work_started_after_refresh",
                    },
                    "capability_check": {
                        "supported": True,
                    },
                }
            }
        }
        queue_status = {
            "state": {
                "status": "idle",
                "active": False,
                "current_workflow_id": "",
            }
        }
        operator_snapshot = {
            "generated_utc": "2026-03-14T10:00:01Z",  # after request
            "backend_projection": {"state": "healthy"},
        }

        confirmation = web_ops._confirmation_projection(
            action_id="refresh_runtime",
            event=event,
            queue_status=queue_status,
            operator_snapshot=operator_snapshot,
        )

        self.assertEqual(confirmation.get("state"), "confirmed")
        # G6N7: Scope note must be present
        self.assertIn("scope_note", confirmation)
        scope_note = confirmation.get("scope_note", "").lower()
        self.assertIn("in-flight", scope_note)
        self.assertIn("prior", scope_note)

    def test_confirmation_fails_closed_when_refresh_unsupported(self):
        """Negative: refresh failure due to unsupported capability is explicit."""
        event = {
            "action_id": "refresh_runtime",
            "requested_utc": "2026-03-14T10:00:00Z",
            "invocation": {
                "ok": True,
                "response": {
                    "ok": False,
                    "reason": "refresh_endpoint_not_found",
                    "detail": "404 endpoint missing",
                }
            }
        }
        queue_status = {"state": {"status": "idle"}}
        operator_snapshot = {"utc": "2026-03-14T10:00:01Z"}

        confirmation = web_ops._confirmation_projection(
            action_id="refresh_runtime",
            event=event,
            queue_status=queue_status,
            operator_snapshot=operator_snapshot,
        )

        # G6N7: Fail-closed semantics for unsupported capability
        self.assertEqual(confirmation.get("state"), "failed")
        summary = confirmation.get("summary", "")
        self.assertIn("not supported", summary.lower())
        self.assertEqual(confirmation.get("durable_signal"), "refresh_capability_unavailable")


class TestG6N7InvocationCapture(unittest.TestCase):
    """Test that invocation events capture full refresh response for drift detection."""

    def test_invocation_captures_refresh_response_fields(self):
        """Positive: invocation captures ok/reason/detail for drift detection."""
        with patch("infra.webui.server_lab_assistant_web_operations._agents.refresh_live_runtime") as mock_agents, \
             patch("infra.webui.server_lab_assistant_web_operations._agent_orch.current_runtime_defaults") as mock_orch, \
             patch("infra.webui.server_lab_assistant_web_operations._append_jsonl") as mock_append:

            mock_agents.return_value = {"ok": True}
            mock_orch.return_value = {}

            # Capture the invocation dict written to JSONL
            captured_event = None
            def capture_append(path, obj):
                nonlocal captured_event
                captured_event = obj
            mock_append.side_effect = capture_append

            web_ops.invoke_runtime_control(Path("/tmp"), action_id="refresh_runtime")

            self.assertIsNotNone(captured_event)
            invocation = captured_event.get("invocation", {})
            # G6N7: Response fields captured for drift detection
            self.assertIn("response", invocation)
            response = invocation.get("response", {})
            self.assertIn("ok", response)

    def test_invocation_captures_failure_reason(self):
        """Negative: invocation captures failure reason for diagnostics."""
        with patch("infra.webui.server_lab_assistant_web_operations._refresh_capability_check") as mock_check, \
             patch("infra.webui.server_lab_assistant_web_operations._agents.refresh_live_runtime") as mock_agents, \
             patch("infra.webui.server_lab_assistant_web_operations._append_jsonl") as mock_append:

            mock_check.return_value = {
                "supported": False,
                "reason": "refresh_endpoint_not_found",
                "detail": "404",
            }
            mock_agents.return_value = {"ok": True}

            captured_event = None
            def capture_append(path, obj):
                nonlocal captured_event
                captured_event = obj
            mock_append.side_effect = capture_append

            web_ops.invoke_runtime_control(Path("/tmp"), action_id="refresh_runtime")

            self.assertIsNotNone(captured_event)
            invocation = captured_event.get("invocation", {})
            response = invocation.get("response", {})
            self.assertFalse(response.get("ok"))
            self.assertEqual(response.get("reason"), "refresh_endpoint_not_found")


class TestG6N7ScopeEnforcement(unittest.TestCase):
    """Test that scope boundaries are enforced (in-flight work not affected)."""

    def test_scope_message_explicitly_separates_future_vs_inflght(self):
        """Positive: scope explicitly states future work vs in-flight separation."""
        with patch("infra.webui.server_lab_assistant_web_operations._agents.refresh_live_runtime") as mock_agents, \
             patch("infra.webui.server_lab_assistant_web_operations._agent_orch.current_runtime_defaults") as mock_orch, \
             patch("infra.webui.server_lab_assistant_web_operations._refresh_capability_check") as mock_check:

            mock_agents.return_value = {"ok": True}
            mock_orch.return_value = {}
            mock_check.return_value = {"supported": True}

            payload = web_ops._runtime_refresh_payload()
            scope = payload.get("scope", {})

            # G6N7: Explicit scope boundaries
            self.assertEqual(scope.get("applies_to"), "future_work_started_after_refresh")
            self.assertEqual(scope.get("does_not_apply_to"), "in_flight_turns_or_jobs")

            # Message must mention both
            message = payload.get("message", "")
            self.assertIn("future", message.lower())
            self.assertIn("in-flight", message.lower() or "in_flight" in message.lower())


if __name__ == "__main__":
    unittest.main()
