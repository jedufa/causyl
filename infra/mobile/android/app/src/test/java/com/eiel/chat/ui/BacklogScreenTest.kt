package com.eiel.chat.ui

import com.eiel.chat.data.OrchestrationWorkflowSummary
import com.eiel.chat.data.WorkflowConversationItem
import com.eiel.chat.data.MobileCockpitChronicle
import com.eiel.chat.data.MobileCockpitPayload
import com.eiel.chat.data.MobileCockpitRunTriage
import com.eiel.chat.data.MobileCockpitShell
import com.eiel.chat.domain.WorkflowEvent
import com.eiel.chat.domain.WorkflowStatus
import com.eiel.chat.data.LedgerItemSummary
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Test

class BacklogScreenTest {
  @Test
  fun defaultBacklogVisibilityHidesDoneOnly() {
    val draft = ledgerItem(status = "draft")
    val ready = ledgerItem(status = "ready_for_implement")
    val implemented = ledgerItem(status = "implemented")
    val blocked = ledgerItem(status = "blocked")
    val releasedDraft = ledgerItem(status = "draft", releaseState = "released")
    val done = ledgerItem(status = "done")
    val nonPis = ledgerItem(uid = "ABC-20260308-TEST", status = "draft")

    assertTrue(isMobileBacklogCandidate(draft))
    assertTrue(isMobileBacklogCandidate(ready))
    assertTrue(isMobileBacklogCandidate(implemented))
    assertTrue(isMobileBacklogCandidate(blocked))
    assertTrue(isMobileBacklogCandidate(releasedDraft))
    assertFalse(isMobileBacklogCandidate(done))
    assertFalse(isMobileBacklogCandidate(nonPis))
  }

  @Test
  fun filterMobileBacklogItemsCanHideDeprecatedWithoutDroppingOtherStatuses() {
    val draft = ledgerItem(uid = "PIS-1", status = "draft")
    val deprecated = ledgerItem(uid = "PIS-2", status = "deprecated")
    val implemented = ledgerItem(uid = "PIS-3", status = "implemented")

    val visible = filterMobileBacklogItems(
      items = listOf(draft, deprecated, implemented),
      hiddenStatuses = setOf("done", "deprecated"),
    )

    assertEquals(listOf("PIS-1", "PIS-3"), visible.map { it.uid })
  }

  @Test
  fun statusFilterOptionsIncludeCountsAndHiddenStatuses() {
    val options = buildBacklogStatusFilterOptions(
      items = listOf(
        ledgerItem(uid = "PIS-1", status = "draft"),
        ledgerItem(uid = "PIS-2", status = "deprecated"),
        ledgerItem(uid = "PIS-3", status = "deprecated"),
      ),
      hiddenStatuses = setOf("done", "deprecated"),
    )

    assertTrue(options.any { it.status == "draft" && it.count == 1 && it.visible })
    assertTrue(options.any { it.status == "deprecated" && it.count == 2 && !it.visible })
    assertTrue(options.any { it.status == "done" && it.count == 0 && !it.visible })
  }

  @Test
  fun backlogStatusFilterSummaryListsHiddenStatuses() {
    assertEquals(
      "Hidden: Deprecated, Done",
      buildBacklogStatusFilterSummary(setOf("deprecated", "done")),
    )
    assertEquals(
      "All statuses visible",
      buildBacklogStatusFilterSummary(emptySet()),
    )
  }

  @Test
  fun backlogVisibilityMessageExplainsWhenFilterHidesEverything() {
    assertEquals(
      "No PIS items match the current status filter.",
      buildBacklogVisibilityMessage(
        visibleCount = 0,
        totalCount = 3,
        hiddenStatuses = setOf("deprecated"),
      ),
    )
    assertEquals(
      "2 of 5 PIS items visible",
      buildBacklogVisibilityMessage(
        visibleCount = 2,
        totalCount = 5,
        hiddenStatuses = setOf("done"),
      ),
    )
  }

