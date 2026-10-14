package com.eiel.chat.ui

import com.eiel.chat.data.ScheduledJobBinding
import com.eiel.chat.data.ScheduledJobDue
import com.eiel.chat.data.ScheduledJobLoop
import com.eiel.chat.data.ScheduledJobRuntime
import com.eiel.chat.data.ScheduledJobSchedule
import com.eiel.chat.data.ScheduledJobState
import com.eiel.chat.data.ScheduledJobSummary
import org.junit.Assert.assertEquals
import org.junit.Assert.assertTrue
import org.junit.Test

class ScheduledJobsScreenTest {

  @Test
  fun projectScheduledJobMarksRunningJobsActiveWithElapsedTime() {
    val projection = projectScheduledJob(
      job = scheduledJob(
        jobId = "job_running",
        enabled = true,
        due = ScheduledJobDue(eligible = false, reason = "active_run", slotKey = "", dueUtc = "2026-03-13T08:00:00Z"),
        state = ScheduledJobState(
          activeRunId = "sj_active",
          activeStartedUtc = "2026-03-13T08:01:00Z",
          lastRunId = "",
          lastStartedUtc = "",
          lastFinishedUtc = "",
          lastStatus = "running",
          lastTrigger = "scheduler",
          lastReceiptPath = "",
          lastReportPath = "",
          lastSlotKey = "",
          updatedUtc = "2026-03-13T08:02:00Z",
        ),
      ),
      nowUtc = "2026-03-13T08:04:30Z",
    )

    assertEquals(ScheduledJobSurfaceStatus.RUNNING, projection.status)
    assertTrue(projection.reason.contains("elapsed"))
    assertEquals("3m 30s", projection.elapsed)
  }

  @Test
  fun projectScheduledJobMarksDependencyWaitSeparatelyFromDisabled() {
    val projection = projectScheduledJob(
      job = scheduledJob(
        jobId = "job_waiting",
        enabled = true,
        due = ScheduledJobDue(eligible = false, reason = "dependency_not_ok:overnight_5437", slotKey = "", dueUtc = ""),
        state = emptyState(lastStatus = ""),
      ),
      nowUtc = "2026-03-13T08:04:30Z",
    )

    assertEquals(ScheduledJobSurfaceStatus.WAITING, projection.status)
    assertEquals("Waiting on overnight_5437", projection.reason)
  }

  @Test
  fun projectScheduledJobMarksStaleHeartbeatAsDegraded() {
    val projection = projectScheduledJob(
      job = scheduledJob(
        jobId = "job_stale_active",
        enabled = true,
        due = ScheduledJobDue(eligible = false, reason = "active_run", slotKey = "", dueUtc = ""),
        state = ScheduledJobState(
          activeRunId = "sj_stale",
          activeStartedUtc = "2026-03-13T08:01:00Z",
          lastRunId = "",
          lastStartedUtc = "",
          lastFinishedUtc = "",
          lastStatus = "running",
          lastTrigger = "scheduler",
          lastReceiptPath = "",
          lastReportPath = "",
          lastSlotKey = "",
          updatedUtc = "2026-03-13T08:01:30Z",
          activeClaimPresent = true,
          activeClaimStale = true,
          activeClaimReason = "stale_heartbeat",
          activeClaimHeartbeatUtc = "2026-03-13T08:01:45Z",
          activeClaimAgeSec = 165.0,
        ),
      ),
      nowUtc = "2026-03-13T08:04:30Z",
    )

    assertEquals(ScheduledJobSurfaceStatus.DEGRADED, projection.status)
    assertTrue(projection.reason.contains("stale", ignoreCase = true))
    assertTrue(projection.heartbeat.contains("Stale"))
  }

