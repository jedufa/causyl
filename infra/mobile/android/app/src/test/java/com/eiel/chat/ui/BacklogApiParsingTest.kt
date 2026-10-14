package com.eiel.chat.ui

import com.eiel.chat.data.AdminApi
import kotlinx.coroutines.runBlocking
import okhttp3.OkHttpClient
import okhttp3.mockwebserver.MockResponse
import okhttp3.mockwebserver.MockWebServer
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Test

class BacklogApiParsingTest {
  @Test
  fun ledgerItemsParsesNestedMetaSummariesForBacklog() = runBlocking {
    MockWebServer().use { server ->
      server.enqueue(
        MockResponse()
          .setResponseCode(200)
          .setBody(
            """
            {
              "items": [
                {
                  "uid": "PIS-20260308-123000Z-A1B2",
                  "meta": {
                    "slug": "checkpointed-autopilot-proof",
                    "title": "Checkpointed autopilot proof",
                    "status": "ready_for_implement",
                    "release_state": "draft",
                    "updated_utc": "2026-03-08T12:30:00Z"
                  }
                }
              ]
            }
            """.trimIndent(),
          ),
      )
      val api = AdminApi(
        client = OkHttpClient(),
        serverBaseUrl = server.url("/").toString().trimEnd('/'),
        authToken = "",
      )

      val item = api.ledgerItems(limit = 25).single()

      assertEquals("PIS-20260308-123000Z-A1B2", item.uid)
      assertEquals("checkpointed-autopilot-proof", item.slug)
      assertEquals("Checkpointed autopilot proof", item.title)
      assertEquals("ready_for_implement", item.status)
      assertEquals("draft", item.releaseState)
      assertEquals("2026-03-08T12:30:00Z", item.updatedUtc)
      assertTrue(isMobileBacklogCandidate(item))
    }
  }

  @Test
  fun ledgerItemsKeepsClosedPisOutOfBacklogWhenOnlyMetaCarriesState() = runBlocking {
    MockWebServer().use { server ->
      server.enqueue(
        MockResponse()
          .setResponseCode(200)
          .setBody(
            """
            {
              "items": [
                {
                  "uid": "PIS-20260308-123000Z-A1B2",
                  "meta": {
                    "title": "Released item",
                    "status": "done",
                    "release_state": "released",
                    "updated_utc": "2026-03-08T12:30:00Z"
                  }
                }
              ]
            }
            """.trimIndent(),
          ),
      )
      val api = AdminApi(
        client = OkHttpClient(),
        serverBaseUrl = server.url("/").toString().trimEnd('/'),
        authToken = "",
      )

      val item = api.ledgerItems(limit = 25).single()

      assertEquals("done", item.status)
      assertEquals("released", item.releaseState)
      assertFalse(isMobileBacklogCandidate(item))
    }
  }

  @Test
  fun ledgerItemsAcceptsTopLevelArrayPayload() = runBlocking {
    MockWebServer().use { server ->
      server.enqueue(
        MockResponse()
          .setResponseCode(200)
          .setBody(
            """
            [
              {
                "uid": "PIS-20260308-143000Z-C3D4",
                "status": "draft",
                "release_state": "draft",
                "updated_utc": "2026-03-08T14:30:00Z",
                "meta": {
                  "slug": "array-root-payload",
                  "title": "Array root payload"
                }
              }
            ]
            """.trimIndent(),
          ),
      )
      val api = AdminApi(
        client = OkHttpClient(),
        serverBaseUrl = server.url("/").toString().trimEnd('/'),
        authToken = "",
      )

      val item = api.ledgerItems(limit = 25).single()

      assertEquals("PIS-20260308-143000Z-C3D4", item.uid)
      assertEquals("array-root-payload", item.slug)
      assertEquals("Array root payload", item.title)
      assertEquals("draft", item.status)
      assertEquals("draft", item.releaseState)
      assertEquals("2026-03-08T14:30:00Z", item.updatedUtc)
      assertTrue(isMobileBacklogCandidate(item))
    }
  }

  @Test
  fun ledgerItemsRejectsUnsupportedScalarPayload() = runBlocking {
    MockWebServer().use { server ->
      server.enqueue(
        MockResponse()
          .setResponseCode(200)
          .setBody("true"),
      )
      val api = AdminApi(
        client = OkHttpClient(),
        serverBaseUrl = server.url("/").toString().trimEnd('/'),
        authToken = "",
      )

      val error = try {
        api.ledgerItems(limit = 25)
        null
      } catch (thrown: IllegalStateException) {
        thrown
      }

      assertEquals("ledger_items failed: unsupported payload", error?.message)
    }
  }