  @Test
  fun optionLabelStartsWithStatusThenUidAndDescriptor() {
    val item = ledgerItem(
      uid = "PIS-20260308-123000Z-A1B2",
      slug = "checkpointed-autopilot-proof",
      title = "Checkpointed autopilot proof",
      status = "ready_for_implement",
    )

    assertEquals(
      "Ready | PIS-20260308-123000Z-A1B2 | checkpointed-autopilot-proof / Checkpointed autopilot proof",
      formatMobileBacklogOptionLabel(item),
    )
  }

  @Test
  fun executionPaneKeepsFullQueueForScrolling() {
    val items = listOf(
      ledgerItem(uid = "PIS-1", title = "First"),
      ledgerItem(uid = "PIS-2", title = "Second"),
      ledgerItem(uid = "PIS-3", title = "Third"),
      ledgerItem(uid = "PIS-4", title = "Fourth"),
    )

    assertEquals(
      listOf("PIS-1", "PIS-2", "PIS-3", "PIS-4"),
      buildExecutionPaneItems(items).map { it.uid },
    )
  }

  @Test
  fun backlogPriorityUpdateMovesItemToFrontWithoutDroppingOthers() {
    val items = listOf(
      ledgerItem(uid = "PIS-1", title = "First"),
      ledgerItem(uid = "PIS-2", title = "Second"),
      ledgerItem(uid = "PIS-3", title = "Third"),
      ledgerItem(uid = "PIS-4", title = "Fourth"),
    )

    assertEquals(
      listOf("PIS-3", "PIS-1", "PIS-2", "PIS-4"),
      applyBacklogPriorityUpdate(
        items = items,
        uid = "PIS-3",
        updatedUtc = "2026-03-11T12:00:00Z",
        executionPriority = -1000,
      ).map { it.uid },
    )
  }

  @Test
  fun executionPaneSummaryMentionsWhenQueueIsTrimmed() {
    assertEquals(
      "Showing next 3 of 7 queued PIS items.",
      buildExecutionPaneSummary(totalCount = 7, visibleCount = 3),
    )
    assertEquals(
      "Showing the full execution queue.",
      buildExecutionPaneSummary(totalCount = 2, visibleCount = 2),
    )
  }

  @Test
  fun executionStageSummaryExplainsCurrentSelectionCount() {
    assertEquals("No PIS items staged for WF-01 yet.", buildExecutionStageSummary(stagedCount = 0))
    assertEquals("1 PIS item staged for WF-01.", buildExecutionStageSummary(stagedCount = 1))
    assertEquals("3 PIS items staged for WF-01.", buildExecutionStageSummary(stagedCount = 3))
  }

  @Test
  fun sanitizeExecutionSelectionsDropsMissingAndBlankItems() {
    val items = listOf(
      ledgerItem(uid = "PIS-1", title = "First"),
      ledgerItem(uid = "PIS-2", title = "Second"),
    )

    assertEquals(
      linkedSetOf("PIS-2"),
      sanitizeBacklogExecutionSelections(
        stagedUids = linkedSetOf(" ", "PIS-9", "PIS-2"),
        items = items,
      ),
    )
  }

  @Test
  fun editableStatusOptionsStayCanonicalButKeepLegacyCurrentValueVisible() {
    val options = editableBacklogStatusOptions("ready_to_implement")

    assertEquals("ready_to_implement", options.first())
    assertTrue(options.contains("ready_for_implement"))
    assertTrue(options.contains("done"))
  }