  @Test
  fun projectScheduledJobCarriesBoundEvidencePathsForDetailDrillDown() {
    val projection = projectScheduledJob(
      job = scheduledJob(
        jobId = "job_evidence",
        enabled = true,
        due = ScheduledJobDue(eligible = false, reason = "already_executed", slotKey = "", dueUtc = ""),
        state = emptyState(
          lastStatus = "ok",
          lastStartedUtc = "2026-03-13T08:00:00Z",
          lastFinishedUtc = "2026-03-13T08:01:00Z",
          lastReceiptPath = "governance/agents/runtime/scheduled_jobs/receipts/job_evidence/sj_abc123.json",
          lastReportPath = "governance/agents/runtime/scheduled_jobs/reports/job_evidence/sj_abc123.md",
        ),
        bindingId = "PIS-20260313-060103Z-S5T9",
        contextRef = "governance/ledger/items/PIS-20260313-060103Z-S5T9__mobile-scheduled-job-and-execution-monitoring-surface-contract/pis.md",
      ),
      nowUtc = "2026-03-13T08:04:30Z",
    )

    assertEquals("PIS-20260313-060103Z-S5T9", projection.boundPisUid)
    assertEquals(
      "governance/ledger/items/PIS-20260313-060103Z-S5T9__mobile-scheduled-job-and-execution-monitoring-surface-contract/pis.md",
      projection.boundPisPath,
    )
    assertEquals("governance/agents/runtime/scheduled_jobs/receipts/job_evidence/sj_abc123.json", projection.receiptPath)
    assertEquals("governance/agents/runtime/scheduled_jobs/reports/job_evidence/sj_abc123.md", projection.reportPath)
  }

  @Test
  fun normalizeScheduledJobFileReferenceConvertsCurrentRepoAbsolutePaths() {
    val normalized = normalizeScheduledJobFileReference(
      "/home/user/causyl/governance/agents/runtime/scheduled_jobs/reports/job_evidence/sj_abc123.md",
    )

    assertEquals(
      "governance/agents/runtime/scheduled_jobs/reports/job_evidence/sj_abc123.md",
      normalized,
    )
  }

  @Test
  fun boundPisPathFallsBackToPisContextRefWhenBindingUidDoesNotMatch() {
    val job = scheduledJob(
      jobId = "job_fallback",
      enabled = true,
      due = ScheduledJobDue(eligible = false, reason = "already_executed", slotKey = "", dueUtc = ""),
      state = emptyState(lastStatus = "ok"),
      bindingType = "other",
      bindingId = "not-in-context",
      contextRefs = listOf(
        "governance/ledger/items/PIS-20260313-FALLB01Z-FALLB__fallback-job/pis.md",
        "governance/ledger/items/PIS-20260313-FALLB02Z-FALLB2__fallback-other/pis.md",
      ),
    )

    assertEquals("", boundPisUid(job))
    assertEquals("governance/ledger/items/PIS-20260313-FALLB01Z-FALLB__fallback-job/pis.md", boundPisPath(job))
  }

  @Test
  fun boundPisPathFallsBackToLedgerItemDirectoryWhenPisRefMissing() {
    val job = scheduledJob(
      jobId = "job_ledger_dir",
      enabled = true,
      due = ScheduledJobDue(eligible = false, reason = "already_executed", slotKey = "", dueUtc = ""),
      state = emptyState(lastStatus = "ok"),
      bindingType = "other",
      bindingId = "not-in-context",
      contextRefs = listOf(
        "governance/ledger/items/PIS-20260313-123456Z-ABCD__no-explicit-pis/artifacts/run.log",
      ),
    )

    assertEquals(
      "governance/ledger/items/PIS-20260313-123456Z-ABCD__no-explicit-pis/pis.md",
      boundPisPath(job),
    )
  }

  @Test
  fun boundPisPathPrefersBindingLedgerUidFromLongUidIds() {
    val job = scheduledJob(
      jobId = "job_long_uid",
      enabled = true,
      due = ScheduledJobDue(eligible = false, reason = "already_executed", slotKey = "", dueUtc = ""),
      state = emptyState(lastStatus = "ok"),
      bindingType = "ledger_uid",
      bindingId = "PIS-20260313-231200Z-R7Q4",
      contextRefs = listOf(
        "governance/ledger/items/PIS-20260312-123456Z-ALFA__other/pis.md",
        "governance/ledger/items/PIS-20260313-231200Z-R7Q4__target/pis.md",
      ),
    )

    assertEquals("PIS-20260313-231200Z-R7Q4", boundPisUid(job))
    assertEquals("governance/ledger/items/PIS-20260313-231200Z-R7Q4__target/pis.md", boundPisPath(job))
  }

