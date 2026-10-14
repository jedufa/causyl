package com.eiel.chat

import com.eiel.chat.data.AdminApi
import kotlinx.coroutines.runBlocking
import okhttp3.OkHttpClient
import okhttp3.mockwebserver.MockResponse
import okhttp3.mockwebserver.MockWebServer
import org.junit.Assert.assertEquals
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Test

class AdminApiTest {

  @Test
  fun ledgerItemsUsesDynamicBaseUrlProvider() = runBlocking {
    MockWebServer().use { serverA ->
      MockWebServer().use { serverB ->
        serverA.enqueue(
          MockResponse().setResponseCode(200).setBody("""{"items":[{"uid":"PIS-A","title":"Alpha","status":"draft","release_state":"draft","updated_utc":"2026-03-08T00:00:00Z"}]}"""),
        )
        serverB.enqueue(
          MockResponse().setResponseCode(200).setBody("""{"items":[{"uid":"PIS-B","title":"Beta","status":"ready","release_state":"draft","updated_utc":"2026-03-09T00:00:00Z"}]}"""),
        )

        var currentBaseUrl = serverA.url("/").toString().trimEnd('/')
        val api = AdminApi(
          client = OkHttpClient(),
          serverBaseUrl = currentBaseUrl,
          serverBaseUrlProvider = { currentBaseUrl },
          authToken = "",
        )

        val first = api.ledgerItems(limit = 5)
        currentBaseUrl = serverB.url("/").toString().trimEnd('/')
        val second = api.ledgerItems(limit = 5)

        assertEquals("PIS-A", first.single().uid)
        assertEquals("PIS-B", second.single().uid)
      }
    }
  }

  @Test
  fun ledgerWorkflowStatusSendsAdminTokenHeaderWhenProvided() = runBlocking {
    MockWebServer().use { server ->
      server.enqueue(
        MockResponse()
          .setResponseCode(200)
          .setBody("""{"next_status":"ready_for_assure"}"""),
      )

      val api = AdminApi(
        client = OkHttpClient(),
        serverBaseUrl = server.url("/").toString().trimEnd('/'),
        authToken = "",
      )

      val status = api.ledgerWorkflowStatus(
        uid = "PIS-20260309-123000Z-A1B2",
        adminToken = "secret-token",
      )

      val request = server.takeRequest()
      assertEquals("/api/ledger/workflow/status?uid=PIS-20260309-123000Z-A1B2", request.path)
      assertEquals("secret-token", request.getHeader("X-Admin-Token"))
      assertNull(request.getHeader("Authorization"))
      assertEquals("ready_for_assure", status.nextStatus)
    }
  }

  @Test
  fun scheduledJobsSendsAdminTokenHeaderWhenProvided() = runBlocking {
    MockWebServer().use { server ->
      server.enqueue(
        MockResponse()
          .setResponseCode(200)
          .setBody(
            """
            {
              "utc":"2026-03-30T20:00:00Z",
              "jobs":[],
              "scheduler":{"running":true,"interval_seconds":30,"started_utc":"","last_tick_utc":"","last_error":"","repo_root":"/repo"},
              "watchdog":{"running":true,"interval_seconds":60,"started_utc":"","last_receipt_utc":"","last_error":"","repo_root":"/repo","latest_receipt":{}},
              "healer":{"running":false,"interval_seconds":120,"started_utc":"","last_receipt_utc":"","last_error":"","repo_root":"/repo","latest_receipt":{}}
            }
            """.trimIndent(),
          ),
      )

      val api = AdminApi(
        client = OkHttpClient(),
        serverBaseUrl = server.url("/").toString().trimEnd('/'),
        authToken = "",
      )

      api.scheduledJobs(adminToken = "secret-token")

      val request = server.takeRequest()
      assertEquals("/api/schedules/jobs", request.path)
      assertEquals("secret-token", request.getHeader("X-Admin-Token"))
      assertNull(request.getHeader("Authorization"))
    }
  }