  @Test
  fun applyStatusUpdateOnlyTouchesMatchingItem() {
    val first = ledgerItem(uid = "PIS-20260308-AAA1", status = "draft")
    val second = ledgerItem(uid = "PIS-20260308-BBB2", status = "ready_for_implement")

    val updated = applyBacklogStatusUpdate(
      items = listOf(first, second),
      uid = second.uid,
      status = "in_progress",
      updatedUtc = "2026-03-11T12:00:00Z",
    )

    assertEquals("draft", updated[0].status)
    assertEquals("ready_for_implement", second.status)
    assertEquals("in_progress", updated[1].status)
    assertEquals("2026-03-11T12:00:00Z", updated[1].updatedUtc)
  }

  @Test
  fun nonBacklogStatusUpdateWarnsThatRefreshWillRemoveItem() {
    assertEquals(
      "Status changed: Ready for implement -> In progress.",
      backlogStatusUpdateMessage("ready_for_implement", "in_progress"),
    )
  }

  @Test
  fun missingBacklogStatusRouteErrorMatchesBareNotFoundOnly() {
    assertTrue(isMissingBacklogStatusRouteError("not found"))
    assertTrue(isMissingBacklogStatusRouteError(" Not Found "))
    assertFalse(isMissingBacklogStatusRouteError("item not found"))
  }

  @Test
  fun workflowRoleStatesKeepExactlyOneLiveRoleDuringBuilderRun() {
    val states = buildWorkflowRoleStates(
      runtime = workflowStatus(
        status = "running",
        turn = "builder_run",
        events = listOf(
          WorkflowEvent(
            utc = "2026-03-11T12:00:00Z",
            event = "se_tasking_done",
            payload = emptyMap(),
          ),
        ),
      ),
      workflowHandle = null,
    )

    assertTrue(states.single { it.label == "B" }.active)
    assertFalse(states.single { it.label == "O" }.active)
    assertFalse(states.single { it.label == "V" }.active)
    assertTrue(states.single { it.label == "O" }.completed)
    assertEquals("Done", states.single { it.label == "O" }.statusText)
    assertEquals("Active", states.single { it.label == "B" }.statusText)
    assertEquals("Waiting", states.single { it.label == "V" }.statusText)
  }

  @Test
  fun workflowActivityModelPrefersFriendlyBuilderSummaryAndRecentConversation() {
    val model = buildBacklogWorkflowActivityModel(
      BacklogUiState(
        selectedUid = "PIS-20260308-123000Z-A1B2",
        workflowHandle = workflowHandle(status = "running", turn = "builder_run"),
        workflowRuntime = workflowStatus(
          status = "running",
          turn = "builder_run",
          iteration = 2,
          maxIterations = 4,
        ),
        workflowActivity = listOf(
          WorkflowConversationItem(
            id = "evt-1",
            utc = "2026-03-11T12:02:00Z",
            actor = "builder",
            source = "builder",
            target = "orchestrator",
            kind = "commentary",
            title = "",
            text = "Running the focused Android tests and checking the stop-path behavior now.",
            origin = "agent",
          ),
        ),
      ),
    )

    assertEquals("Builder working", model.headline)
    assertTrue(model.live)
    assertTrue(model.detail.contains("Iteration 2 of 4"))
    assertEquals(1, model.entries.size)
    assertEquals("B", model.entries.single().label)
    assertTrue(model.entries.single().body.contains("focused Android tests"))
  }

  @Test
  fun workflowActivityModelShowsFriendlyEmptyStateBeforeLaunch() {
    val model = buildBacklogWorkflowActivityModel(
      BacklogUiState(
        selectedUid = "PIS-20260308-123000Z-A1B2",
      ),
    )

    assertEquals("No workflow yet", model.headline)
    assertFalse(model.live)
    assertTrue(model.detail.contains("Launch WF-01"))
    assertTrue(model.entries.isEmpty())
  }