  @Test
  fun sortScheduledJobsForMobilePrioritizesActiveThenFailedThenSucceeded() {
    val ordered = sortScheduledJobsForMobile(
      listOf(
        scheduledJob(
          jobId = "job_ok",
          enabled = true,
          due = ScheduledJobDue(eligible = false, reason = "already_executed", slotKey = "", dueUtc = ""),
          state = emptyState(
            lastStatus = "ok",
            lastStartedUtc = "2026-03-13T07:00:00Z",
            lastFinishedUtc = "2026-03-13T07:02:00Z",
          ),
        ),
        scheduledJob(
          jobId = "job_pending",
          enabled = true,
          due = ScheduledJobDue(eligible = true, reason = "due", slotKey = "", dueUtc = "2026-03-13T08:05:00Z"),
          state = emptyState(lastStatus = ""),
        ),
        scheduledJob(
          jobId = "job_failed",
          enabled = true,
          due = ScheduledJobDue(eligible = false, reason = "already_executed", slotKey = "", dueUtc = ""),
          state = emptyState(
            lastStatus = "error",
            lastStartedUtc = "2026-03-13T08:00:00Z",
            lastFinishedUtc = "2026-03-13T08:01:00Z",
          ),
        ),
        scheduledJob(
          jobId = "job_running",
          enabled = true,
          due = ScheduledJobDue(eligible = false, reason = "active_run", slotKey = "", dueUtc = ""),
          state = ScheduledJobState(
            activeRunId = "sj_active",
            activeStartedUtc = "2026-03-13T08:03:00Z",
            lastRunId = "",
            lastStartedUtc = "",
            lastFinishedUtc = "",
            lastStatus = "running",
            lastTrigger = "scheduler",
            lastReceiptPath = "",
            lastReportPath = "",
            lastSlotKey = "",
            updatedUtc = "2026-03-13T08:03:10Z",
          ),
        ),
      ),
      nowUtc = "2026-03-13T08:04:30Z",
    )

    assertEquals(listOf("job_running", "job_pending", "job_failed", "job_ok"), ordered.map { it.jobId })
  }

  @Test
  fun displayJobTitleUsesHumanReadablePisTitleWhenAvailable() {
    val title = displayJobTitle(
      scheduledJob(
        jobId = "job_running",
        enabled = true,
        due = ScheduledJobDue(eligible = false, reason = "active_run", slotKey = "", dueUtc = "2026-03-13T08:00:00Z"),
        state = emptyState(lastStatus = "running"),
      ),
    )

    assertEquals("Mobile Scheduled Job Monitor And Job Detail Surface", title)
  }

