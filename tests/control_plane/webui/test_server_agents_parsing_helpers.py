from __future__ import annotations

from infra.webui import server_agents_parsing as parsing


def test_extract_host_verification_request_reads_json_block() -> None:
    text = 'note\nHOST_VERIFICATION_REQUEST_JSON: {"action_id":"android_unit_test","params":{"gradle_task":":app:testDebugUnitTest"}}'
    result = parsing.extract_host_verification_request(text)

    assert result["action_id"] == "android_unit_test"
    assert result["params"]["gradle_task"] == ":app:testDebugUnitTest"


def test_chat_header_helpers_strip_and_normalize() -> None:
    text = "hello\nCHAT_HEADER_TITLE: Plane Status Fix\nbody"

    assert parsing.extract_chat_header_title(text) == "Plane Status Fix"
    assert parsing.strip_chat_header_title_marker(text) == "hello\nbody"
    assert parsing.normalize_chat_header_title("  Plane   Status   Fix  ") == "Plane Status Fix"