  @Test
  fun scheduledJobsParsesSharedSchedulerProjectionFields() = runBlocking {
    MockWebServer().use { server ->
      server.enqueue(
        MockResponse()
          .setResponseCode(200)
          .setBody(
            """
            {
              "utc":"2026-03-30T20:00:00Z",
              "jobs":[
                {
                  "job_id":"job-123",
                  "job_kind":"build_verify_loop",
                  "enabled":true,
                  "role":"builder",
                  "runtime_role":"builder",
                  "display_title":"Job 123",
                  "binding":{"type":"ledger_uid","id":"PIS-20260330-120000Z-ABCD"},
                  "depends_on":[],
                  "schedule":{"mode":"once","run_at":"2026-03-30T21:00:00Z","timezone":"UTC"},
                  "runtime":{"agent_tech":"codex","model":"gpt-5.4"},
                  "loop":{"max_iterations":1},
                  "prompt":"",
                  "prompt_preview":"",
                  "context_refs":[],
                  "created_utc":"2026-03-30T19:00:00Z",
                  "updated_utc":"2026-03-30T20:00:00Z",
                  "state":{"last_status":"ok"},
                  "due":{"eligible":true,"reason":"due","slot_key":"","due_utc":"2026-03-30T21:00:00Z"},
                  "scheduler_projection_v1":{
                    "schema_version":"scheduler_projection_row_v1",
                    "projected_status":"completed_once",
                    "projection_reason":"already_executed",
                    "canonical_state":"succeeded",
                    "due_reason":"due",
                    "due_eligible":true,
                    "next_candidate_bucket":"none",
                    "next_due_utc":"2026-03-30T21:00:00Z",
                    "durable_conflict":true,
                    "ambiguity_reasons":["completed_once_conflicts_with_due_eligibility"]
                  },
                  "scheduler_status":"completed_once",
                  "scheduler_status_reason":"already_executed",
                  "scheduler_truth_degraded":true,
                  "scheduler_status_source":"scheduler_projection_v1"
                }
              ],
              "scheduler":{"running":true,"interval_seconds":30,"started_utc":"","last_tick_utc":"","last_error":"","repo_root":"/repo"},
              "watchdog":{"running":true,"interval_seconds":60,"started_utc":"","last_receipt_utc":"","last_error":"","repo_root":"/repo","latest_receipt":{}},
              "healer":{"running":false,"interval_seconds":120,"started_utc":"","last_receipt_utc":"","last_error":"","repo_root":"/repo","latest_receipt":{}}
            }
            """.trimIndent(),
          ),
      )

      val api = AdminApi(
        client = OkHttpClient(),
        serverBaseUrl = server.url("/").toString().trimEnd('/'),
        authToken = "",
      )

      val result = api.scheduledJobs()

      val job = result.jobs.single()
      requireNotNull(job.schedulerProjectionV1)
      assertEquals("completed_once", job.schedulerProjectionV1?.projectedStatus)
      assertEquals("none", job.schedulerProjectionV1?.nextCandidateBucket)
      assertEquals(true, job.schedulerProjectionV1?.durableConflict)
      assertEquals(listOf("completed_once_conflicts_with_due_eligibility"), job.schedulerProjectionV1?.ambiguityReasons)
      assertEquals("completed_once", job.schedulerStatus)
      assertEquals("already_executed", job.schedulerStatusReason)
      assertEquals(true, job.schedulerTruthDegraded)
      assertEquals("scheduler_projection_v1", job.schedulerStatusSource)
    }
  }

  @Test
  fun runScheduledJobNowSendsAdminTokenHeaderWhenProvided() = runBlocking {
    MockWebServer().use { server ->
      server.enqueue(
        MockResponse()
          .setResponseCode(200)
          .setBody("""{"ok":true,"job_id":"job-123","run_id":"run-456","status":"queued","queued":true,"reason":"run_now_requested","requested_utc":"2026-03-30T20:00:00Z"}"""),
      )

      val api = AdminApi(
        client = OkHttpClient(),
        serverBaseUrl = server.url("/").toString().trimEnd('/'),
        authToken = "",
      )

      val result = api.runScheduledJobNow(jobId = "job-123", adminToken = "secret-token")

      val request = server.takeRequest()
      assertEquals("/api/schedules/run_now", request.path)
      assertEquals("secret-token", request.getHeader("X-Admin-Token"))
      assertNull(request.getHeader("Authorization"))
      assertEquals("queued", result.status)
      assertEquals(true, result.queued)
      assertEquals("run_now_requested", result.reason)
      assertEquals("run-456", result.runId)
    }
  }