  @Test
  fun buildScheduledJobBranchSectionsKeepsActiveBranchAheadOfLaterBranches() {
    val rootA = scheduledJob(
      jobId = "root_a",
      enabled = true,
      due = ScheduledJobDue(eligible = false, reason = "already_executed", slotKey = "", dueUtc = ""),
      state = emptyState(lastStatus = "ok"),
      bindingId = "PIS-20260312-234415Z-M9P4",
      contextRef = "governance/ledger/items/PIS-20260312-234415Z-M9P4__mobile-scheduled-job-monitor-and-job-detail-surface/pis.md",
    )
    val branchAActive = scheduledJob(
      jobId = "branch_a_active",
      enabled = true,
      due = ScheduledJobDue(eligible = false, reason = "active_run", slotKey = "", dueUtc = ""),
      state = ScheduledJobState(
        activeRunId = "sj_active",
        activeStartedUtc = "2026-03-13T08:03:00Z",
        lastRunId = "",
        lastStartedUtc = "",
        lastFinishedUtc = "",
        lastStatus = "running",
        lastTrigger = "scheduler",
        lastReceiptPath = "",
        lastReportPath = "",
        lastSlotKey = "",
        updatedUtc = "2026-03-13T08:03:10Z",
      ),
      dependsOn = listOf("root_a"),
      bindingId = "PIS-20260313-AAA111Z-AAAA",
      contextRef = "governance/ledger/items/PIS-20260313-AAA111Z-AAAA__active-branch-worker/pis.md",
    )
    val branchANext = scheduledJob(
      jobId = "branch_a_next",
      enabled = true,
      due = ScheduledJobDue(eligible = true, reason = "due", slotKey = "", dueUtc = "2026-03-13T08:05:00Z"),
      state = emptyState(lastStatus = ""),
      dependsOn = listOf("branch_a_active"),
      bindingId = "PIS-20260313-AAB111Z-AAAB",
      contextRef = "governance/ledger/items/PIS-20260313-AAB111Z-AAAB__active-branch-next-step/pis.md",
    )
    val rootB = scheduledJob(
      jobId = "root_b",
      enabled = true,
      due = ScheduledJobDue(eligible = true, reason = "due", slotKey = "", dueUtc = "2026-03-13T08:05:00Z"),
      state = emptyState(lastStatus = ""),
      bindingId = "PIS-20260313-BBB111Z-BBBB",
      contextRef = "governance/ledger/items/PIS-20260313-BBB111Z-BBBB__next-branch-root/pis.md",
    )
    val branchBNext = scheduledJob(
      jobId = "branch_b_next",
      enabled = true,
      due = ScheduledJobDue(eligible = true, reason = "due", slotKey = "", dueUtc = "2026-03-13T08:06:00Z"),
      state = emptyState(lastStatus = ""),
      dependsOn = listOf("root_b"),
      bindingId = "PIS-20260313-CCC111Z-CCCC",
      contextRef = "governance/ledger/items/PIS-20260313-CCC111Z-CCCC__next-branch-step/pis.md",
    )

    val sections = buildScheduledJobBranchSections(
      displayJobs = listOf(branchBNext, rootB, branchANext, branchAActive, rootA),
      graphJobs = listOf(branchBNext, rootB, branchANext, branchAActive, rootA),
      nowUtc = "2026-03-13T08:04:30Z",
    )

    assertEquals(listOf("Mobile Scheduled Job Monitor And Job Detail Surface", "Next Branch Root"), sections.map { it.title })
    assertEquals(listOf("branch_a_active", "branch_a_next", "root_a"), sections.first().jobs.map { it.jobId })
    assertEquals(listOf("root_b", "branch_b_next"), sections.last().jobs.map { it.jobId })
    assertEquals(0, sections.first().indentByJobId["root_a"])
    assertEquals(0, sections.first().indentByJobId["branch_a_active"])
    assertEquals(1, sections.first().indentByJobId["branch_a_next"])
    assertEquals(0, sections.last().indentByJobId["root_b"])
    assertEquals(0, sections.last().indentByJobId["branch_b_next"])
  }