  @Test
  fun workflowActivityModelShowsHostVerificationWaitReason() {
    val model = buildBacklogWorkflowActivityModel(
      BacklogUiState(
        selectedUid = "PIS-20260308-123000Z-A1B2",
        workflowHandle = workflowHandle(status = "running", turn = "host_verification"),
        workflowRuntime = workflowStatus(
          status = "running",
          statusDetail = "running_waiting_host_verification",
          turn = "host_verification",
          activeRole = "V",
          activeWaitReason = "Waiting for focused Android host verification.",
        ),
      ),
    )

    assertEquals("Waiting on host proof", model.headline)
    assertTrue(model.detail.contains("focused Android host verification"))
  }

  @Test
  fun backlogWorkflowStartRequestCarriesTrimmedAdditionalInstructions() {
    val request = buildBacklogWorkflowStartRequest(
      uid = " PIS-20260308-123000Z-A1B2 ",
      executionDirective = "  Focus on the failing verifier path first.  ",
    )

    assertEquals("ledger_uid", request.bindingType)
    assertEquals("PIS-20260308-123000Z-A1B2", request.bindingId)
    assertEquals("WF-01", request.phaseLabel)
    assertEquals("Implement and verify PIS-20260308-123000Z-A1B2 with WF-01.", request.objective)
    assertEquals(null, request.maxIterations)
    assertEquals("Focus on the failing verifier path first.", request.executionDirective)
  }

  @Test
  fun workflowExecutionTargetLabelRequiresStageBeforeNewLaunch() {
    assertEquals(
      "Tick a PIS in the execution list to stage WF-01.",
      buildWorkflowExecutionTargetLabel(
        selectedUid = "",
        stagedExecutionUids = emptySet(),
      ),
    )
    assertEquals(
      "Tick this PIS in the execution list to run WF-01.",
      buildWorkflowExecutionTargetLabel(
        selectedUid = "PIS-20260308-123000Z-A1B2",
        stagedExecutionUids = setOf("PIS-20260308-OTHER"),
      ),
    )
    assertEquals(
      "WF-01 target: PIS-20260308-123000Z-A1B2 · 2 staged",
      buildWorkflowExecutionTargetLabel(
        selectedUid = "PIS-20260308-123000Z-A1B2",
        stagedExecutionUids = setOf("PIS-20260308-123000Z-A1B2", "PIS-20260308-OTHER"),
      ),
    )
  }

  @Test
  fun backlogWorkflowStartRequestPromotesLeadingDigitToIterations() {
    val request = buildBacklogWorkflowStartRequest(
      uid = "PIS-20260308-123000Z-A1B2",
      executionDirective = "4 Focus on the failing verifier path first.",
    )

    assertEquals(4, request.maxIterations)
    assertEquals("Focus on the failing verifier path first.", request.executionDirective)
  }

  @Test
  fun workflowLaunchDirectiveIgnoresOutOfRangeLeadingDigit() {
    val directive = parseWorkflowLaunchDirective("9 Keep this as plain text")

    assertEquals(null, directive.maxIterations)
    assertEquals("9 Keep this as plain text", directive.instruction)
  }

  @Test
  fun workflowRoleStatesShowEscalatingBuilderLoop() {
    val states = buildWorkflowRoleStates(
      runtime = workflowStatus(
        status = "running",
        statusDetail = "blocked_validation_loop",
        turn = "builder_run",
        activeRole = "B",
      ),
      workflowHandle = null,
    )

    assertEquals("Looping", states.single { it.label == "B" }.statusText)
    assertFalse(states.single { it.label == "O" }.active)
  }

  @Test
  fun cockpitActionSelectionPrefersStagedSetButFallsBackToSelectedUid() {
    assertEquals(
      listOf("PIS-2", "PIS-3"),
      buildCockpitActionSelection(
        selectedUid = "PIS-1",
        stagedExecutionUids = linkedSetOf("PIS-2", "PIS-3"),
      ),
    )
    assertEquals(
      listOf("PIS-1"),
      buildCockpitActionSelection(
        selectedUid = "PIS-1",
        stagedExecutionUids = emptySet(),
      ),
    )
  }