  @Test
  fun restartServerParsesStructuredConflictBlockersWithoutThrowing() = runBlocking {
    MockWebServer().use { server ->
      server.enqueue(
        MockResponse()
          .setResponseCode(409)
          .setBody(
            """
            {
              "ok": false,
              "safe_to_restart": false,
              "message": "Restart blocked because the plane still has active work or live scheduler claims.",
              "force_requested": false,
              "blockers": [
                {
                  "kind": "scheduler_job_locks_present",
                  "count": 2,
                  "summary": "2 scheduler job lock(s) are still active."
                }
              ],
              "scheduler": {
                "active_job_lock_count": 2,
                "stale_job_lock_count": 3
              },
              "admin_commands": {
                "active_job_count": 1
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

      val result = api.restartServer(adminToken = "secret-token")

      val request = server.takeRequest()
      assertEquals("/api/admin/server/restart", request.path)
      assertEquals("secret-token", request.getHeader("X-Admin-Token"))
      assertTrue(result.blocked)
      assertEquals("blocked", result.healthStatus)
      assertEquals(1, result.blockers.size)
      assertEquals("scheduler_job_locks_present", result.blockers.first().kind)
      assertEquals(3, result.staleSchedulerJobLockCount)
      assertEquals(1, result.activeAdminCommandJobCount)
    }
  }


  @Test
  fun mobileAppUpdateManifestSendsAdminTokenHeaderWhenProvided() = runBlocking {
    MockWebServer().use { server ->
      server.enqueue(
        MockResponse()
          .setResponseCode(200)
          .setBody(
            """
            {
              "channel":"debug",
              "package_name":"com.eiel.chat",
              "version_name":"1.0",
              "version_code":1,
              "apk_name":"causyl-debug-1.0-debug.apk",
              "apk_size_bytes":123,
              "apk_sha256":"deadbeef",
              "built_at":"2026-03-30T20:00:00Z",
              "source_path":"/tmp/causyl-debug-1.0-debug.apk",
              "download_path":"/api/admin/mobile_apk/download?channel=debug"
            }
            """.trimIndent(),
          ),
      )

      val api = AdminApi(
        client = OkHttpClient(),
        serverBaseUrl = server.url("/").toString().trimEnd('/'),
        authToken = "",
      )

      val manifest = api.mobileAppUpdateManifest(channel = "debug", adminToken = "secret-token")

      val request = server.takeRequest()
      assertEquals("/api/admin/mobile_apk/manifest?channel=debug", request.path)
      assertEquals("secret-token", request.getHeader("X-Admin-Token"))
      assertNull(request.getHeader("Authorization"))
      assertEquals("deadbeef", manifest.apkSha256)
    }
  }

  @Test
  fun downloadMobileAppUpdateSendsAdminTokenHeaderWhenProvided() = runBlocking {
    MockWebServer().use { server ->
      server.enqueue(
        MockResponse()
          .setResponseCode(200)
          .setHeader("Content-Type", "application/vnd.android.package-archive")
          .setHeader("Content-Disposition", "attachment; filename=causyl-debug-1.0-debug.apk")
          .setBody("apk-bytes"),
      )

      val api = AdminApi(
        client = OkHttpClient(),
        serverBaseUrl = server.url("/").toString().trimEnd('/'),
        authToken = "",
      )

      val payload = api.downloadMobileAppUpdate(channel = "debug", adminToken = "secret-token")

      val request = server.takeRequest()
      assertEquals("/api/admin/mobile_apk/download?channel=debug", request.path)
      assertEquals("secret-token", request.getHeader("X-Admin-Token"))
      assertNull(request.getHeader("Authorization"))
      assertEquals("causyl-debug-1.0-debug.apk", payload.filename)
    }
  }

  @Test
  fun downloadParsesUtf8AndUnquotedContentDispositionFilenames() = runBlocking {
    MockWebServer().use { server ->
      server.enqueue(
        MockResponse()
          .setResponseCode(200)
          .setHeader("Content-Type", "application/zip")
          .setHeader("Content-Disposition", "attachment; filename=baseline-390.zip")
          .setBody("zip-one"),
      )
      server.enqueue(
        MockResponse()
          .setResponseCode(200)
          .setHeader("Content-Type", "application/zip")
          .setHeader("Content-Disposition", "attachment; filename*=UTF-8''plane%20snapshot.zip")
          .setBody("zip-two"),
      )

      val api = AdminApi(
        client = OkHttpClient(),
        serverBaseUrl = server.url("/").toString().trimEnd('/'),
        authToken = "",
      )

      val plain = api.downloadArchive(adminToken = "secret-token", name = "baseline-390.zip")
      val utf8 = api.downloadArchive(adminToken = "secret-token", name = "plane snapshot.zip")

      assertEquals("baseline-390.zip", plain.filename)
      assertEquals("plane snapshot.zip", utf8.filename)
    }
  }

  @Test
  fun gitStatusParsesPendingCommitJournalEntries() = runBlocking {
    MockWebServer().use { server ->
      server.enqueue(
        MockResponse()
          .setResponseCode(200)
          .setBody(
            """
            {
              "branch":"main",
              "head":"abc123",
              "describe":"baseline-329",
              "status_porcelain":" M infra/mobile/android/app/src/main/java/com/eiel/chat/ui/AdminScreen.kt\n",
              "baseline_tag_suggested":"baseline-330",
              "commit_journal":{
                "pending_count":1,
                "invalid_count":0,
                "coverage_ok":true,
                "ready":true,
                "uncovered_paths":[],
                "preview_subject":"Review commit journal on mobile",
                "entries":[
                  {
                    "entry_id":"cjn_demo",
                    "created_utc":"2026-03-11T10:37:43Z",
                    "title":"add mobile journal viewer",
                    "outcome":"applied",
                    "scope_hint":"mobile-ui",
                    "summary_bullets":["Added swipeable journal viewer card"],
                    "files":["infra/mobile/android/app/src/main/java/com/eiel/chat/ui/AdminScreen.kt"],
                    "validation":["python -m py_compile infra/webui/server_routes_and_main.py"],
                    "notes":["Android Gradle proof still host-only here"],
                    "author":{
                      "role":"generalist",
                      "source":"codex",
                      "binding":{"type":"task_id","id":"mbl"}
                    }
                  }
                ]
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

      val status = api.gitStatus(adminToken = "")

      assertEquals(1, status.commitJournalEntries.size)
      val entry = status.commitJournalEntries.single()
      assertEquals("cjn_demo", entry.entryId)
      assertEquals("generalist", entry.authorRole)
      assertEquals("mbl", entry.bindingId)
      assertTrue(entry.summaryBullets.single().contains("swipeable"))
      assertTrue(entry.files.single().contains("AdminScreen.kt"))
    }
  }

}