  @Test
  fun buildScheduledJobBranchSectionsShowsParallelActiveTreesSeparately() {
    val root = scheduledJob(
      jobId = "root",
      enabled = true,
      due = ScheduledJobDue(eligible = false, reason = "already_executed", slotKey = "", dueUtc = ""),
      state = emptyState(lastStatus = "ok"),
      bindingId = "PIS-20260313-ROOT11Z-ROOT",
      contextRef = "governance/ledger/items/PIS-20260313-ROOT11Z-ROOT__parallel-root/pis.md",
    )
    val activeA = scheduledJob(
      jobId = "active_a",
      enabled = true,
      due = ScheduledJobDue(eligible = false, reason = "active_run", slotKey = "", dueUtc = ""),
      state = ScheduledJobState(
        activeRunId = "run_a",
        activeStartedUtc = "2026-03-13T08:03:00Z",
        lastRunId = "",
        lastStartedUtc = "",
        lastFinishedUtc = "",
        lastStatus = "running",
        lastTrigger = "scheduler",
        lastReceiptPath = "",
        lastReportPath = "",
        lastSlotKey = "",
        updatedUtc = "2026-03-13T08:03:10Z",
      ),
      dependsOn = listOf("root"),
      bindingId = "PIS-20260313-ACTA11Z-ACTA",
      contextRef = "governance/ledger/items/PIS-20260313-ACTA11Z-ACTA__parallel-active-a/pis.md",
    )
    val nextA = scheduledJob(
      jobId = "next_a",
      enabled = true,
      due = ScheduledJobDue(eligible = true, reason = "due", slotKey = "", dueUtc = "2026-03-13T08:05:00Z"),
      state = emptyState(lastStatus = ""),
      dependsOn = listOf("active_a"),
      bindingId = "PIS-20260313-NXTA11Z-NXTA",
      contextRef = "governance/ledger/items/PIS-20260313-NXTA11Z-NXTA__parallel-next-a/pis.md",
    )
    val activeB = scheduledJob(
      jobId = "active_b",
      enabled = true,
      due = ScheduledJobDue(eligible = false, reason = "active_run", slotKey = "", dueUtc = ""),
      state = ScheduledJobState(
        activeRunId = "run_b",
        activeStartedUtc = "2026-03-13T08:02:00Z",
        lastRunId = "",
        lastStartedUtc = "",
        lastFinishedUtc = "",
        lastStatus = "running",
        lastTrigger = "scheduler",
        lastReceiptPath = "",
        lastReportPath = "",
        lastSlotKey = "",
        updatedUtc = "2026-03-13T08:02:10Z",
      ),
      dependsOn = listOf("root"),
      bindingId = "PIS-20260313-ACTB11Z-ACTB",
      contextRef = "governance/ledger/items/PIS-20260313-ACTB11Z-ACTB__parallel-active-b/pis.md",
    )
    val nextB = scheduledJob(
      jobId = "next_b",
      enabled = true,
      due = ScheduledJobDue(eligible = true, reason = "due", slotKey = "", dueUtc = "2026-03-13T08:06:00Z"),
      state = emptyState(lastStatus = ""),
      dependsOn = listOf("active_b"),
      bindingId = "PIS-20260313-NXTB11Z-NXTB",
      contextRef = "governance/ledger/items/PIS-20260313-NXTB11Z-NXTB__parallel-next-b/pis.md",
    )

    val section = buildScheduledJobBranchSections(
      displayJobs = listOf(nextB, activeB, nextA, activeA, root),
      graphJobs = listOf(nextB, activeB, nextA, activeA, root),
      nowUtc = "2026-03-13T08:04:30Z",
    ).single()

    assertEquals(listOf("active_a", "next_a", "active_b", "next_b", "root"), section.jobs.map { it.jobId })
    assertEquals(0, section.indentByJobId["active_a"])
    assertEquals(1, section.indentByJobId["next_a"])
    assertEquals(0, section.indentByJobId["active_b"])
    assertEquals(1, section.indentByJobId["next_b"])
    assertEquals(0, section.indentByJobId["root"])
  }

  @Test
  fun buildVerificationWorkflowRunCardsExposeBlockedAndDegradedPhasesExplicitly() {
    val root = scheduledJob(
      jobId = "root",
      enabled = true,
      due = ScheduledJobDue(eligible = false, reason = "already_executed", slotKey = "", dueUtc = ""),
      state = emptyState(lastStatus = "ok"),
      bindingId = "PIS-20260414-043439Z-L2N5",
      contextRef = "governance/ledger/patch_instruction_sheets/causyl/active/2026/04/PIS-20260414-043439Z-L2N5__build-verification-workflow-run-card-and-phase-status-surface/pis.md",
      displayTitle = "Verification workflow root",
    )
    val degraded = scheduledJob(
      jobId = "phase_degraded",
      enabled = true,
      due = ScheduledJobDue(eligible = false, reason = "active_run", slotKey = "", dueUtc = ""),
      state = ScheduledJobState(
        activeRunId = "sj_degraded",
        activeStartedUtc = "2026-03-13T08:01:00Z",
        lastRunId = "",
        lastStartedUtc = "",
        lastFinishedUtc = "",
        lastStatus = "running",
        lastTrigger = "scheduler",
        lastReceiptPath = "",
        lastReportPath = "",
        lastSlotKey = "",
        updatedUtc = "2026-03-13T08:01:30Z",
        activeClaimPresent = true,
        activeClaimStale = true,
        activeClaimReason = "stale_heartbeat",
        activeClaimHeartbeatUtc = "2026-03-13T08:01:45Z",
        activeClaimAgeSec = 165.0,
      ),
      dependsOn = listOf("root"),
      bindingId = "PIS-20260414-043440Z-M3P6",
      contextRef = "governance/ledger/patch_instruction_sheets/causyl/active/2026/04/PIS-20260414-043440Z-M3P6__workflow-phase-degraded/pis.md",
      displayTitle = "Verifier execution",
    )
    val blocked = scheduledJob(
      jobId = "phase_blocked",
      enabled = false,
      due = ScheduledJobDue(eligible = false, reason = "dependency_not_ok:verifier_execution", slotKey = "", dueUtc = ""),
      state = emptyState(lastStatus = ""),
      dependsOn = listOf("phase_degraded"),
      bindingId = "PIS-20260414-043441Z-N4Q7",
      contextRef = "governance/ledger/patch_instruction_sheets/causyl/active/2026/04/PIS-20260414-043441Z-N4Q7__workflow-phase-blocked/pis.md",
      displayTitle = "Report closeout",
    )

    val cards = buildVerificationWorkflowRunCards(
      jobs = listOf(blocked, degraded, root),
      nowUtc = "2026-03-13T08:04:30Z",
    )

    assertEquals(1, cards.size)
    val card = cards.single()
    assertEquals(ScheduledJobSurfaceStatus.DEGRADED, card.overallStatus)
    assertTrue(card.summary.contains("1 degraded"))
    assertTrue(card.summary.contains("1 blocked"))
    assertEquals(
      listOf("phase_degraded", "phase_blocked", "root"),
      card.phases.map { it.jobId },
    )
    assertEquals(ScheduledJobSurfaceStatus.DEGRADED, card.phases[0].status)
    assertTrue(card.phases[0].statusDetail.contains("Stale"))
    assertEquals(ScheduledJobSurfaceStatus.BLOCKED, card.phases[1].status)
    assertEquals("Disabled by spec", card.phases[1].statusDetail)
    assertEquals(ScheduledJobSurfaceStatus.SUCCEEDED, card.phases[2].status)
  }