  @Test
  fun cockpitSelectionSummaryExplainsSingleAndBatchLaunchTargets() {
    assertEquals(
      "Campaign target: PIS-1.",
      buildCockpitSelectionSummary(selectedUid = "PIS-1", stagedExecutionUids = emptySet()),
    )
    assertEquals(
      "Campaign targets: 2 staged PIS items.",
      buildCockpitSelectionSummary(selectedUid = "PIS-1", stagedExecutionUids = linkedSetOf("PIS-2", "PIS-3")),
    )
  }

  @Test
  fun derivedLivenessDoesNotMarkWaitingOperatorStateAsHealthy() {
    val payload = MobileCockpitPayload(
      schemaVersion = "mobile_cockpit_v1",
      utc = "2026-04-02T10:00:00Z",
      shell = MobileCockpitShell(status = "waiting_on_operator"),
      runTriage = MobileCockpitRunTriage(status = "waiting_on_operator"),
    )

    assertEquals("Degraded", buildDerivedLiveness(payload))
  }

  @Test
  fun executiveActionRequiresSelectionOrStagedSet() {
    assertFalse(canLaunchExecutiveAction(BacklogUiState()))
    assertTrue(canLaunchExecutiveAction(BacklogUiState(selectedUid = "PIS-1")))
    assertTrue(canLaunchExecutiveAction(BacklogUiState(stagedExecutionUids = linkedSetOf("PIS-2"))))
  }

  @Test
  fun derivedLivenessOnlyReportsHealthyForExplicitHealthyStates() {
    val payload = MobileCockpitPayload(
      schemaVersion = "mobile_cockpit_v1",
      utc = "2026-04-02T10:00:00Z",
      shell = MobileCockpitShell(status = "running"),
      runTriage = MobileCockpitRunTriage(status = "running"),
      chronicle = MobileCockpitChronicle(),
    )

    assertEquals("Healthy", buildDerivedLiveness(payload))
  }

  private fun ledgerItem(
    uid: String = "PIS-20260308-123000Z-A1B2",
    slug: String = "demo-slug",
    title: String = "Demo title",
    status: String = "draft",
    releaseState: String = "draft",
  ): LedgerItemSummary {
    return LedgerItemSummary(
      uid = uid,
      slug = slug,
      title = title,
      status = status,
      releaseState = releaseState,
      updatedUtc = "2026-03-08T12:30:00Z",
    )
  }

  private fun workflowHandle(
    status: String,
    turn: String,
  ): OrchestrationWorkflowSummary {
    return OrchestrationWorkflowSummary(
      workflowId = "wf-123",
      phaseLabel = "WF-01",
      status = status,
      updatedUtc = "2026-03-11T12:00:00Z",
      createdUtc = "2026-03-11T11:58:00Z",
      bindingType = "ledger_uid",
      bindingId = "PIS-20260308-123000Z-A1B2",
      turn = turn,
    )
  }

  private fun workflowStatus(
    status: String,
    statusDetail: String = "",
    turn: String,
    activeRole: String = "",
    activeWaitReason: String = "",
    activeProgressNote: String = "",
    iteration: Int = 1,
    maxIterations: Int = 4,
    events: List<WorkflowEvent> = emptyList(),
  ): WorkflowStatus {
    return WorkflowStatus(
      workflowId = "wf-123",
      status = status,
      statusDetail = statusDetail,
      turn = turn,
      activeRole = activeRole,
      activeWaitReason = activeWaitReason,
      activeProgressNote = activeProgressNote,
      lastMeaningfulProgressUtc = "",
      lastMeaningfulProgressEvent = "",
      stuckReason = "",
      iteration = iteration,
      maxIterations = maxIterations,
      phaseLabel = "WF-01",
      objective = "Implement and verify the selected PIS.",
      verifierVerdict = "",
      lastError = "",
      intentValidationReport = "",
      events = events,
    )
  }
}