  @Test
  fun mobileCockpitParsesCampaignShellAndChroniclePayload() = runBlocking {
    MockWebServer().use { server ->
      server.enqueue(
        MockResponse()
          .setResponseCode(200)
          .setBody(
            """
            {
              "schema_version": "mobile_cockpit_v1",
              "utc": "2026-04-02T09:00:00Z",
              "contracts": {
                "schema_version": "mobile_cockpit_contract_v1",
                "contract_check": {"ok": true},
                "entity_contracts": {
                  "pis": {"owner": "ledger"},
                  "campaign": {"owner": "mobile_cockpit"}
                },
                "shared_status_language": ["draft", "running"],
                "shared_action_language": ["launch_campaign", "refresh_projection"]
              },
              "selection": {
                "selected_uids": ["PIS-20260402-AAAA"],
                "primary_uid": "PIS-20260402-AAAA",
                "selected_count": 1
              },
              "pis": {
                "items": [
                  {
                    "uid": "PIS-20260402-AAAA",
                    "title": "Android cockpit",
                    "status": "draft",
                    "release_state": "draft",
                    "updated_utc": "2026-04-02T09:00:00Z"
                  }
                ],
                "counts": {"draft": 1, "ready": 0, "total": 1}
              },
              "campaigns": {
                "latest": {
                  "campaign_id": "mcamp-1",
                  "launch_outcome": "started",
                  "queue_run_id": "eq-1",
                  "workflow_id": "wf-1"
                }
              },
              "execution_queue": {
                "state": {"status": "running", "current_uid": "PIS-20260402-AAAA", "run_id": "eq-1"},
                "batch_report": {"total_count": 1, "run_id": "eq-1"},
                "worker_alive": true
              },
              "artifacts": {
                "status": "ok",
                "latest_handoff": {"name": "handoff.md", "preview_text": "preview text", "available": true}
              },
              "continuation": {
                "status": "ready",
                "summary": "Checkpoint exists but terminal completion is not proven.",
                "handoff_status": "ready",
                "authoritative_source": "continuation_packet",
                "packet_status": "ready",
                "packet_id": "pkt-1",
                "packet_revision": 2,
                "memory_status": "degraded",
                "memory_counts": {"active": 1, "expired": 0, "withdrawn": 1, "contradicted": 0},
                "failed_paths": ["Do not treat this partial checkpoint as finished work."],
                "open_questions": ["What remaining work should resume from this checkpoint?"],
                "next_steps": ["Resume from the durable checkpoint and verify completion separately."],
                "visible_caveats": ["no_valid_continuation_packet_for_resumed_or_successor_work"],
                "memory_records": [
                  {"summary": "Resume from durable checkpoint", "status": "active", "reason": ""}
                ]
              },
              "run_triage": {
                "status": "blocked",
                "summary": "Verifier waiting on operator",
                "failure_case": {"failure_class": "verifier_gap", "actions": ["inspect_workflow"]},
                "workflow": {"workflow_id": "wf-1"}
              },
              "economics": {
                "status": "degraded",
                "token_usage": {"total_tokens": 0},
                "waste_warning": "token_usage_unavailable"
              },
              "archive_snapshots": {
                "status": "ok",
                "latest_repo_snapshot": {"snapshot_id": "repo.zip"},
                "run_archives": [{"snapshot_id": "run-1.zip"}]
              },
              "chronicle": {
                "status": "ok",
                "entries": [
                  {"entry_id": "chron-1", "kind": "campaign", "summary": "Campaign started", "utc": "2026-04-02T09:00:00Z"}
                ]
              },
              "shell": {
                "status": "low_trust",
                "warnings": ["economics:degraded"],
                "fallback_actions": ["refresh_projection", "export_handoff"],
                "executor_identity": {"campaign_id": "mcamp-1", "workflow_id": "wf-1", "queue_run_id": "eq-1"}
              }
            }
            """.trimIndent(),
          ),
      )
      val api = AdminApi(
        client = OkHttpClient(),
        serverBaseUrl = server.url("/").toString().trimEnd('/'),
        authToken = "",
      )

      val payload = api.mobileCockpit(limit = 25, selectedUids = listOf("PIS-20260402-AAAA"))

      assertEquals("mobile_cockpit_v1", payload.schemaVersion)
      assertTrue(payload.contracts.contractOk)
      assertEquals("mcamp-1", payload.campaigns.latest?.campaignId)
      assertEquals("running", payload.executionQueue.status)
      assertEquals("handoff.md", payload.artifacts.latestHandoff?.name)
      assertEquals("ready", payload.continuation.status)
      assertEquals("continuation_packet", payload.continuation.authoritativeSource)
      assertEquals("What remaining work should resume from this checkpoint?", payload.continuation.openQuestions.single())
      assertEquals("degraded", payload.economics.status)
      assertEquals("repo.zip", payload.archiveSnapshots.latestRepoSnapshotId)
      assertEquals("low_trust", payload.shell.status)
      assertEquals("campaign", payload.chronicle.entries.single().kind)
      assertTrue(payload.rawCampaigns.contains("\"campaign_id\": \"mcamp-1\""))
      assertTrue(payload.rawContinuation.contains("\"packet_id\": \"pkt-1\""))
      assertTrue(payload.rawShell.contains("\"status\": \"low_trust\""))
      assertTrue(payload.rawChronicle.contains("\"entry_id\": \"chron-1\""))
    }
  }
}