  @Test
  fun summarizeVerificationWorkflowRunStatusPrefersBlockedOverRunningAndWaiting() {
    val status = summarizeVerificationWorkflowRunStatus(
      listOf(
        ScheduledJobSurfaceStatus.RUNNING,
        ScheduledJobSurfaceStatus.WAITING,
        ScheduledJobSurfaceStatus.BLOCKED,
      ),
    )

    assertEquals(ScheduledJobSurfaceStatus.BLOCKED, status)
  }

  @Test
  fun buildVerificationWorkflowRunSummaryListsExplicitPhaseCounts() {
    val summary = buildVerificationWorkflowRunSummary(
      listOf(
        VerificationWorkflowRunPhase(
          jobId = "degraded",
          title = "Verifier execution",
          status = ScheduledJobSurfaceStatus.DEGRADED,
          statusDetail = "Stale (2m 45s)",
        ),
        VerificationWorkflowRunPhase(
          jobId = "blocked",
          title = "Report closeout",
          status = ScheduledJobSurfaceStatus.BLOCKED,
          statusDetail = "Disabled by spec",
        ),
        VerificationWorkflowRunPhase(
          jobId = "pending",
          title = "Archive",
          status = ScheduledJobSurfaceStatus.PENDING,
          statusDetail = "Ready to run",
        ),
      ),
    )

    assertEquals("1 degraded · 1 blocked · 1 pending", summary)
  }

  @Test
  fun buildPipelineBoardCreatesThreeLanesAndDistributesJobsRoundRobin() {
    val jobs = listOf(
      scheduledJob(
        jobId = "j1",
        enabled = true,
        due = ScheduledJobDue(eligible = false, reason = "already_executed", slotKey = "", dueUtc = "2026-03-13T08:00:00Z"),
        state = emptyState(lastStatus = "ok", lastStartedUtc = "2026-03-13T06:50:00Z", lastFinishedUtc = "2026-03-13T06:55:00Z"),
      ),
      scheduledJob(
        jobId = "j2",
        enabled = true,
        due = ScheduledJobDue(eligible = false, reason = "already_executed", slotKey = "", dueUtc = "2026-03-13T08:10:00Z"),
        state = emptyState(lastStatus = "ok", lastStartedUtc = "2026-03-13T06:55:00Z", lastFinishedUtc = "2026-03-13T06:58:00Z"),
      ),
      scheduledJob(
        jobId = "j3",
        enabled = true,
        due = ScheduledJobDue(eligible = false, reason = "already_executed", slotKey = "", dueUtc = "2026-03-13T08:20:00Z"),
        state = emptyState(lastStatus = "ok", lastStartedUtc = "2026-03-13T07:00:00Z", lastFinishedUtc = "2026-03-13T07:01:00Z"),
      ),
      scheduledJob(
        jobId = "j4",
        enabled = true,
        due = ScheduledJobDue(eligible = false, reason = "already_executed", slotKey = "", dueUtc = "2026-03-13T08:30:00Z"),
        state = emptyState(lastStatus = "ok", lastStartedUtc = "2026-03-13T07:02:00Z", lastFinishedUtc = "2026-03-13T07:03:00Z"),
      ),
      scheduledJob(
        jobId = "j5",
        enabled = true,
        due = ScheduledJobDue(eligible = false, reason = "already_executed", slotKey = "", dueUtc = "2026-03-13T08:40:00Z"),
        state = emptyState(lastStatus = "ok", lastStartedUtc = "2026-03-13T07:04:00Z", lastFinishedUtc = "2026-03-13T07:05:00Z"),
      ),
      scheduledJob(
        jobId = "j6",
        enabled = true,
        due = ScheduledJobDue(eligible = false, reason = "already_executed", slotKey = "", dueUtc = "2026-03-13T08:50:00Z"),
        state = emptyState(lastStatus = "ok", lastStartedUtc = "2026-03-13T07:06:00Z", lastFinishedUtc = "2026-03-13T07:07:00Z"),
      ),
      scheduledJob(
        jobId = "j7",
        enabled = true,
        due = ScheduledJobDue(eligible = false, reason = "already_executed", slotKey = "", dueUtc = "2026-03-13T09:00:00Z"),
        state = emptyState(lastStatus = "ok", lastStartedUtc = "2026-03-13T07:08:00Z", lastFinishedUtc = "2026-03-13T07:09:00Z"),
      ),
    )
    val board = buildPipelineBoard(jobs, nowUtc = "2026-03-13T10:00:00Z")

    assertEquals(PIPELINE_LANE_COUNT, board.lanes.size)
    assertEquals(3, board.lanes[0].items.size)
    assertEquals(2, board.lanes[1].items.size)
    assertEquals(2, board.lanes[2].items.size)

    assertEquals(
      listOf("j1", "j4", "j7"),
      board.lanes[0].items.filterIsInstance<PipelineLaneItem.TaskCard>().map { it.job.jobId },
    )
  }

  @Test
  fun buildPipelineBoardInsertsWaitMarkersForScheduledJobsOnly() {
    val board = buildPipelineBoard(
      listOf(
        scheduledJob(
          jobId = "future_due",
          enabled = true,
          due = ScheduledJobDue(eligible = true, reason = "due", slotKey = "", dueUtc = "2026-03-13T09:00:00Z"),
          state = emptyState(lastStatus = ""),
        ),
        scheduledJob(
          jobId = "waiting_for_time",
          enabled = true,
          due = ScheduledJobDue(eligible = false, reason = "not_due_yet", slotKey = "", dueUtc = "2026-03-13T09:00:00Z"),
          state = emptyState(lastStatus = ""),
        ),
        scheduledJob(
          jobId = "disabled",
          enabled = false,
          due = ScheduledJobDue(eligible = true, reason = "due", slotKey = "", dueUtc = "2026-03-13T09:00:00Z"),
          state = emptyState(lastStatus = ""),
        ),
      ),
      nowUtc = "2026-03-13T08:00:00Z",
    )

    val markers = board.lanes.flatMap { lane -> lane.items.filterIsInstance<PipelineLaneItem.WaitCard>() }
    assertEquals(2, markers.size)
    assertTrue(markers.any { it.reason == "Ready to run" })
    assertTrue(markers.any { it.reason == "Waiting for scheduled time" })
  }

  @Test
  fun buildPipelineBoardCollapsesLongDependencyWaitQueues() {
    val waitingJobs = (0 until 9).map { index ->
      scheduledJob(
        jobId = "wait_$index",
        enabled = true,
        due = ScheduledJobDue(eligible = false, reason = "dependency_not_ok:nightly_bootstrap", slotKey = "", dueUtc = "2026-03-13T09:00:00Z"),
        state = emptyState(lastStatus = ""),
      )
    }
    val board = buildPipelineBoard(waitingJobs, nowUtc = "2026-03-13T08:00:00Z")

    val groupedLanes = board.lanes.count { lane -> lane.items.any { it is PipelineLaneItem.CollapsedWaitGroup } }
    assertEquals(3, groupedLanes)
    val firstCollapsed = board.lanes[0].items.firstOrNull { it is PipelineLaneItem.CollapsedWaitGroup }
    assertTrue(firstCollapsed is PipelineLaneItem.CollapsedWaitGroup)
    val group = firstCollapsed as PipelineLaneItem.CollapsedWaitGroup
    assertEquals(3, group.count)
    assertEquals(3, group.jobs.size)
    assertEquals("nightly_bootstrap", group.reason)
  }

  @Test
  fun buildPipelineBoardIsEmptyForNoJobs() {
    val board = buildPipelineBoard(emptyList(), nowUtc = "2026-03-13T08:00:00Z")
    assertEquals(PIPELINE_LANE_COUNT, board.lanes.size)
    assertEquals(listOf(0, 0, 0), board.lanes.map { it.items.size })
    assertEquals(0, pipelineBoardVisibleRows(board))
    assertEquals(0, board.totalItems)
  }

  @Test
  fun pipelineBoardVisibleRowsRespectsFiveItemViewport() {
    val manyJobs = (0 until 17).map { index ->
      scheduledJob(
        jobId = "job_$index",
        enabled = true,
        due = ScheduledJobDue(
          eligible = true,
          reason = "due",
          slotKey = "",
          dueUtc = "2026-03-13T09:${(index + 10).toString().padStart(2, '0')}:00Z",
        ),
        state = emptyState(lastStatus = ""),
      )
    }
    val board = buildPipelineBoard(manyJobs, nowUtc = "2026-03-13T08:00:00Z")

    assertEquals(3, board.lanes.size)
    assertEquals(34, board.totalItems)
    assertEquals(5, pipelineBoardVisibleRows(board))
    assertEquals(pipelineBoardHeightForItems(5), pipelineBoardHeightForItems(pipelineBoardVisibleRows(board)))
  }

  @Test
  fun pipelineBoardVisibleRowsReturnsZeroForEmptyBoard() {
    val board = buildPipelineBoard(emptyList(), nowUtc = "2026-03-13T08:00:00Z")

    assertEquals(0, pipelineBoardVisibleRows(board))
    assertEquals(0, board.totalItems)
  }

  private fun scheduledJob(
    jobId: String,
    enabled: Boolean,
    due: ScheduledJobDue,
    state: ScheduledJobState,
    dependsOn: List<String> = emptyList(),
    bindingType: String = "ledger_uid",
    bindingId: String = "PIS-20260312-234415Z-M9P4",
    contextRef: String = "governance/ledger/items/PIS-20260312-234415Z-M9P4__mobile-scheduled-job-monitor-and-job-detail-surface/pis.md",
    contextRefs: List<String>? = null,
    displayTitle: String = "",
  ): ScheduledJobSummary {
    return ScheduledJobSummary(
      jobId = jobId,
      jobKind = "single_turn",
      enabled = enabled,
      role = "generalist",
      runtimeRole = "generalist",
      displayTitle = displayTitle,
      binding = ScheduledJobBinding(type = bindingType, id = bindingId),
      dependsOn = dependsOn,
      schedule = ScheduledJobSchedule(mode = "once", runAtUtc = "2026-03-13T08:00:00Z", timeOfDay = "", timezone = "UTC"),
      runtime = ScheduledJobRuntime(agentTech = "codex", model = "gpt-5.4"),
      loop = ScheduledJobLoop(maxIterations = 1),
      prompt = "monitor scheduled job",
      promptPreview = "monitor scheduled job",
      contextRefs = contextRefs ?: listOf(contextRef),
      createdUtc = "2026-03-12T23:44:15Z",
      updatedUtc = "2026-03-13T08:00:00Z",
      state = state,
      due = due,
    )
  }

  private fun emptyState(
    lastStatus: String,
    lastStartedUtc: String = "",
    lastFinishedUtc: String = "",
    lastReceiptPath: String = "",
    lastReportPath: String = "",
  ): ScheduledJobState {
    return ScheduledJobState(
      activeRunId = "",
      activeStartedUtc = "",
      lastRunId = "",
      lastStartedUtc = lastStartedUtc,
      lastFinishedUtc = lastFinishedUtc,
      lastStatus = lastStatus,
      lastTrigger = "",
      lastReceiptPath = lastReceiptPath,
      lastReportPath = lastReportPath,
      lastSlotKey = "",
      updatedUtc = "2026-03-13T08:00:00Z",
    )
  }
}
