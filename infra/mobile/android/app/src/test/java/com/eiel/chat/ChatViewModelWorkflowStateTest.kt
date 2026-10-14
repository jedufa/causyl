package com.eiel.chat

import android.app.Application
import android.content.SharedPreferences
import com.eiel.chat.data.ChatApi
import com.eiel.chat.data.ChatRemoteCancelFailedException
import com.eiel.chat.data.ChatRemoteCancelPendingException
import com.eiel.chat.data.ChatRepository
import com.eiel.chat.data.LedgerItemSummary
import com.eiel.chat.data.MobileRuntimePolicy
import com.eiel.chat.data.MobileRuntimePolicySource
import com.eiel.chat.data.ServerHealthProbe
import com.eiel.chat.domain.AgentTab
import com.eiel.chat.domain.AGENT_PROMPT_QUEUE_MAX_DEPTH
import com.eiel.chat.domain.DIRECT_CHAT_STOP_RACE_LABEL
import com.eiel.chat.domain.MessageRole
import com.eiel.chat.domain.MessageStatus
import com.eiel.chat.domain.SendRequest
import com.eiel.chat.domain.SessionHistoryMessage
import com.eiel.chat.domain.SessionSnapshot
import com.eiel.chat.domain.SessionStats
import com.eiel.chat.domain.StreamEvent
import com.eiel.chat.domain.StreamPhase
import com.eiel.chat.domain.SessionStreamState
import com.eiel.chat.domain.WorkflowEvent
import com.eiel.chat.domain.WorkflowStartRequest
import com.eiel.chat.domain.WorkflowStartResponse
import com.eiel.chat.domain.WorkflowStatus
import com.eiel.chat.domain.buildPromptWithAttachedDocuments
import com.eiel.chat.ui.ChatViewModel
import com.eiel.chat.ui.LedgerItemsSource
import com.eiel.chat.ui.MarkdownBlock
import com.eiel.chat.ui.MessageRenderBlock
import com.eiel.chat.ui.SemanticBlockKind
import com.eiel.chat.ui.parseMessageRenderBlocks
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.ExperimentalCoroutinesApi
import kotlinx.coroutines.CancellationException
import kotlinx.coroutines.channels.Channel
import kotlinx.coroutines.flow.Flow
import kotlinx.coroutines.flow.emptyFlow
import kotlinx.coroutines.flow.flow
import kotlinx.coroutines.awaitCancellation
import kotlinx.coroutines.launch
import kotlinx.coroutines.test.StandardTestDispatcher
import kotlinx.coroutines.test.TestCoroutineScheduler
import kotlinx.coroutines.test.TestScope
import kotlinx.coroutines.test.advanceTimeBy
import kotlinx.coroutines.test.advanceUntilIdle
import kotlinx.coroutines.test.resetMain
import kotlinx.coroutines.test.runCurrent
import kotlinx.coroutines.test.runTest
import kotlinx.coroutines.test.setMain
import org.junit.After
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Before
import org.junit.Test

@OptIn(ExperimentalCoroutinesApi::class)
class ChatViewModelWorkflowStateTest {
  private val scheduler = TestCoroutineScheduler()
  private val dispatcher = StandardTestDispatcher(scheduler)
  private val createdViewModels = mutableListOf<ChatViewModel>()

  @Before
  fun setUp() {
    Dispatchers.setMain(dispatcher)
  }

  @After
  fun tearDown() {
    createdViewModels.asReversed().forEach(::clearViewModelForTest)
    createdViewModels.clear()
    Dispatchers.resetMain()
  }

  @Test
  fun sparseTerminalStatusUsesPersistedMembershipForFinalFanout() = runTest(dispatcher) {
    val prefs = InMemorySharedPreferences(
      mutableMapOf(
        "workflow_runtime_records" to """
          [
            {
              "workflow_id":"wf-123",
              "status":"running",
              "iteration":2,
              "participants":["o","b","v"]
            }
          ]
        """.trimIndent(),
        "active_workflow_o" to "wf-123",
        "active_workflow_b" to "wf-123",
        "active_workflow_v" to "wf-123",
      ),
    )
    val viewModel = testViewModel(prefs = prefs)

    viewModel.processWorkflowStatus(
      WorkflowStatus(
        workflowId = "wf-123",
        status = "completed",
        turn = "done",
        iteration = 3,
        maxIterations = 3,
        phaseLabel = "WF-01",
        objective = "Implement PIS-006",
        verifierVerdict = "PASS",
        lastError = "",
        intentValidationReport = "Final report from verifier loop",
        events = emptyList(),
      ),
    )

    listOf(AgentTab.ORCHESTRATOR, AgentTab.BUILDER, AgentTab.VERIFIER).forEach { tab ->
      val agentState = viewModel.uiState.value.agentStates.getValue(tab)
      assertTrue(agentState.messages.any { it.content.contains("Final report from verifier loop") })
      assertEquals(null, agentState.activeWorkflowId)
    }
  }

  @Test
  fun completedAssistantReplyOnlyUpdatesTitleWhenAgentEmitsStructuredTitle() = runTest(dispatcher) {
    val api = ControlledStreamChatApi()
    val prefs = InMemorySharedPreferences()
    val viewModel = testViewModel(api = api, prefs = prefs)

    viewModel.onDraftChanged("please investigate the mobile updater browser fallback")
    viewModel.onSendClicked()
    advanceUntilIdle()

    val laneOne = viewModel.uiState.value.agentStates.getValue(AgentTab.ALL)
    assertEquals("", laneOne.title)
    assertEquals("AGT", laneOne.displayTitle)
    api.finishRequest(0, tokens = listOf("Mobile updater browser fallback recovery plan."))
    advanceUntilIdle()

    assertEquals(
      "",
      viewModel.uiState.value.agentStates.getValue(AgentTab.ALL).title,
    )
    assertEquals(
      null,
      prefs.getString("conversation_title_a", null),
    )

    viewModel.onTabSelected(AgentTab.ALL_2)
    viewModel.onDraftChanged("builder, fix the admin terminal toggle persistence")
    viewModel.onSendClicked()
    advanceUntilIdle()
    api.finishRequest(
      1,
      tokens = listOf("Admin terminal toggle persistence repair plan."),
      title = "Admin Toggle Fix",
    )
    advanceUntilIdle()

    val laneTwo = viewModel.uiState.value.agentStates.getValue(AgentTab.ALL_2)
    assertEquals("Admin Toggle Fix", laneTwo.title)
    assertEquals(
      "Admin Toggle Fix",
      prefs.getString("conversation_title_a2", null),
    )
  }

  @Test
  fun allGeneralistTabsKeepFixedChipLabelsAndSharedAgentFallbackHeader() = runTest(dispatcher) {
    val viewModel = testViewModel()
    val state = viewModel.uiState.value

    assertEquals(listOf("A", "A2", "A3", "O", "B", "V"), state.tabs.map { it.label })
    assertEquals("AGT", state.agentStates.getValue(AgentTab.ALL).displayTitle)
    assertEquals("AGT", state.agentStates.getValue(AgentTab.ALL_2).displayTitle)
    assertEquals("AGT", state.agentStates.getValue(AgentTab.ALL_3).displayTitle)
    assertEquals("ORCH", state.agentStates.getValue(AgentTab.ORCHESTRATOR).displayTitle)
    assertEquals("BLD", state.agentStates.getValue(AgentTab.BUILDER).displayTitle)
    assertEquals("VER", state.agentStates.getValue(AgentTab.VERIFIER).displayTitle)
  }

  @Test
  fun sendRefreshesRuntimePolicyBeforeApplyingIdleTimeoutFailureText() = runTest(dispatcher) {
    val api = object : FakeChatApi() {
      override fun stream(request: SendRequest): Flow<StreamEvent> = flow {
        throw CancellationException("stream idle timeout")
      }
    }
    val runtimePolicySource = FakeMobileRuntimePolicySource(
      currentPolicy = MobileRuntimePolicy(
        chatTurnTimeoutS = 900,
        chatIdleTimeoutS = 120,
        workflowTimeoutS = 7000,
        fetchedAtMs = 0L,
      ),
      refreshedPolicy = MobileRuntimePolicy(
        chatTurnTimeoutS = 1800,
        chatIdleTimeoutS = 240,
        workflowTimeoutS = 7000,
        fetchedAtMs = 240_000L,
      ),
    )
    val viewModel = testViewModel(api = api, runtimePolicySource = runtimePolicySource)

    viewModel.onDraftChanged("please take the longer timeout budget")
    viewModel.onSendClicked()
    advanceUntilIdle()

    assertEquals(1, runtimePolicySource.refreshCalls)
    assertEquals(
      "No response from server for 240s.",
      viewModel.uiState.value.agentStates.getValue(AgentTab.ALL).lastError,
    )
  }

  @Test
  fun restoreUsesPersistedConversationTitleInsteadOfDerivingFromHistory() = runTest(dispatcher) {
    val prefs = InMemorySharedPreferences(
      mutableMapOf(
        "conversation_title_a" to "Admin Toggle Fix",
        "history_cache_json_a" to """
          [
            {
              "role":"user",
              "content":"please review the mobile admin page layout and terminal flow",
              "status":"complete"
            },
            {
              "role":"assistant",
              "content":"Mobile admin terminal layout review plan.",
              "status":"complete"
            }
          ]
        """.trimIndent(),
      ),
    )

    val viewModel = testViewModel(prefs = prefs)

    assertEquals(
      "Admin Toggle Fix",
      viewModel.uiState.value.agentStates.getValue(AgentTab.ALL).title,
    )
    assertEquals(
      "Admin Toggle Fix",
      prefs.getString("conversation_title_a", null),
    )
  }

  @Test
  fun restoreDropsOverlongConversationTitlesAndFallsBackToFixedTabHeader() = runTest(dispatcher) {
    val prefs = InMemorySharedPreferences(
      mutableMapOf(
        "conversation_title_a" to "mobile admin terminal timeout regression cleanup",
        "history_cache_json_a" to """
          [
            {
              "role":"user",
              "content":"please fix the mobile admin timeout regression",
              "status":"complete"
            },
            {
              "role":"assistant",
              "content":"I will investigate the mobile admin timeout regression.",
              "status":"complete"
            }
          ]
        """.trimIndent(),
      ),
    )

    val viewModel = testViewModel(prefs = prefs)
    val state = viewModel.uiState.value.agentStates.getValue(AgentTab.ALL)

    assertEquals("", state.title)
    assertEquals("AGT", state.displayTitle)
    assertNull(prefs.getString("conversation_title_a", null))
  }

  @Test
  fun followUpRepliesKeepLastAgentTitleUntilAgentChangesIt() = runTest(dispatcher) {
    val api = ControlledStreamChatApi()
    val prefs = InMemorySharedPreferences()
    val viewModel = testViewModel(api = api, prefs = prefs)

    viewModel.onDraftChanged("builder, fix the admin terminal toggle persistence")
    viewModel.onSendClicked()
    advanceUntilIdle()
    api.finishRequest(
      0,
      tokens = listOf("Admin terminal toggle persistence fix plan."),
      title = "Admin Toggle Fix",
    )
    advanceUntilIdle()

    assertEquals(
      "initial title should come from the agent marker",
      "Admin Toggle Fix",
      viewModel.uiState.value.agentStates.getValue(AgentTab.ALL).title,
    )

    viewModel.onDraftChanged("also add a regression test for the admin terminal toggle persistence")
    viewModel.onSendClicked()
    advanceUntilIdle()
    api.finishRequest(1, tokens = listOf("Admin terminal toggle persistence regression coverage."))
    advanceUntilIdle()

    assertEquals(
      "follow-up reply without a marker should keep the existing title",
      "Admin Toggle Fix",
      viewModel.uiState.value.agentStates.getValue(AgentTab.ALL).title,
    )

    viewModel.onDraftChanged("investigate the chemistry validator selected parents metric")
    viewModel.onSendClicked()
    advanceUntilIdle()
    api.finishRequest(
      2,
      tokens = listOf("Chemistry validator selected parents metric investigation."),
      title = "Chemistry Gate Check",
    )
    advanceUntilIdle()

    assertEquals(
      "topic shift should replace the header title only when the agent marks it",
      "Chemistry Gate Check",
      viewModel.uiState.value.agentStates.getValue(AgentTab.ALL).title,
    )
    assertEquals(
      "topic shift should also persist the new title",
      "Chemistry Gate Check",
      prefs.getString("conversation_title_a", null),
    )
  }

  @Test
  fun invalidLongAgentTitleIsIgnoredAndDoesNotReplaceStructuredShortTitle() = runTest(dispatcher) {
    val api = ControlledStreamChatApi()
    val prefs = InMemorySharedPreferences()
    val viewModel = testViewModel(api = api, prefs = prefs)

    viewModel.onDraftChanged("investigate the mobile runner state machine")
    viewModel.onSendClicked()
    advanceUntilIdle()
    api.finishRequest(0, tokens = listOf("Runner state machine review."), title = "Runner State Review")
    advanceUntilIdle()

    assertEquals("Runner State Review", viewModel.uiState.value.agentStates.getValue(AgentTab.ALL).title)

    viewModel.onDraftChanged("keep going")
    viewModel.onSendClicked()
    advanceUntilIdle()
    api.finishRequest(
      1,
      tokens = listOf("Follow-up detail."),
      title = "mobile runner state machine review",
    )
    advanceUntilIdle()

    assertEquals(
      "long invalid title markers should be ignored",
      "Runner State Review",
      viewModel.uiState.value.agentStates.getValue(AgentTab.ALL).title,
    )
    assertEquals("Runner State Review", prefs.getString("conversation_title_a", null))
  }

  @Test
  fun specialistTabsIgnoreConversationTitleMetadata() = runTest(dispatcher) {
    val api = ControlledStreamChatApi()
    val prefs = InMemorySharedPreferences()
    val viewModel = testViewModel(api = api, prefs = prefs)

    viewModel.onTabSelected(AgentTab.BUILDER)
    viewModel.onDraftChanged("fix the mobile workflow state handling")
    viewModel.onSendClicked()
    advanceUntilIdle()
    api.finishRequest(0, tokens = listOf("Builder implementation plan."), title = "BLD")
    advanceUntilIdle()

    val state = viewModel.uiState.value.agentStates.getValue(AgentTab.BUILDER)
    assertEquals("", state.title)
    assertEquals("BLD", state.displayTitle)
    assertNull(prefs.getString("conversation_title_b", null))
  }

  @Test
  fun restoreClearsPersistedTitlesForSpecialistTabs() = runTest(dispatcher) {
    val prefs = InMemorySharedPreferences(
      mutableMapOf(
        "conversation_title_b" to "BLD",
      ),
    )

    val viewModel = testViewModel(prefs = prefs)
    val state = viewModel.uiState.value.agentStates.getValue(AgentTab.BUILDER)

    assertEquals("", state.title)
    assertEquals("BLD", state.displayTitle)
    assertNull(prefs.getString("conversation_title_b", null))
  }

  @Test
  fun sparseTerminalStatusRecoversParticipantMembershipFromCachedWorkflowMessages() = runTest(dispatcher) {
    val prefs = InMemorySharedPreferences(
      mutableMapOf(
        "history_cache_json_o" to """
          [
            {
              "role":"workflow",
              "content":"Started for WF-01.",
              "status":"complete",
              "label":"WF-01",
              "workflow_id":"wf-456",
              "event_key":"wf-456|start"
            }
          ]
        """.trimIndent(),
        "history_cache_json_b" to """
          [
            {
              "role":"workflow",
              "content":"Builder finished iteration 1.",
              "status":"complete",
              "label":"B",
              "workflow_id":"wf-456",
              "event_key":"wf-456|builder"
            }
          ]
        """.trimIndent(),
        "history_cache_json_v" to """
          [
            {
              "role":"workflow",
              "content":"Verifier finished iteration 1 with verdict FAIL.",
              "status":"complete",
              "label":"V",
              "workflow_id":"wf-456",
              "event_key":"wf-456|verifier"
            }
          ]
        """.trimIndent(),
        "active_workflow_o" to "wf-456",
      ),
    )
    val viewModel = testViewModel(prefs = prefs)

    viewModel.processWorkflowStatus(
      WorkflowStatus(
        workflowId = "wf-456",
        status = "failed",
        turn = "done",
        iteration = 2,
        maxIterations = 3,
        phaseLabel = "WF-01",
        objective = "Implement PIS-006",
        verifierVerdict = "FAIL",
        lastError = "Verifier still failed after retry limit",
        intentValidationReport = "",
        events = emptyList(),
      ),
    )

    listOf(AgentTab.ORCHESTRATOR, AgentTab.BUILDER, AgentTab.VERIFIER).forEach { tab ->
      val state = viewModel.uiState.value.agentStates.getValue(tab)
      assertTrue(state.messages.any { it.content.contains("Verifier still failed after retry limit") })
      assertEquals(null, state.activeWorkflowId)
    }
  }

  @Test
  fun failedStatusWithWorkflowFailedEventEmitsSingleTerminalFailureBubblePerParticipant() = runTest(dispatcher) {
    val viewModel = testViewModel()

    viewModel.processWorkflowStatus(
      WorkflowStatus(
        workflowId = "wf-fail-event",
        status = "failed",
        turn = "verifier_run",
        iteration = 2,
        maxIterations = 3,
        phaseLabel = "WF-01",
        objective = "Implement PIS-010",
        verifierVerdict = "FAIL",
        lastError = "Verifier reported blocking defects",
        intentValidationReport = "",
        events = listOf(
          WorkflowEvent(
            utc = "2026-03-07T12:30:00Z",
            event = "workflow_failed",
            payload = mapOf(
              "turn" to "verifier_run",
              "error" to "Verifier reported blocking defects",
            ),
          ),
        ),
      ),
    )

    assertTerminalFailureBubbleCounts(
      viewModel = viewModel,
      expectedCount = 1,
      expectedContentSnippet = "Workflow failed during verifier_run: Verifier reported blocking defects",
    )
  }

  @Test
  fun failedStatusWithoutWorkflowFailedEventFallsBackToSingleLastErrorBubblePerParticipant() = runTest(dispatcher) {
    val viewModel = testViewModel()

    viewModel.processWorkflowStatus(
      WorkflowStatus(
        workflowId = "wf-fail-fallback",
        status = "failed",
        turn = "verifier_run",
        iteration = 2,
        maxIterations = 3,
        phaseLabel = "WF-01",
        objective = "Implement PIS-011",
        verifierVerdict = "FAIL",
        lastError = "Verifier reported blocking defects",
        intentValidationReport = "",
        events = emptyList(),
      ),
    )

    assertTerminalFailureBubbleCounts(
      viewModel = viewModel,
      expectedCount = 1,
      expectedContentSnippet = "Workflow ended with error: Verifier reported blocking defects",
    )
  }

  @Test
  fun cancelWorkflowRequestCallsApiAndClearsActiveWorkflowAfterCancelledStatus() = runTest(dispatcher) {
    val prefs = InMemorySharedPreferences(
      mutableMapOf(
        "selected_tab" to "o",
        "active_workflow_o" to "wf-123",
      ),
    )
    val api = CancelWorkflowChatApi(
      status = WorkflowStatus(
        workflowId = "wf-123",
        status = "cancelled",
        turn = "builder_run",
        iteration = 1,
        maxIterations = 3,
        phaseLabel = "WF-01",
        objective = "Implement PIS-006",
        verifierVerdict = "",
        lastError = "",
        intentValidationReport = "",
        events = listOf(
          WorkflowEvent(
            utc = "2026-03-10T22:40:00Z",
            event = "workflow_cancelled",
            payload = mapOf("reason" to "cancelled from mobile"),
          ),
        ),
      ),
    )
    val viewModel = testViewModel(api = api, prefs = prefs)

    viewModel.onWorkflowCancelRequested("wf-123")
    advanceUntilIdle()

    assertEquals(listOf("wf-123|cancelled from mobile"), api.cancelCalls)
    assertNull(viewModel.uiState.value.agentStates.getValue(AgentTab.ORCHESTRATOR).activeWorkflowId)
    assertTrue(
      viewModel.uiState.value.agentStates.getValue(AgentTab.ORCHESTRATOR).messages.any {
        it.role == MessageRole.WORKFLOW && it.content.contains("Workflow cancelled.")
      },
    )
  }

  @Test
  fun cancelWorkflowFailureSurfacesLastErrorOnSelectedTab() = runTest(dispatcher) {
    val prefs = InMemorySharedPreferences(
      mutableMapOf(
        "selected_tab" to "o",
        "active_workflow_o" to "wf-123",
      ),
    )
    val api = object : FakeChatApi() {
      override suspend fun cancelWorkflow(workflowId: String, reason: String) {
        throw IllegalStateException("workflow cancel failed: HTTP 500")
      }
    }
    val viewModel = testViewModel(api = api, prefs = prefs)

    viewModel.onWorkflowCancelRequested("wf-123")
    advanceUntilIdle()

    assertEquals(
      "workflow cancel failed: HTTP 500",
      viewModel.uiState.value.agentStates.getValue(AgentTab.ORCHESTRATOR).lastError,
    )
  }

  @Test
  fun reprocessingSameFailedTerminalStatusDoesNotDuplicateFailureBubbles() = runTest(dispatcher) {
    val viewModel = testViewModel()
    val status = WorkflowStatus(
      workflowId = "wf-fail-repeat",
      status = "failed",
      turn = "verifier_run",
      iteration = 2,
      maxIterations = 3,
      phaseLabel = "WF-01",
      objective = "Implement PIS-012",
      verifierVerdict = "FAIL",
      lastError = "Verifier reported blocking defects",
      intentValidationReport = "",
      events = listOf(
        WorkflowEvent(
          utc = "2026-03-07T12:35:00Z",
          event = "workflow_failed",
          payload = mapOf(
            "turn" to "verifier_run",
            "error" to "Verifier reported blocking defects",
          ),
        ),
      ),
    )

    viewModel.processWorkflowStatus(status)
    viewModel.processWorkflowStatus(status)

    assertTerminalFailureBubbleCounts(
      viewModel = viewModel,
      expectedCount = 1,
      expectedContentSnippet = "Workflow failed during verifier_run: Verifier reported blocking defects",
    )
  }

  @Test
  fun pendingWorkflowLaunchSurvivesRestoreAndIsMarkedFailedWhenInterrupted() = runTest(dispatcher) {
    val prefs = InMemorySharedPreferences(
      mutableMapOf(
        "history_cache_json_o" to """
          [
            {
              "role":"workflow",
              "content":"WF-01 requested for PIS-006\\nAdditional request: Check edge cases",
              "status":"sending",
              "label":"WF-01"
            }
          ]
        """.trimIndent(),
        "pending_workflow_launches" to """
          [
            {
              "tab":"o",
              "pis_id":"PIS-006",
              "commentary":"Check edge cases",
              "request_summary":"WF-01 requested for PIS-006\\nAdditional request: Check edge cases",
              "created_at_ms":100
            }
          ]
        """.trimIndent(),
      ),
    )

    val viewModel = testViewModel(prefs = prefs)

    val state = viewModel.uiState.value.agentStates.getValue(AgentTab.ORCHESTRATOR)
    val workflowMessage = state.messages.single { it.role == MessageRole.WORKFLOW }

    assertEquals(MessageStatus.FAILED, workflowMessage.status)
    assertTrue(workflowMessage.content.contains("failed to start"))
    assertTrue(workflowMessage.content.contains("interrupted before the server acknowledged start"))
    assertFalse(state.isLaunchingWorkflow)
    assertEquals("[]", prefs.getString("pending_workflow_launches", null))
  }

  @Test
  fun pendingWorkflowLaunchIsRecreatedFromPendingRecordWhenCachedHistoryIsMissing() = runTest(dispatcher) {
    val prefs = InMemorySharedPreferences(
      mutableMapOf(
        "pending_workflow_launches" to """
          [
            {
              "tab":"o",
              "pis_id":"PIS-009",
              "commentary":"Check retry edge cases",
              "request_summary":"WF-01 requested for PIS-009\\nAdditional request: Check retry edge cases",
              "created_at_ms":250
            }
          ]
        """.trimIndent(),
      ),
    )

    val viewModel = testViewModel(prefs = prefs)

    val state = viewModel.uiState.value.agentStates.getValue(AgentTab.ORCHESTRATOR)
    val workflowMessage = state.messages.single { it.role == MessageRole.WORKFLOW }

    assertEquals(MessageStatus.FAILED, workflowMessage.status)
    assertTrue(workflowMessage.content.contains("PIS-009"))
    assertTrue(workflowMessage.content.contains("Check retry edge cases"))
    assertTrue(workflowMessage.content.contains("interrupted before the server acknowledged start"))
    assertEquals("[]", prefs.getString("pending_workflow_launches", null))
    assertTrue(
      prefs.getString("history_cache_json_o", "")!!.contains("PIS-009"),
    )
  }

  @Test
  fun pendingWorkflowRestorePrefersPersistedSummaryOverRebuiltFormat() = runTest(dispatcher) {
    val legacySummary = "WF-01 requested for PIS-006\nAdditional request: Check edge cases"
    val prefs = InMemorySharedPreferences(
      mutableMapOf(
        "history_cache_json_o" to """
          [
            {
              "role":"workflow",
              "content":${jsonStringLiteral(legacySummary)},
              "status":"sending",
              "label":"WF-01"
            }
          ]
        """.trimIndent(),
        "pending_workflow_launches" to """
          [
            {
              "tab":"o",
              "pis_id":"PIS-006",
              "commentary":"Check edge cases",
              "request_summary":${jsonStringLiteral(legacySummary)},
              "created_at_ms":100
            }
          ]
        """.trimIndent(),
      ),
    )

    val viewModel = testViewModel(prefs = prefs)

    val state = viewModel.uiState.value.agentStates.getValue(AgentTab.ORCHESTRATOR)
    val workflowMessages = state.messages.filter { it.role == MessageRole.WORKFLOW }

    assertEquals(1, workflowMessages.size)
    assertEquals(MessageStatus.FAILED, workflowMessages.single().status)
    assertTrue(workflowMessages.single().content.contains("failed to start"))
  }

  @Test
  fun restoredActiveWorkflowAttemptsSingleResumeAndKeepsOriginalRequestBubble() = runTest(dispatcher) {
    val requestSummary = "WF-01 requested for PIS-20260310-114704Z-DDD3\nWorkflow config: max_iterations=4."
    val prefs = InMemorySharedPreferences(
      mutableMapOf(
        "history_cache_json_o" to """
          [
            {
              "role":"workflow",
              "content":${jsonStringLiteral(requestSummary)},
              "status":"complete",
              "label":"WF-01",
              "workflow_id":"wf-restore",
              "event_key":"request|wf-restore"
            }
          ]
        """.trimIndent(),
        "active_workflow_o" to "wf-restore",
      ),
    )
    val api = RestoredWorkflowResumeChatApi(
      statuses = mutableListOf(
        WorkflowStatus(
          workflowId = "wf-restore",
          status = "completed",
          turn = "done",
          iteration = 4,
          maxIterations = 4,
          phaseLabel = "WF-01",
          objective = "Implement and verify PIS-20260310-114704Z-DDD3 with WF-01.",
          verifierVerdict = "PASS",
          lastError = "",
          intentValidationReport = "Recovered workflow reached the final report.",
          events = emptyList(),
        ),
      ),
    )

    val viewModel = testViewModel(
      api = api,
      prefs = prefs,
      startBackgroundWork = true,
      enableWorkflowStatusPolling = true,
    )

    runCurrent()

    val messages = viewModel.uiState.value.agentStates.getValue(AgentTab.ORCHESTRATOR).messages
    assertEquals(listOf("wf-restore"), api.runWorkflowCalls)
    assertTrue(api.statusCalls >= 1)
    assertEquals(1, messages.count { it.content == requestSummary })
    assertTrue(messages.any { it.content.contains("Recovered workflow reached the final report.") })
    assertEquals(null, viewModel.uiState.value.agentStates.getValue(AgentTab.ORCHESTRATOR).activeWorkflowId)
  }

  @Test
  fun missingRestoredWorkflowBecomesRecoveryBubbleAndClearsActiveState() = runTest(dispatcher) {
    val prefs = InMemorySharedPreferences(
      mutableMapOf(
        "history_cache_json_o" to """
          [
            {
              "role":"workflow",
              "content":"Started for WF-01.",
              "status":"complete",
              "label":"WF-01",
              "workflow_id":"wf-missing",
              "event_key":"wf-missing|start"
            }
          ]
        """.trimIndent(),
        "active_workflow_o" to "wf-missing",
      ),
    )
    val api = MissingWorkflowChatApi()

    val viewModel = testViewModel(
      api = api,
      prefs = prefs,
      startBackgroundWork = true,
      enableWorkflowStatusPolling = true,
    )

    runCurrent()
    runCurrent()

    val state = viewModel.uiState.value.agentStates.getValue(AgentTab.ORCHESTRATOR)
    assertEquals(1, api.runCalls)
    assertEquals(null, state.activeWorkflowId)
    assertEquals(null, prefs.getString("active_workflow_o", null))
    assertTrue(
      state.messages.any { message ->
        message.role == MessageRole.WORKFLOW &&
          message.content.contains("Workflow recovery failed for wf-missing") &&
          message.content.contains("HTTP 404")
      },
    )
    assertTrue(prefs.getString("workflow_runtime_records", "")!!.contains("\"status\":\"failed\""))
  }

  @Test
  fun cachedWorkflowHistoryWithoutRestoredActiveWorkflowDoesNotResumeOrEmitRecoveryBubble() = runTest(dispatcher) {
    val prefs = InMemorySharedPreferences(
      mutableMapOf(
        "history_cache_json_o" to """
          [
            {
              "role":"workflow",
              "content":"Started for WF-01.",
              "status":"complete",
              "label":"WF-01",
              "workflow_id":"wf-history-only",
              "event_key":"wf-history-only|start"
            }
          ]
        """.trimIndent(),
      ),
    )
    val api = RestoredWorkflowResumeChatApi(
      statuses = mutableListOf(runningWorkflowStatus("wf-history-only")),
    )

    val viewModel = testViewModel(api = api, prefs = prefs, startBackgroundWork = true)

    runCurrent()

    val state = viewModel.uiState.value.agentStates.getValue(AgentTab.ORCHESTRATOR)
    assertTrue(api.runWorkflowCalls.isEmpty())
    assertEquals(0, api.statusCalls)
    assertFalse(
      state.messages.any { message ->
        message.role == MessageRole.WORKFLOW && message.content.contains("Workflow recovery failed")
      },
    )
  }

  @Test
  fun repeatedStartupKeepsSingleRequestBubbleWhileResumingRestoredWorkflowOncePerStartup() = runTest(dispatcher) {
    val requestSummary = "WF-01 requested for PIS-20260310-114704Z-DDD3\nWorkflow config: max_iterations=4."
    val prefs = InMemorySharedPreferences(
      mutableMapOf(
        "history_cache_json_o" to """
          [
            {
              "role":"workflow",
              "content":${jsonStringLiteral(requestSummary)},
              "status":"complete",
              "label":"WF-01",
              "workflow_id":"wf-repeat",
              "event_key":"request|wf-repeat"
            }
          ]
        """.trimIndent(),
        "active_workflow_o" to "wf-repeat",
      ),
    )
    val api = RestoredWorkflowResumeChatApi(
      statuses = mutableListOf(
        runningWorkflowStatus("wf-repeat"),
        runningWorkflowStatus("wf-repeat"),
        runningWorkflowStatus("wf-repeat"),
        runningWorkflowStatus("wf-repeat"),
      ),
    )

    val firstViewModel = testViewModel(
      api = api,
      prefs = prefs,
      startBackgroundWork = true,
      enableWorkflowStatusPolling = true,
    )
    runCurrent()

    val firstState = firstViewModel.uiState.value.agentStates.getValue(AgentTab.ORCHESTRATOR)
    assertEquals(listOf("wf-repeat"), api.runWorkflowCalls)
    assertEquals(1, firstState.messages.count { it.content == requestSummary })
    assertEquals("wf-repeat", firstState.activeWorkflowId)

    val secondViewModel = testViewModel(
      api = api,
      prefs = prefs,
      startBackgroundWork = true,
      enableWorkflowStatusPolling = true,
    )
    runCurrent()

    val secondState = secondViewModel.uiState.value.agentStates.getValue(AgentTab.ORCHESTRATOR)
    assertEquals(listOf("wf-repeat", "wf-repeat"), api.runWorkflowCalls)
    assertEquals(1, secondState.messages.count { it.content == requestSummary })
    assertEquals("wf-repeat", secondState.activeWorkflowId)
  }

  @Test
  fun cachedHistoryRoundTripPreservesSemanticMarkdownForAssistantAndWorkflowMessages() = runTest(dispatcher) {
    val assistantContent = """
      :::decision
      Choose the deterministic path.
      :::

      ```python
      print("stable")
      ```
    """.trimIndent()
    val workflowContent = """
      :::next
      Update <file>infra/mobile/android/app/src/main/java/com/eiel/chat/ui/MarkdownMessage.kt</file>
      :::
    """.trimIndent()
    val prefs = InMemorySharedPreferences(
      mutableMapOf(
        "history_cache_json_a" to """
          [
            {
              "role":"assistant",
              "content":${jsonStringLiteral(assistantContent)},
              "status":"complete"
            }
          ]
        """.trimIndent(),
        "history_cache_json_o" to """
          [
            {
              "role":"workflow",
              "content":${jsonStringLiteral(workflowContent)},
              "status":"failed",
              "label":"WF-01"
            }
          ]
        """.trimIndent(),
      ),
    )

    val viewModel = testViewModel(prefs = prefs)

    val assistantMessage = viewModel.uiState.value.agentStates.getValue(AgentTab.ALL).messages.single()
    assertEquals(MessageRole.ASSISTANT, assistantMessage.role)
    assertEquals(assistantContent, assistantMessage.content)
    assertEquals(MessageStatus.COMPLETE, assistantMessage.status)
    val assistantBlocks = parseMessageRenderBlocks(assistantMessage.content)
    assertTrue(assistantBlocks.first() is MessageRenderBlock.SemanticCallout)
    assertEquals(
      MarkdownBlock.CodeFence(language = "python", code = "print(\"stable\")"),
      (assistantBlocks.last() as MessageRenderBlock.Markdown).block,
    )

    val workflowMessage = viewModel.uiState.value.agentStates.getValue(AgentTab.ORCHESTRATOR).messages.single()
    assertEquals(MessageRole.WORKFLOW, workflowMessage.role)
    assertEquals(MessageStatus.FAILED, workflowMessage.status)
    assertEquals("WF-01", workflowMessage.label)
    val workflowCallout = parseMessageRenderBlocks(workflowMessage.content).single() as MessageRenderBlock.SemanticCallout
    assertEquals(SemanticBlockKind.NEXT, workflowCallout.kind)
  }

  @Test
  fun hydratedWorkflowHistoryDoesNotDuplicateCachedWorkflowMessageWithSameEventKey() = runTest(dispatcher) {
    val workflowContent = """
      :::warning
      Check <cmd>./gradlew :app:testDebugUnitTest</cmd>
      :::
    """.trimIndent()
    val prefs = InMemorySharedPreferences(
      mutableMapOf(
        "history_cache_json_o" to """
          [
            {
              "role":"workflow",
              "content":${jsonStringLiteral(workflowContent)},
              "status":"complete",
              "label":"WF-01",
              "workflow_id":"wf-123",
              "event_key":"wf-123|warn"
            }
          ]
        """.trimIndent(),
      ),
    )
    val api = HistoryChatApi(
      history = listOf(
        SessionHistoryMessage(
          role = MessageRole.WORKFLOW,
          content = workflowContent,
          status = MessageStatus.COMPLETE,
          label = "WF-01",
          workflowId = "wf-123",
          eventKey = "wf-123|warn",
        ),
      ),
    )

    val viewModel = testViewModel(api = api, prefs = prefs, startBackgroundWork = true)

    runCurrent()

    val workflowMessages = viewModel.uiState.value.agentStates.getValue(AgentTab.ORCHESTRATOR).messages
      .filter { it.role == MessageRole.WORKFLOW }
    assertEquals(1, workflowMessages.size)
    assertEquals("wf-123|warn", workflowMessages.single().eventKey)
    val rendered = parseMessageRenderBlocks(workflowMessages.single().content).single() as MessageRenderBlock.SemanticCallout
    assertEquals(SemanticBlockKind.WARNING, rendered.kind)
  }

  @Test
  fun generalistLanesRestoreDraftHistoryAndUnreadStateIndependently() = runTest(dispatcher) {
    val prefs = InMemorySharedPreferences(
      mutableMapOf(
        "selected_tab" to "a3",
        "draft_a" to "draft lane a",
        "draft_a2" to "draft lane a2",
        "draft_a3" to "draft lane a3",
        "has_unread_a" to true,
        "has_unread_a2" to true,
        "has_unread_a3" to true,
        "history_cache_json_a" to """
          [
            {
              "role":"assistant",
              "content":"history lane a",
              "status":"complete"
            }
          ]
        """.trimIndent(),
        "history_cache_json_a2" to """
          [
            {
              "role":"assistant",
              "content":"history lane a2",
              "status":"complete"
            }
          ]
        """.trimIndent(),
        "history_cache_json_a3" to """
          [
            {
              "role":"assistant",
              "content":"history lane a3",
              "status":"complete"
            }
          ]
        """.trimIndent(),
      ),
    )

    val viewModel = testViewModel(prefs = prefs)

    val state = viewModel.uiState.value
    assertEquals(listOf("A", "A2", "A3", "O", "B", "V"), state.tabs.map { it.label })
    assertEquals(AgentTab.ALL_3, state.selectedTab)
    assertEquals(false, state.agentStates.getValue(AgentTab.ALL_3).hasUnread)
    assertEquals(true, state.agentStates.getValue(AgentTab.ALL).hasUnread)
    assertEquals(true, state.agentStates.getValue(AgentTab.ALL_2).hasUnread)

    val laneA = state.agentStates.getValue(AgentTab.ALL)
    val laneA2 = state.agentStates.getValue(AgentTab.ALL_2)
    val laneA3 = state.agentStates.getValue(AgentTab.ALL_3)

    assertEquals("draft lane a", laneA.draft)
    assertEquals("draft lane a2", laneA2.draft)
    assertEquals("draft lane a3", laneA3.draft)

    assertEquals("mbl", laneA.threadId)
    assertEquals("mb2", laneA2.threadId)
    assertEquals("mb3", laneA3.threadId)

    assertEquals(listOf("history lane a"), laneA.messages.map { it.content })
    assertEquals(listOf("history lane a2"), laneA2.messages.map { it.content })
    assertEquals(listOf("history lane a3"), laneA3.messages.map { it.content })
  }

  @Test
  fun pisOptionsLoadOpenItemsAtStartupAndRefreshOnDemand() = runTest(dispatcher) {
    val prefs = InMemorySharedPreferences(
      mutableMapOf(
        "selected_pis_o" to "PIS-OLD",
      ),
    )
    val ledger = FakeLedgerItemsSource(
      mutableListOf(
        listOf(
          ledgerItem(uid = "PIS-20260307-131313Z-FFFF", title = "Verified but release draft", status = "verified", releaseState = "draft", updatedUtc = "2026-03-07T13:13:13Z"),
          ledgerItem(uid = "PIS-20260307-111111Z-AAAA", title = "Draft item", status = "draft", updatedUtc = "2026-03-07T11:11:11Z"),
          ledgerItem(uid = "PIS-20260307-101010Z-BBBB", title = "Ready item", status = "ready_for_implement", updatedUtc = "2026-03-07T10:10:10Z"),
          ledgerItem(uid = "PIS-20260307-090909Z-CCCC", title = "Closed item", status = "done", updatedUtc = "2026-03-07T09:09:09Z"),
          ledgerItem(uid = "ABC-20260307-080808Z-DDDD", title = "Wrong prefix", status = "draft", updatedUtc = "2026-03-07T08:08:08Z"),
        ),
        listOf(
          ledgerItem(uid = "PIS-20260307-121212Z-EEEE", title = "Newest item", status = "draft", updatedUtc = "2026-03-07T12:12:12Z"),
          ledgerItem(uid = "PIS-20260307-111111Z-AAAA", title = "Draft item", status = "draft", updatedUtc = "2026-03-07T11:11:11Z"),
        ),
      ),
    )
    val viewModel = testViewModel(prefs = prefs, ledgerItemsSource = ledger)

    advanceUntilIdle()

    assertEquals(
      listOf(
        "PIS-20260307-131313Z-FFFF",
        "PIS-20260307-111111Z-AAAA",
        "PIS-20260307-101010Z-BBBB",
      ),
      viewModel.uiState.value.pisOptions.map { it.uid },
    )
    assertEquals("", viewModel.uiState.value.agentStates.getValue(AgentTab.ORCHESTRATOR).selectedPisId)
    assertEquals("", viewModel.uiState.value.pisOptions.first().releaseState)

    viewModel.refreshPisOptions()
    advanceUntilIdle()

    assertEquals(
      listOf("PIS-20260307-121212Z-EEEE", "PIS-20260307-111111Z-AAAA"),
      viewModel.uiState.value.pisOptions.map { it.uid },
    )
    assertEquals(2, ledger.calls)
  }

  @Test
  fun pisOptionsKeepAllStatusesExceptDone() = runTest(dispatcher) {
    val ledger = FakeLedgerItemsSource(
      mutableListOf(
        listOf(
          ledgerItem(uid = "PIS-20260307-151515Z-AAAA", title = "In progress", status = "in_progress", updatedUtc = "2026-03-07T15:15:15Z"),
          ledgerItem(uid = "PIS-20260307-141414Z-BBBB", title = "Blocked", status = "blocked", updatedUtc = "2026-03-07T14:14:14Z"),
          ledgerItem(uid = "PIS-20260307-131313Z-CCCC", title = "Released draft status", status = "draft", releaseState = "released", updatedUtc = "2026-03-07T13:13:13Z"),
          ledgerItem(uid = "PIS-20260307-121212Z-DDDD", title = "Implemented", status = "implemented", updatedUtc = "2026-03-07T12:12:12Z"),
        ),
      ),
    )
    val viewModel = testViewModel(ledgerItemsSource = ledger)

    advanceUntilIdle()

    assertEquals(
      listOf(
        "PIS-20260307-151515Z-AAAA",
        "PIS-20260307-141414Z-BBBB",
        "PIS-20260307-131313Z-CCCC",
        "PIS-20260307-121212Z-DDDD",
      ),
      viewModel.uiState.value.pisOptions.map { it.uid },
    )
  }

  @Test
  fun workflowLaunchShowsAuthoritativeMaxIterationsWithoutRetryWording() = runTest(dispatcher) {
    val api = RecordingChatApi()
    api.startResponse = WorkflowStartResponse(
      workflowId = "wf-123",
      phaseLabel = "WF-01",
      objective = "Implement and verify PIS-20260307-151515Z-AAAA with WF-01.",
      maxIterations = 5,
    )
    val viewModel = testViewModel(api = api)

    viewModel.refreshPisOptions()
    advanceUntilIdle()
    viewModel.onTabSelected(AgentTab.ORCHESTRATOR)
    viewModel.onSelectedPisChanged("PIS-20260307-151515Z-AAAA")
    viewModel.onDraftChanged("3 Please focus on validation")

    viewModel.onLaunchWorkflowClicked()
    advanceUntilIdle()

    val request = api.started.single()
    assertEquals("Implement and verify PIS-20260307-151515Z-AAAA with WF-01.", request.objective)
    assertEquals(3, request.maxIterations)
    assertFalse(request.objective.contains("retry up to"))
    val workflowMessage = viewModel.uiState.value.agentStates
      .getValue(AgentTab.ORCHESTRATOR)
      .messages
      .single { it.role == MessageRole.WORKFLOW }
    assertFalse(workflowMessage.content.contains("retry up to"))
    assertTrue(workflowMessage.content.contains("Workflow config: max_iterations=5."))
    assertTrue(workflowMessage.content.contains("Implement and verify PIS-20260307-151515Z-AAAA with WF-01."))
    assertTrue(workflowMessage.content.contains("SPECIAL INSTRUCTION: Please focus on validation"))
  }

  @Test
  fun workflowLaunchOmitsMaxIterationsWhenDraftDoesNotStartWithDigit() = runTest(dispatcher) {
    val api = RecordingChatApi()
    api.startResponse = WorkflowStartResponse(
      workflowId = "wf-123",
      phaseLabel = "WF-01",
      objective = "Implement and verify PIS-20260307-151515Z-AAAA with WF-01.",
      maxIterations = 4,
    )
    val viewModel = testViewModel(api = api)

    viewModel.refreshPisOptions()
    advanceUntilIdle()
    viewModel.onSelectedPisChanged("PIS-20260307-151515Z-AAAA")
    viewModel.onDraftChanged("Please focus on validation")

    viewModel.onLaunchWorkflowClicked()
    advanceUntilIdle()

    val request = api.started.single()
    assertEquals(null, request.maxIterations)
    val workflowMessage = viewModel.uiState.value.agentStates
      .getValue(AgentTab.ORCHESTRATOR)
      .messages
      .single { it.role == MessageRole.WORKFLOW }
    assertTrue(workflowMessage.content.contains("Workflow config: max_iterations=4."))
    assertTrue(workflowMessage.content.contains("SPECIAL INSTRUCTION: Please focus on validation"))
  }

  @Test
  fun sendUsesDistinctAgentKeysAndThreadIdsForAdditionalGeneralistLanes() = runTest(dispatcher) {
    val api = RecordingStreamChatApi()
    val viewModel = testViewModel(api = api)

    viewModel.onTabSelected(AgentTab.ALL_2)
    viewModel.onDraftChanged("hello from lane two")
    viewModel.onSendClicked()
    runCurrent()

    viewModel.onTabSelected(AgentTab.ALL_3)
    viewModel.onDraftChanged("hello from lane three")
    viewModel.onSendClicked()
    runCurrent()

    assertEquals(
      listOf(
        SendRequest(threadId = "mb2", prompt = "hello from lane two", agentKey = "a2"),
        SendRequest(threadId = "mb3", prompt = "hello from lane three", agentKey = "a3"),
      ),
      api.streamRequests,
    )
  }

  @Test
  fun sendWhileStreamingQueuesPromptAndLongPressPopRemovesNewestItem() = runTest(dispatcher) {
    val api = ControlledStreamChatApi()
    val viewModel = testViewModel(api = api)

    viewModel.onDraftChanged("first")
    viewModel.onSendClicked()
    runCurrent()

    viewModel.onDraftChanged("second")
    viewModel.onSendClicked()
    viewModel.onDraftChanged("third")
    viewModel.onSendClicked()
    runCurrent()

    val queuedBeforePop = viewModel.uiState.value.agentStates.getValue(AgentTab.ALL).queuedPrompts
    assertEquals(listOf("second", "third"), queuedBeforePop)

    viewModel.onQueueTailPopClicked()

    val state = viewModel.uiState.value.agentStates.getValue(AgentTab.ALL)
    assertEquals(listOf("second"), state.queuedPrompts)
    assertEquals("", state.draft)
    assertEquals(1, api.streamRequests.size)
  }

  @Test
  fun queuedPromptsDrainSeriallyAfterSuccessfulStreamCompletion() = runTest(dispatcher) {
    val api = ControlledStreamChatApi()
    val viewModel = testViewModel(api = api)

    viewModel.onDraftChanged("first")
    viewModel.onSendClicked()
    runCurrent()

    viewModel.onDraftChanged("second")
    viewModel.onSendClicked()
    viewModel.onDraftChanged("third")
    viewModel.onSendClicked()
    runCurrent()

    assertEquals(listOf("second", "third"), viewModel.uiState.value.agentStates.getValue(AgentTab.ALL).queuedPrompts)

    api.finishRequest(0)
    repeat(5) { runCurrent() }
    assertEquals(
      listOf(
        SendRequest(threadId = "mbl", prompt = "first", agentKey = "a"),
        SendRequest(threadId = "mbl", prompt = "second", agentKey = "a"),
      ),
      api.streamRequests,
    )
    assertEquals(listOf("third"), viewModel.uiState.value.agentStates.getValue(AgentTab.ALL).queuedPrompts)

    api.finishRequest(1)
    repeat(5) { runCurrent() }
    assertEquals(
      listOf(
        SendRequest(threadId = "mbl", prompt = "first", agentKey = "a"),
        SendRequest(threadId = "mbl", prompt = "second", agentKey = "a"),
        SendRequest(threadId = "mbl", prompt = "third", agentKey = "a"),
      ),
      api.streamRequests,
    )
    assertTrue(viewModel.uiState.value.agentStates.getValue(AgentTab.ALL).queuedPrompts.isEmpty())
  }

  @Test
  fun interruptedTurnKeepsPartialTextRetryStateAndQueuedPromptBlocked() = runTest(dispatcher) {
    val api = ControlledStreamChatApi()
    val viewModel = testViewModel(api = api)

    viewModel.onDraftChanged("first")
    viewModel.onSendClicked()
    runCurrent()

    viewModel.onDraftChanged("second")
    viewModel.onSendClicked()
    runCurrent()

    api.emitToken(0, "Partial answer")
    api.failRequest(0, "stream interrupted before completion")
    runCurrent()

    val state = viewModel.uiState.value.agentStates.getValue(AgentTab.ALL)
    val assistant = state.messages.last { it.role == MessageRole.ASSISTANT }
    assertEquals("Partial answer", assistant.content)
    assertEquals(MessageStatus.INTERRUPTED, assistant.status)
    assertEquals("first", state.lastFailedPrompt)
    assertEquals(listOf("second"), state.queuedPrompts)
    assertFalse(state.isStreaming)
    assertFalse(state.isSubmitting)
    assertEquals(1, api.streamRequests.size)
  }

  @Test
  fun canonicalCompletionAfterInterruptedTurnClearsRetryAndThenDrainsQueue() = runTest(dispatcher) {
    val api = ControlledStreamWithSnapshotChatApi(
      snapshots = mutableListOf(
        SessionSnapshot(history = emptyList()),
        SessionSnapshot(
          history = listOf(
            SessionHistoryMessage(role = MessageRole.USER, content = "first"),
            SessionHistoryMessage(role = MessageRole.ASSISTANT, content = "Partial answer completed."),
          ),
        ),
      ),
    )
    val viewModel = testViewModel(api = api)

    viewModel.onDraftChanged("first")
    viewModel.onSendClicked()
    runCurrent()

    viewModel.onDraftChanged("second")
    viewModel.onSendClicked()
    runCurrent()

    api.emitToken(0, "Partial answer")
    api.failRequest(0, "stream interrupted before completion")
    runCurrent()

    val interrupted = viewModel.uiState.value.agentStates.getValue(AgentTab.ALL)
    assertEquals("first", interrupted.lastFailedPrompt)
    assertEquals(listOf("second"), interrupted.queuedPrompts)
    assertEquals(1, api.streamRequests.size)

    advanceTimeBy(1000L)
    runCurrent()

    val recovered = viewModel.uiState.value.agentStates.getValue(AgentTab.ALL)
    assertEquals(null, recovered.lastFailedPrompt)
    assertTrue(
      recovered.messages.any {
        it.role == MessageRole.ASSISTANT &&
          it.status == MessageStatus.COMPLETE &&
          it.content == "Partial answer completed."
      },
    )
    assertEquals(
      listOf(
        SendRequest(threadId = "mbl", prompt = "first", agentKey = "a"),
        SendRequest(threadId = "mbl", prompt = "second", agentKey = "a"),
      ),
      api.streamRequests,
    )
  }

  @Test
  fun activeTurnConflictStartsRecoveryInsteadOfLeavingFailedFetchBubble() = runTest(dispatcher) {
    val api = ControlledStreamWithSnapshotChatApi(
      snapshots = mutableListOf(
        SessionSnapshot(
          history = listOf(
            SessionHistoryMessage(role = MessageRole.USER, content = "first"),
          ),
          streamState = SessionStreamState(
            status = "streaming",
            content = "Partial answer",
          ),
        ),
        SessionSnapshot(
          history = listOf(
            SessionHistoryMessage(role = MessageRole.USER, content = "first"),
            SessionHistoryMessage(role = MessageRole.ASSISTANT, content = "Partial answer completed."),
          ),
        ),
      ),
    )
    val viewModel = testViewModel(api = api)

    viewModel.onDraftChanged("first")
    viewModel.onSendClicked()
    runCurrent()

    api.failRequest(0, "stream failed: another turn is already running for this tab")
    runCurrent()

    val recovering = viewModel.uiState.value.agentStates.getValue(AgentTab.ALL)
    val recoveringAssistant = recovering.messages.last { it.role == MessageRole.ASSISTANT }
    assertEquals(MessageStatus.STREAMING, recoveringAssistant.status)
    assertEquals("Partial answer", recoveringAssistant.content)
    assertTrue(recovering.isStreaming)
    assertEquals(null, recovering.lastError)
    assertEquals(null, recovering.lastFailedPrompt)
    assertFalse(recovering.messages.any { it.content == "Failed to fetch response." })

    advanceTimeBy(1200L)
    runCurrent()

    val recovered = viewModel.uiState.value.agentStates.getValue(AgentTab.ALL)
    assertFalse(recovered.isStreaming)
    assertEquals(null, recovered.lastError)
    assertEquals(null, recovered.lastFailedPrompt)
    assertTrue(
      recovered.messages.any {
        it.role == MessageRole.ASSISTANT &&
          it.status == MessageStatus.COMPLETE &&
          it.content == "Partial answer completed."
      },
    )
    assertEquals(1, api.streamRequests.size)
    assertTrue(api.snapshotCalls >= 2)
  }

  @Test
  fun interruptedTurnPersistsAcrossRestartWhenSnapshotFetchFails() = runTest(dispatcher) {
    val prefs = InMemorySharedPreferences()
    val firstApi = ControlledStreamChatApi()
    val firstViewModel = testViewModel(api = firstApi, prefs = prefs)

    firstViewModel.onDraftChanged("first")
    firstViewModel.onSendClicked()
    runCurrent()

    firstApi.emitToken(0, "Partial answer")
    firstApi.failRequest(0, "stream interrupted before completion")
    runCurrent()

    assertTrue(
      prefs.getString("history_cache_json_a", "")!!.contains("\"status\":\"interrupted\""),
    )
    assertEquals("first", prefs.getString("last_failed_prompt_a", null))

    val restoredViewModel = testViewModel(
      api = SnapshotFailureChatApi(),
      prefs = prefs,
      startBackgroundWork = true,
    )

    runCurrent()
    advanceTimeBy(1000L)
    runCurrent()

    val restored = restoredViewModel.uiState.value.agentStates.getValue(AgentTab.ALL)
    val assistant = restored.messages.last { it.role == MessageRole.ASSISTANT }
    assertEquals("Partial answer", assistant.content)
    assertEquals(MessageStatus.INTERRUPTED, assistant.status)
    assertEquals("first", restored.lastFailedPrompt)
    assertFalse(restored.isStreaming)
  }

  @Test
  fun activeTurnConflictTreatsAwaitingResumeAsRecoverableCanonicalStream() = runTest(dispatcher) {
    val api = object : FakeChatApi() {
      override fun stream(request: SendRequest): Flow<StreamEvent> = flow {
        emit(StreamEvent.Error("another turn is already running for this tab"))
      }

      override suspend fun getSessionSnapshot(threadId: String): SessionSnapshot {
        return SessionSnapshot(
          history = listOf(SessionHistoryMessage(role = MessageRole.USER, content = "first")),
          streamState = SessionStreamState(
            status = "awaiting_resume",
            content = "Partial answer",
          ),
        )
      }
    }
    val viewModel = testViewModel(api = api)

    viewModel.onTabSelected(AgentTab.ALL)
    viewModel.onDraftChanged("first")
    viewModel.onSendClicked()
    advanceUntilIdle()

    val state = viewModel.uiState.value.agentStates.getValue(AgentTab.ALL)
    assertTrue(state.isStreaming)
    assertTrue(state.isSubmitting)
    assertNull(state.lastError)
    assertTrue(
      state.messages.any {
        it.role == MessageRole.ASSISTANT &&
          it.status == MessageStatus.STREAMING &&
          it.content == "Partial answer"
      },
    )
  }

  @Test
  fun activeTurnConflictTreatsInterruptedRecoveringAsRecoverableCanonicalStream() = runTest(dispatcher) {
    val api = object : FakeChatApi() {
      override fun stream(request: SendRequest): Flow<StreamEvent> = flow {
        emit(StreamEvent.Error("another turn is already running for this tab"))
      }

      override suspend fun getSessionSnapshot(threadId: String): SessionSnapshot {
        return SessionSnapshot(
          history = listOf(SessionHistoryMessage(role = MessageRole.USER, content = "first")),
          streamState = SessionStreamState(
            status = "interrupted_recovering",
            content = "Resuming after interruption",
            turnId = "turn-mobile-recover-1",
          ),
        )
      }
    }
    val viewModel = testViewModel(api = api)

    viewModel.onTabSelected(AgentTab.ALL)
    viewModel.onDraftChanged("first")
    viewModel.onSendClicked()
    advanceUntilIdle()

    val state = viewModel.uiState.value.agentStates.getValue(AgentTab.ALL)
    assertTrue(state.isStreaming)
    assertTrue(state.isSubmitting)
    assertNull(state.lastError)
    assertTrue(
      state.messages.any {
        it.role == MessageRole.ASSISTANT &&
          it.status == MessageStatus.STREAMING &&
          it.content == "Resuming after interruption"
      },
    )
  }

  @Test
  fun malformedSnapshotPayloadDuringRecoverySurfacesRecoveryError() = runTest(dispatcher) {
    val api = object : FakeChatApi() {
      override fun stream(request: SendRequest): Flow<StreamEvent> = flow {
        emit(StreamEvent.Error("another turn is already running for this tab"))
      }

      override suspend fun getSessionSnapshot(threadId: String): SessionSnapshot {
        throw IllegalStateException("session snapshot failed: malformed payload")
      }
    }
    val viewModel = testViewModel(api = api)

    viewModel.onTabSelected(AgentTab.ALL)
    viewModel.onDraftChanged("first")
    viewModel.onSendClicked()
    advanceUntilIdle()

    val state = viewModel.uiState.value.agentStates.getValue(AgentTab.ALL)
    assertFalse(state.isStreaming)
    assertFalse(state.isSubmitting)
    assertEquals(
      "Session recovery failed: malformed session snapshot payload from the server.",
      state.lastError,
    )
  }

  @Test
  fun acceptedTurnDisconnectWithoutTokensIsInterruptedNotFailedFetch() = runTest(dispatcher) {
    val api = object : FakeChatApi() {
      override fun stream(request: SendRequest): Flow<StreamEvent> = flow {
        emit(StreamEvent.Start("turn-123"))
        emit(StreamEvent.Error("stream ended before completion"))
      }
    }
    val viewModel = testViewModel(api = api)

    viewModel.onTabSelected(AgentTab.ALL)
    viewModel.onDraftChanged("first")
    viewModel.onSendClicked()
    advanceUntilIdle()

    val state = viewModel.uiState.value.agentStates.getValue(AgentTab.ALL)
    val assistant = state.messages.last { it.role == MessageRole.ASSISTANT }
    assertEquals(MessageStatus.INTERRUPTED, assistant.status)
    assertEquals("Connection lost while the server was still working.", assistant.content)
    assertEquals("first", state.lastFailedPrompt)
  }

  @Test
  fun idleTimeoutAfterPartialOutputLeavesInterruptedReplyAndRetryState() = runTest(dispatcher) {
    val api = ControlledStreamChatApi()
    val viewModel = testViewModel(api = api)

    viewModel.onDraftChanged("first")
    viewModel.onSendClicked()
    runCurrent()

    api.emitToken(0, "Partial answer")
    runCurrent()

    advanceTimeBy(BuildConfig.CHAT_IDLE_TIMEOUT_S.toLong() * 1000L + 6000L)
    runCurrent()

    val state = viewModel.uiState.value.agentStates.getValue(AgentTab.ALL)
    val assistant = state.messages.last { it.role == MessageRole.ASSISTANT }
    assertEquals("Partial answer", assistant.content)
    assertEquals(MessageStatus.INTERRUPTED, assistant.status)
    assertEquals("first", state.lastFailedPrompt)
    assertTrue(state.lastError.orEmpty().contains("No response from server"))
    assertFalse(state.isStreaming)
  }

  @Test
  fun backgroundStreamingDoesNotMarkUnreadUntilTerminalEventArrives() = runTest(dispatcher) {
    val api = ControlledStreamChatApi()
    val viewModel = testViewModel(api = api)

    viewModel.onTabSelected(AgentTab.ALL_2)
    viewModel.onDraftChanged("hello from lane two")
    viewModel.onSendClicked()
    runCurrent()

    viewModel.onTabSelected(AgentTab.ALL)
    runCurrent()

    api.emitProgress(0, "Thinking through the patch.")
    runCurrent()
    assertFalse(viewModel.uiState.value.agentStates.getValue(AgentTab.ALL_2).hasUnread)

    api.emitToken(0, "Final")
    api.emitToken(0, " answer")
    runCurrent()
    assertFalse(viewModel.uiState.value.agentStates.getValue(AgentTab.ALL_2).hasUnread)

    api.finishRequest(0)
    advanceTimeBy(1200L)
    runCurrent()

    val laneTwo = viewModel.uiState.value.agentStates.getValue(AgentTab.ALL_2)
    assertTrue(laneTwo.hasUnread)
    assertEquals("Final answer", laneTwo.messages.last { it.role == MessageRole.ASSISTANT }.content)
  }

  @Test
  fun hostVerificationWaitPhaseIsVisibleWithoutMarkingUnreadUntilDone() = runTest(dispatcher) {
    val api = ControlledStreamChatApi()
    val viewModel = testViewModel(api = api)

    viewModel.onTabSelected(AgentTab.ALL_2)
    viewModel.onDraftChanged("please verify this")
    viewModel.onSendClicked()
    runCurrent()

    api.emitToken(0, "Need host proof.")
    runCurrent()

    viewModel.onTabSelected(AgentTab.ALL)
    runCurrent()

    api.emitProgress(0, "Running host verification...", phase = StreamPhase.HOST_VERIFICATION_WAIT)
    runCurrent()

    val waiting = viewModel.uiState.value.agentStates.getValue(AgentTab.ALL_2)
    assertEquals(StreamPhase.HOST_VERIFICATION_WAIT, waiting.streamPhase)
    assertTrue((waiting.streamPhaseStartedAtMs ?: 0L) > 0L)
    assertFalse(waiting.hasUnread)

    api.finishRequest(0)
    advanceUntilIdle()

    val finished = viewModel.uiState.value.agentStates.getValue(AgentTab.ALL_2)
    assertNull(finished.streamPhase)
    assertTrue(finished.hasUnread)
  }

  @Test
  fun completedStreamRefreshesCanonicalSnapshotSoHostVerificationFollowupAppears() = runTest(dispatcher) {
    val api = ControlledStreamWithSnapshotChatApi(
      snapshots = mutableListOf(
        SessionSnapshot(
          history = listOf(
            SessionHistoryMessage(role = MessageRole.USER, content = "please verify this"),
            SessionHistoryMessage(role = MessageRole.ASSISTANT, content = "Need host proof."),
            SessionHistoryMessage(role = MessageRole.ASSISTANT, content = "Host verification result:\nPASS"),
          ),
        ),
      ),
    )
    val viewModel = testViewModel(api = api)

    viewModel.onDraftChanged("please verify this")
    viewModel.onSendClicked()
    runCurrent()

    api.emitToken(0, "Need host proof.")
    api.finishRequest(0)
    advanceUntilIdle()

    val state = viewModel.uiState.value.agentStates.getValue(AgentTab.ALL)
    val assistantMessages = state.messages.filter { it.role == MessageRole.ASSISTANT }
    assertFalse(state.isStreaming)
    assertEquals(1, api.snapshotCalls)
    assertEquals(
      listOf("Need host proof.", "Host verification result:\nPASS"),
      assistantMessages.map { it.content },
    )
  }

  @Test
  fun completedStreamWaitsForCanonicalSnapshotBeforeQueuedPromptDrains() = runTest(dispatcher) {
    val api = BlockingSnapshotChatApi(
      snapshot = SessionSnapshot(
        history = listOf(
          SessionHistoryMessage(role = MessageRole.USER, content = "please verify this"),
          SessionHistoryMessage(role = MessageRole.ASSISTANT, content = "Need host proof."),
          SessionHistoryMessage(role = MessageRole.ASSISTANT, content = "Host verification result:\nPASS"),
        ),
      ),
    )
    val viewModel = testViewModel(api = api)

    viewModel.onDraftChanged("please verify this")
    viewModel.onSendClicked()
    runCurrent()

    viewModel.onDraftChanged("second")
    viewModel.onSendClicked()
    runCurrent()

    api.emitToken(0, "Need host proof.")
    api.finishRequest(0)
    runCurrent()

    assertEquals(1, api.snapshotCalls)
    assertEquals(
      listOf(
        SendRequest(threadId = "mbl", prompt = "please verify this", agentKey = "a"),
      ),
      api.streamRequests,
    )

    api.releaseSnapshot()
    runCurrent()

    val state = viewModel.uiState.value.agentStates.getValue(AgentTab.ALL)
    val assistantMessages = state.messages.filter { it.role == MessageRole.ASSISTANT }
    assertEquals(
      listOf(
        SendRequest(threadId = "mbl", prompt = "please verify this", agentKey = "a"),
        SendRequest(threadId = "mbl", prompt = "second", agentKey = "a"),
      ),
      api.streamRequests,
    )
    assertEquals(
      listOf("Need host proof.", "Host verification result:\nPASS"),
      assistantMessages.map { it.content }.takeLast(2),
    )

    api.finishRequest(1)
    advanceUntilIdle()
  }

  @Test
  fun startupRecoversActiveAssistantReplyFromSessionSnapshotAndFinishesWhenHistoryCatchesUp() = runTest(dispatcher) {
    val api = RecoverySnapshotChatApi(
      snapshots = mutableListOf(
        SessionSnapshot(
          history = listOf(
            SessionHistoryMessage(role = MessageRole.USER, content = "finish the analysis"),
          ),
          streamState = SessionStreamState(
            status = "streaming",
            content = "Working through",
            startedUtc = "2026-03-09T12:00:00Z",
          ),
        ),
        SessionSnapshot(
          history = listOf(
            SessionHistoryMessage(role = MessageRole.USER, content = "finish the analysis"),
            SessionHistoryMessage(role = MessageRole.ASSISTANT, content = "Working through the recovery now."),
          ),
          streamState = null,
        ),
      ),
    )
    val viewModel = testViewModel(api = api, startBackgroundWork = true)

    runCurrent()
    val recovering = viewModel.uiState.value.agentStates.getValue(AgentTab.ALL)
    assertTrue(recovering.isStreaming)
    assertEquals(2, recovering.messages.size)
    assertEquals(MessageStatus.STREAMING, recovering.messages.last().status)
    assertEquals("Working through", recovering.messages.last().content)
    assertFalse(recovering.hasUnread)

    advanceUntilIdle()

    val recovered = viewModel.uiState.value.agentStates.getValue(AgentTab.ALL)
    assertFalse(recovered.isStreaming)
    assertEquals(MessageStatus.COMPLETE, recovered.messages.last().status)
    assertEquals("Working through the recovery now.", recovered.messages.last().content)
  }

  @Test
  fun startupRecoveryKeepsRicherLocalInterruptedReplyWhenCanonicalStreamIsShorter() = runTest(dispatcher) {
    val prefs = InMemorySharedPreferences(
      mutableMapOf(
        "history_cache_json_a" to """
          [
            {"role":"user","content":"finish the analysis","status":"complete"},
            {"role":"assistant","content":"Working through the recovery now.","status":"interrupted"}
          ]
        """.trimIndent(),
        "last_failed_prompt_a" to "finish the analysis",
      ),
    )
    val api = RecoverySnapshotChatApi(
      snapshots = mutableListOf(
        SessionSnapshot(
          history = listOf(
            SessionHistoryMessage(role = MessageRole.USER, content = "finish the analysis"),
          ),
          streamState = SessionStreamState(
            status = "streaming",
            content = "Working through",
            startedUtc = "2026-03-09T12:00:00Z",
          ),
        ),
      ),
    )
    val viewModel = testViewModel(api = api, prefs = prefs, startBackgroundWork = true)

    runCurrent()

    val recovering = viewModel.uiState.value.agentStates.getValue(AgentTab.ALL)
    assertTrue(recovering.isStreaming)
    assertEquals(MessageStatus.STREAMING, recovering.messages.last().status)
    assertEquals("Working through the recovery now.", recovering.messages.last().content)
  }

  @Test
  fun startupDoesNotInventStreamingFromUnpairedHistoryOrPendingPrompt() = runTest(dispatcher) {
    val prefs = InMemorySharedPreferences(mutableMapOf("pending_prompt_a" to "finish the analysis"))
    val api = RecoverySnapshotChatApi(
      snapshots = mutableListOf(
        SessionSnapshot(
          history = listOf(
            SessionHistoryMessage(role = MessageRole.USER, content = "finish the analysis"),
          ),
          streamState = null,
        ),
      ),
    )
    val viewModel = testViewModel(api = api, prefs = prefs, startBackgroundWork = true)

    runCurrent()

    val restored = viewModel.uiState.value.agentStates.getValue(AgentTab.ALL)
    assertFalse(restored.isStreaming)
    assertFalse(restored.isSubmitting)
    assertEquals(1, restored.messages.size)
    assertEquals(MessageRole.USER, restored.messages.last().role)
    assertEquals("finish the analysis", restored.lastFailedPrompt)
  }

  @Test
  fun processDeathPreservesPendingTurnRequestIdentityAndResumeTurnId() = runTest(dispatcher) {
    val prefs = InMemorySharedPreferences()
    val initialApi = ControlledStreamChatApi()
    val initialViewModel = testViewModel(api = initialApi, prefs = prefs)

    initialViewModel.onTabSelected(AgentTab.ALL)
    initialViewModel.onDraftChanged("finish the analysis")
    initialViewModel.onSendClicked()
    runCurrent()

    initialApi.emitStart(0, "turn-mobile-restore-1")
    initialApi.emitProgress(0, "Thinking...")
    runCurrent()

    val firstRequest = initialApi.streamRequests.single()
    assertTrue(firstRequest.requestId.isNotBlank())
    assertEquals(firstRequest.requestId, firstRequest.idempotencyKey)
    releaseViewModelForTest(initialViewModel)

    val restoredApi = RecordingStreamChatApi()
    val restoredViewModel = testViewModel(api = restoredApi, prefs = prefs)
    val restoredState = restoredViewModel.uiState.value.agentStates.getValue(AgentTab.ALL)

    assertEquals("finish the analysis", restoredState.lastFailedPrompt)
    restoredViewModel.onRetryLastFailedClicked()
    runCurrent()

    val retriedRequest = restoredApi.streamRequests.single()
    assertEquals(firstRequest.requestId, retriedRequest.requestId)
    assertEquals(firstRequest.idempotencyKey, retriedRequest.idempotencyKey)
    assertEquals("turn-mobile-restore-1", retriedRequest.resumeTurnId)
  }

  @Test
  fun backgroundRecoveryDoesNotHideLocallySentUserMessageBeforeServerHistoryCatchesUp() = runTest(dispatcher) {
    val api = ControlledStreamWithSnapshotChatApi(
      snapshots = mutableListOf(
        SessionSnapshot(history = emptyList()),
        SessionSnapshot(
          history = emptyList(),
          streamState = SessionStreamState(
            status = "streaming",
            content = "",
            startedUtc = "2026-03-10T12:00:00Z",
          ),
        ),
      ),
    )
    val viewModel = testViewModel(api = api, startBackgroundWork = true)

    try {
      runCurrent()

      viewModel.onDraftChanged("please keep this visible")
      viewModel.onSendClicked()
      runCurrent()

      val sendingState = viewModel.uiState.value.agentStates.getValue(AgentTab.ALL)
      assertEquals(
        listOf(MessageRole.USER, MessageRole.ASSISTANT),
        sendingState.messages.map { it.role },
      )
      assertEquals("please keep this visible", sendingState.messages.first().content)

      advanceTimeBy(1000L)
      runCurrent()

      val recoveringState = viewModel.uiState.value.agentStates.getValue(AgentTab.ALL)
      assertTrue(recoveringState.isStreaming)
      assertTrue(
        recoveringState.messages.any { it.role == MessageRole.USER && it.content == "please keep this visible" },
      )
      assertEquals(MessageRole.ASSISTANT, recoveringState.messages.last().role)
      assertEquals(MessageStatus.STREAMING, recoveringState.messages.last().status)
    } finally {
      releaseViewModelForTest(viewModel)
    }
  }

  @Test
  fun specialistDirectChatRemainsVisibleWhileWorkflowMessagesContinueDuringRecovery() = runTest(dispatcher) {
    val prefs = InMemorySharedPreferences(
      mutableMapOf(
        "history_cache_json_b" to """
          [
            {
              "role":"workflow",
              "content":"Builder finished iteration 1.",
              "status":"complete",
              "label":"B",
              "workflow_id":"wf-123",
              "event_key":"wf-123|builder"
            }
          ]
        """.trimIndent(),
        "active_workflow_b" to "wf-123",
      ),
    )
    val api = ControlledStreamWithSnapshotChatApi(
      snapshots = mutableListOf(
        SessionSnapshot(history = emptyList()),
        SessionSnapshot(
          history = emptyList(),
          streamState = SessionStreamState(
            status = "streaming",
            content = "",
            startedUtc = "2026-03-10T12:00:00Z",
          ),
        ),
      ),
    )
    val viewModel = testViewModel(api = api, prefs = prefs, startBackgroundWork = true)

    try {
      runCurrent()

      viewModel.onTabSelected(AgentTab.BUILDER)
      viewModel.onDraftChanged("builder, please inspect the patch")
      viewModel.onSendClicked()
      runCurrent()

      advanceTimeBy(1000L)
      runCurrent()

      val state = viewModel.uiState.value.agentStates.getValue(AgentTab.BUILDER)
      assertTrue(state.isStreaming)
      assertTrue(
        state.messages.any { it.role == MessageRole.USER && it.content == "builder, please inspect the patch" },
      )
      assertTrue(
        state.messages.any { it.role == MessageRole.WORKFLOW && it.content.contains("Builder finished iteration 1.") },
      )
      assertTrue(
        state.messages.any { it.role == MessageRole.ASSISTANT && it.status == MessageStatus.STREAMING },
      )
    } finally {
      releaseViewModelForTest(viewModel)
    }
  }

  @Test
  fun orchestratorCompletedDirectChatSurvivesLaterWorkflowOnlyHydration() = runTest(dispatcher) {
    val prefs = InMemorySharedPreferences(
      mutableMapOf(
        "history_cache_json_o" to """
          [
            {
              "role":"workflow",
              "content":"WF-01 requested for PIS-123",
              "status":"complete",
              "label":"WF-01",
              "workflow_id":"wf-123",
              "event_key":"wf-123|start"
            }
          ]
        """.trimIndent(),
      ),
    )
    val api = ControlledStreamWithSnapshotChatApi(
      snapshots = mutableListOf(
        SessionSnapshot(
          history = listOf(
            SessionHistoryMessage(
              role = MessageRole.WORKFLOW,
              content = "WF-01 requested for PIS-123",
              status = MessageStatus.COMPLETE,
              label = "WF-01",
              workflowId = "wf-123",
              eventKey = "wf-123|start",
            ),
          ),
        ),
      ),
    )
    val viewModel = testViewModel(api = api, prefs = prefs)

    viewModel.onTabSelected(AgentTab.ORCHESTRATOR)
    viewModel.onDraftChanged("orchestrator, explain the current blocker")
    viewModel.onSendClicked()
    runCurrent()

    api.emitToken(0, "The current blocker is")
    api.emitToken(0, " hydration drift.")
    api.finishRequest(0)
    advanceUntilIdle()

    val afterReply = viewModel.uiState.value.agentStates.getValue(AgentTab.ORCHESTRATOR)
    assertTrue(afterReply.messages.any { it.role == MessageRole.USER && it.content == "orchestrator, explain the current blocker" })
    assertTrue(afterReply.messages.any { it.role == MessageRole.ASSISTANT && it.content == "The current blocker is hydration drift." })
    assertTrue(afterReply.messages.any { it.role == MessageRole.WORKFLOW && it.content.contains("WF-01 requested") })

    runCurrent()

    val afterHydration = viewModel.uiState.value.agentStates.getValue(AgentTab.ORCHESTRATOR)
    assertTrue(afterHydration.messages.any { it.role == MessageRole.USER && it.content == "orchestrator, explain the current blocker" })
    assertTrue(afterHydration.messages.any { it.role == MessageRole.ASSISTANT && it.content == "The current blocker is hydration drift." })
    assertTrue(afterHydration.messages.any { it.role == MessageRole.WORKFLOW && it.content.contains("WF-01 requested") })
  }

  @Test
  fun terminalDirectChatSyncKeepsLocalReplyWhileMergingWorkflowSnapshotMessages() = runTest(dispatcher) {
    val api = ControlledStreamWithSnapshotChatApi(
      snapshots = mutableListOf(
        SessionSnapshot(
          history = listOf(
            SessionHistoryMessage(
              role = MessageRole.WORKFLOW,
              content = "WF-01 requested for PIS-1476",
              status = MessageStatus.COMPLETE,
              label = "WF-01",
              workflowId = "wf-1476",
              eventKey = "wf-1476|start",
            ),
          ),
        ),
      ),
    )
    val viewModel = testViewModel(api = api)

    viewModel.onTabSelected(AgentTab.ORCHESTRATOR)
    viewModel.onDraftChanged("orchestrator, explain the blocker")
    viewModel.onSendClicked()
    runCurrent()

    api.emitToken(0, "The blocker is hydration drift.")
    api.finishRequest(0)
    advanceUntilIdle()

    val state = viewModel.uiState.value.agentStates.getValue(AgentTab.ORCHESTRATOR)
    assertEquals(1, api.snapshotCalls)
    assertTrue(state.messages.any { it.role == MessageRole.USER && it.content == "orchestrator, explain the blocker" })
    assertTrue(
      state.messages.any {
        it.role == MessageRole.ASSISTANT &&
          it.content == "The blocker is hydration drift."
      },
    )
    assertTrue(
      state.messages.any {
        it.role == MessageRole.WORKFLOW &&
          it.workflowId == "wf-1476" &&
          it.content == "WF-01 requested for PIS-1476"
      },
    )
    assertEquals(MessageRole.WORKFLOW, state.messages.last().role)
  }

  @Test
  fun queuedHeadHealthFailureStillStartsNextSend() = runTest(dispatcher) {
    val api = ToggleHealthyControlledStreamChatApi()
    val viewModel = testViewModel(api = api)

    viewModel.onTabSelected(AgentTab.ALL)
    viewModel.onDraftChanged("first")
    viewModel.onSendClicked()
    runCurrent()

    viewModel.onDraftChanged("second")
    viewModel.onSendClicked()
    runCurrent()

    api.healthy = false
    api.finishRequest(0)
    advanceUntilIdle()

    val continuedState = viewModel.uiState.value.agentStates.getValue(AgentTab.ALL)
    assertTrue(continuedState.queuedPrompts.isEmpty())
    assertEquals(2, api.streamRequests.size)
    assertEquals("second", api.streamRequests.last().prompt)

    api.finishRequest(1)
    runCurrent()

    val completedState = viewModel.uiState.value.agentStates.getValue(AgentTab.ALL)
    assertFalse(completedState.isSubmitting)
    assertFalse(completedState.isStreaming)
    assertEquals(
      listOf(
        SendRequest(threadId = "mbl", prompt = "first", agentKey = "a"),
        SendRequest(threadId = "mbl", prompt = "second", agentKey = "a"),
      ),
      api.streamRequests,
    )
  }

  @Test
  fun sendContinuesWhenLocalNetworkCheckFailsButRecentServerContactExists() = runTest(dispatcher) {
    val api = ToggleHealthyControlledStreamChatApi()
    val viewModel = testViewModel(
      api = api,
      startBackgroundWork = true,
      enableHeartbeatPolling = true,
      networkChecker = { false },
    )

    advanceUntilIdle()
    assertTrue(viewModel.uiState.value.serverHealthy)

    viewModel.onDraftChanged("send despite flaky local network state")
    viewModel.onSendClicked()
    runCurrent()

    assertEquals(1, api.streamRequests.size)
    assertEquals("send despite flaky local network state", api.streamRequests.single().prompt)
  }

  @Test
  fun retryLastFailedSendPreservesOriginalAttachedDocumentSnapshot() = runTest(dispatcher) {
    val api = ControlledStreamChatApi()
    val viewModel = testViewModel(api = api)

    viewModel.onTabSelected(AgentTab.ALL)
    viewModel.addAttachedDocument(
      filename = "audit.md",
      mimeType = "text/markdown",
      text = "# Audit\n\nFirst attached context.",
      truncated = false,
    )
    viewModel.onDraftChanged("summarize it")
    viewModel.onSendClicked()
    runCurrent()

    api.failRequest(0, "temporary failure")
    advanceUntilIdle()

    val failedState = viewModel.uiState.value.agentStates.getValue(AgentTab.ALL)
    assertEquals("summarize it", failedState.lastFailedPrompt)

    val originalEffectivePrompt = buildPromptWithAttachedDocuments(
      prompt = "summarize it",
      documents = failedState.attachedDocuments,
    )
    assertEquals(originalEffectivePrompt, api.streamRequests[0].prompt)

    viewModel.removeAttachedDocument(failedState.attachedDocuments.single().id)
    viewModel.addAttachedDocument(
      filename = "replacement.md",
      mimeType = "text/markdown",
      text = "# Replacement\n\nDifferent attached context.",
      truncated = false,
    )
    runCurrent()

    viewModel.onRetryLastFailedClicked()
    runCurrent()

    assertEquals(2, api.streamRequests.size)
    assertEquals(originalEffectivePrompt, api.streamRequests[1].prompt)
    assertFalse(api.streamRequests[1].prompt.contains("Different attached context."))
  }

  @Test
  fun queueStopsAtFiveAndRejectsSixthPrompt() = runTest(dispatcher) {
    val api = ControlledStreamChatApi()
    val viewModel = testViewModel(api = api)

    viewModel.onDraftChanged("first")
    viewModel.onSendClicked()
    runCurrent()

    repeat(AGENT_PROMPT_QUEUE_MAX_DEPTH) { idx ->
      viewModel.onDraftChanged("queued-${idx + 1}")
      viewModel.onSendClicked()
    }
    runCurrent()

    viewModel.onDraftChanged("queued-6")
    viewModel.onSendClicked()
    runCurrent()

    val state = viewModel.uiState.value.agentStates.getValue(AgentTab.ALL)
    assertEquals(AGENT_PROMPT_QUEUE_MAX_DEPTH, state.queuedPrompts.size)
    assertEquals("queued-5", state.queuedPrompts.last())
    assertTrue(state.lastError.orEmpty().contains("Queue full"))
    assertEquals(1, api.streamRequests.size)
  }

  @Test
  fun secondTapDuringPreflightDoesNotQueueOrDuplicateSend() = runTest(dispatcher) {
    val api = BlockingPreflightChatApi()
    val viewModel = testViewModel(api = api)

    viewModel.onDraftChanged("hello")
    viewModel.onSendClicked()
    viewModel.onSendClicked()
    runCurrent()

    val waitingState = viewModel.uiState.value.agentStates.getValue(AgentTab.ALL)
    assertTrue(waitingState.isSubmitting)
    assertEquals("", waitingState.draft)
    assertTrue(waitingState.queuedPrompts.isEmpty())
    assertTrue(api.streamRequests.isEmpty())
    assertEquals(
      listOf(MessageRole.USER, MessageRole.ASSISTANT),
      waitingState.messages.map { it.role },
    )
    assertEquals(MessageStatus.SENDING, waitingState.messages.first().status)
    assertEquals("hello", waitingState.messages.first().content)
    assertEquals(MessageStatus.STREAMING, waitingState.messages.last().status)

    api.releasePreflight()
    advanceUntilIdle()

    assertEquals(
      listOf(SendRequest(threadId = "mbl", prompt = "hello", agentKey = "a")),
      api.streamRequests,
    )
    assertTrue(viewModel.uiState.value.agentStates.getValue(AgentTab.ALL).queuedPrompts.isEmpty())
  }

  @Test
  fun stopDuringPreflightCancelsPendingDispatch() = runTest(dispatcher) {
    val api = BlockingPreflightChatApi()
    val viewModel = testViewModel(api = api)

    viewModel.onDraftChanged("hello")
    viewModel.onSendClicked()
    runCurrent()
    assertTrue(viewModel.uiState.value.agentStates.getValue(AgentTab.ALL).isSubmitting)

    viewModel.onStopClicked()
    runCurrent()

    val state = viewModel.uiState.value.agentStates.getValue(AgentTab.ALL)
    assertTrue(state.queuedPrompts.isEmpty())
    assertEquals(listOf("mbl"), api.cancelCalls)
    assertTrue(api.streamRequests.isEmpty())
    assertTrue(state.messages.isEmpty())
  }

  @Test
  fun stopDuringActiveStreamCancelsServerAndKeepsCancelledTerminalState() = runTest(dispatcher) {
    val api = CancelRecordingControlledStreamChatApi()
    val viewModel = testViewModel(api = api)

    viewModel.onDraftChanged("hello")
    viewModel.onSendClicked()
    runCurrent()
    api.emitToken(0, "partial")
    runCurrent()

    viewModel.onStopClicked()
    runCurrent()

    val state = viewModel.uiState.value.agentStates.getValue(AgentTab.ALL)
    val assistant = state.messages.last { it.role == MessageRole.ASSISTANT }
    assertEquals(listOf("mbl"), api.cancelCalls)
    assertEquals(MessageStatus.CANCELLED, assistant.status)
    assertEquals("partial", assistant.content)
    assertEquals(null, state.lastError)
  }

  @Test
  fun stopDuringActiveStreamShowsClearErrorWhenRemoteCancelFails() = runTest(dispatcher) {
    val api = CancelFailingControlledStreamChatApi(
      error = ChatRemoteCancelFailedException(
        "Local rendering stopped, but server cancellation may not have succeeded. Refresh to confirm the final server state.",
      ),
    )
    val viewModel = testViewModel(api = api)

    viewModel.onDraftChanged("hello")
    viewModel.onSendClicked()
    runCurrent()
    api.emitToken(0, "partial")
    runCurrent()

    viewModel.onStopClicked()
    runCurrent()

    val state = viewModel.uiState.value.agentStates.getValue(AgentTab.ALL)
    val assistant = state.messages.last { it.role == MessageRole.ASSISTANT }
    assertEquals(listOf("mbl"), api.cancelCalls)
    assertEquals(MessageStatus.INTERRUPTED, assistant.status)
    assertEquals("partial", assistant.content)
    assertTrue(state.lastError.orEmpty().contains("server cancellation may not have succeeded"))
  }

  @Test
  fun stopDuringActiveStreamTreatsCancelRequestedAsUncertainRace() = runTest(dispatcher) {
    val api = CancelFailingControlledStreamChatApi(
      error = ChatRemoteCancelPendingException(
        "Local rendering stopped, but the server is still resolving the cancel request. The reply may still complete; refresh to confirm the final server state.",
      ),
    )
    val viewModel = testViewModel(api = api)

    viewModel.onDraftChanged("hello")
    viewModel.onSendClicked()
    runCurrent()
    api.emitToken(0, "partial")
    runCurrent()

    viewModel.onStopClicked()
    runCurrent()

    val state = viewModel.uiState.value.agentStates.getValue(AgentTab.ALL)
    val assistant = state.messages.last { it.role == MessageRole.ASSISTANT }
    assertEquals(listOf("mbl"), api.cancelCalls)
    assertEquals(MessageStatus.INTERRUPTED, assistant.status)
    assertEquals("partial", assistant.content)
    assertTrue(state.lastError.orEmpty().contains("still resolving the cancel request"))
    assertTrue(state.lastError.orEmpty().contains("may still complete"))
  }

  @Test
  fun startupRecoveryKeepsStopRaceTurnInterruptedAcrossHydration() = runTest(dispatcher) {
    val prefs = InMemorySharedPreferences(
      mutableMapOf(
        "history_cache_json_a" to """
          [
            {"role":"user","content":"hello","status":"complete"},
            {"role":"assistant","content":"partial","status":"interrupted"}
          ]
        """.trimIndent(),
      ),
    )
    val api = RecoverySnapshotChatApi(
      snapshots = mutableListOf(
        SessionSnapshot(
          history = listOf(
            SessionHistoryMessage(role = MessageRole.USER, content = "hello", status = MessageStatus.COMPLETE),
            SessionHistoryMessage(
              role = MessageRole.ASSISTANT,
              content = "partial complete",
              status = MessageStatus.INTERRUPTED,
              label = DIRECT_CHAT_STOP_RACE_LABEL,
            ),
          ),
        ),
      ),
    )
    val viewModel = testViewModel(api = api, prefs = prefs, startBackgroundWork = true)

    runCurrent()
    advanceUntilIdle()

    val state = viewModel.uiState.value.agentStates.getValue(AgentTab.ALL)
    val assistant = state.messages.last { it.role == MessageRole.ASSISTANT }
    assertEquals(MessageStatus.INTERRUPTED, assistant.status)
    assertEquals("partial complete", assistant.content)
    assertEquals(DIRECT_CHAT_STOP_RACE_LABEL, assistant.label)
    assertNull(state.lastFailedPrompt)
    assertTrue(prefs.getString("history_cache_json_a", "")!!.contains("\"label\":\"$DIRECT_CHAT_STOP_RACE_LABEL\""))
    assertTrue(prefs.getString("history_cache_json_a", "")!!.contains("\"status\":\"interrupted\""))
    assertTrue(api.snapshotCalls >= 1)
  }

  @Test
  fun sentPromptIsVisibleImmediatelyDuringPreflight() = runTest(dispatcher) {
    val api = BlockingPreflightChatApi()
    val viewModel = testViewModel(api = api)

    viewModel.onDraftChanged("show this immediately")
    viewModel.onSendClicked()
    runCurrent()

    val waitingState = viewModel.uiState.value.agentStates.getValue(AgentTab.ALL)
    assertTrue(waitingState.isSubmitting)
    assertEquals(
      listOf(MessageRole.USER, MessageRole.ASSISTANT),
      waitingState.messages.map { it.role },
    )
    assertEquals("show this immediately", waitingState.messages.first().content)
    assertEquals(MessageStatus.SENDING, waitingState.messages.first().status)
    assertEquals(MessageStatus.STREAMING, waitingState.messages.last().status)

    api.releasePreflight()
    advanceUntilIdle()

    val streamingState = viewModel.uiState.value.agentStates.getValue(AgentTab.ALL)
    assertEquals(
      listOf(MessageRole.USER, MessageRole.ASSISTANT),
      streamingState.messages.map { it.role },
    )
    assertEquals(MessageStatus.COMPLETE, streamingState.messages.first().status)
    assertEquals(MessageStatus.STREAMING, streamingState.messages.last().status)
  }

  @Test
  fun startupHydrationSkipsTabsThatAlreadyHaveLocalDirectHistory() = runTest(dispatcher) {
    val prefs = InMemorySharedPreferences(
      mutableMapOf(
        "history_cache_json_a" to """
          [
            {"role":"user","content":"keep local history","status":"complete"},
            {"role":"assistant","content":"local answer","status":"complete"}
          ]
        """.trimIndent(),
      ),
    )
    val api = ControlledStreamWithSnapshotChatApi(
      snapshots = mutableListOf(
        SessionSnapshot(
          history = listOf(
            SessionHistoryMessage(
              role = MessageRole.WORKFLOW,
              content = "stale remote replacement",
              status = MessageStatus.COMPLETE,
              label = "WF-01",
              workflowId = "wf-remote",
              eventKey = "wf-remote|1",
            ),
          ),
        ),
      ),
    )

    val viewModel = testViewModel(api = api, prefs = prefs, startBackgroundWork = true)
    runCurrent()

    val state = viewModel.uiState.value.agentStates.getValue(AgentTab.ALL)
    assertTrue(state.messages.any { it.role == MessageRole.USER && it.content == "keep local history" })
    assertTrue(state.messages.any { it.role == MessageRole.ASSISTANT && it.content == "local answer" })
    assertEquals(0, api.snapshotCalls)
  }

  @Test
  fun startupHydrationRetryStopsAfterLocalDirectHistoryAppears() = runTest(dispatcher) {
    val api = ControlledStreamWithSnapshotChatApi(
      snapshots = mutableListOf(
        SessionSnapshot(history = emptyList()),
        SessionSnapshot(
          history = listOf(
            SessionHistoryMessage(
              role = MessageRole.WORKFLOW,
              content = "stale remote replacement",
              status = MessageStatus.COMPLETE,
              label = "WF-01",
              workflowId = "wf-remote",
              eventKey = "wf-remote|1",
            ),
          ),
        ),
      ),
    )
    val viewModel = testViewModel(api = api, startBackgroundWork = true)

    try {
      runCurrent()
      assertEquals(1, api.snapshotCalls)

      viewModel.onDraftChanged("keep this local")
      viewModel.onSendClicked()
      runCurrent()

      advanceTimeBy(1500L)
      runCurrent()

      val state = viewModel.uiState.value.agentStates.getValue(AgentTab.ALL)
      assertTrue(state.messages.any { it.role == MessageRole.USER && it.content == "keep this local" })
      assertFalse(state.messages.any { it.role == MessageRole.WORKFLOW && it.content == "stale remote replacement" })
      assertEquals(1, api.snapshotCalls)
    } finally {
      releaseViewModelForTest(viewModel)
    }
  }

  @Test
  fun snapshotDoesNotClearInterruptedRetryFromDifferentAssistantTurnWithSamePrefix() = runTest(dispatcher) {
    val prefs = InMemorySharedPreferences(
      mutableMapOf(
        "history_cache_json_a" to """
          [
            {"role":"user","content":"question one","status":"complete"},
            {"role":"assistant","content":"Need host proof.","status":"complete"},
            {"role":"user","content":"question two","status":"complete"},
            {"role":"assistant","content":"Need host","status":"interrupted"}
          ]
        """.trimIndent(),
        "last_failed_prompt_a" to "question two",
        "queued_prompts_a" to """["follow up"]""",
      ),
    )
    val api = ControlledStreamWithSnapshotChatApi(
      snapshots = mutableListOf(
        SessionSnapshot(
          history = listOf(
            SessionHistoryMessage(role = MessageRole.USER, content = "question one", status = MessageStatus.COMPLETE),
            SessionHistoryMessage(role = MessageRole.ASSISTANT, content = "Need host proof.", status = MessageStatus.COMPLETE),
            SessionHistoryMessage(role = MessageRole.USER, content = "question two", status = MessageStatus.COMPLETE),
            SessionHistoryMessage(role = MessageRole.ASSISTANT, content = "Still waiting.", status = MessageStatus.COMPLETE),
          ),
        ),
      ),
    )
    val viewModel = testViewModel(api = api, prefs = prefs, startBackgroundWork = true)

    advanceUntilIdle()

    val state = viewModel.uiState.value.agentStates.getValue(AgentTab.ALL)
    assertEquals("question two", state.lastFailedPrompt)
    assertEquals(listOf("follow up"), state.queuedPrompts)
    assertTrue(
      state.messages.any {
        it.role == MessageRole.ASSISTANT &&
          it.status == MessageStatus.INTERRUPTED &&
          it.content == "Need host"
      },
    )
  }

  @Test
  fun activeWorkflowDoesNotTriggerHydrationWhenLocalDirectHistoryAlreadyExists() = runTest(dispatcher) {
    val prefs = InMemorySharedPreferences(
      mutableMapOf(
        "history_cache_json_o" to """
          [
            {"role":"user","content":"where is my orchestrator answer","status":"complete"},
            {"role":"assistant","content":"still here locally","status":"complete"},
            {"role":"workflow","content":"WF still running","status":"complete","label":"WF-01","workflow_id":"wf-live","event_key":"wf-live|1"}
          ]
        """.trimIndent(),
        "active_workflow_o" to "wf-live",
      ),
    )
    val api = ControlledStreamWithSnapshotChatApi(
      snapshots = mutableListOf(
        SessionSnapshot(
          history = listOf(
            SessionHistoryMessage(role = MessageRole.USER, content = "stale remote only", status = MessageStatus.COMPLETE),
          ),
        ),
      ),
    )

    val viewModel = testViewModel(api = api, prefs = prefs, startBackgroundWork = true)
    runCurrent()

    val state = viewModel.uiState.value.agentStates.getValue(AgentTab.ORCHESTRATOR)
    assertTrue(state.messages.any { it.role == MessageRole.USER && it.content == "where is my orchestrator answer" })
    assertTrue(state.messages.any { it.role == MessageRole.ASSISTANT && it.content == "still here locally" })
    assertEquals(0, api.snapshotCalls)
  }

  @Test
  fun staleShorterRemoteHistoryDoesNotReplaceRicherLocalDirectConversation() = runTest(dispatcher) {
    val prefs = InMemorySharedPreferences(
      mutableMapOf(
        "pending_prompt_a" to "recover if needed",
        "history_cache_json_a" to """
          [
            {"role":"user","content":"local user turn","status":"complete"},
            {"role":"assistant","content":"local assistant reply","status":"complete"}
          ]
        """.trimIndent(),
      ),
    )
    val api = ControlledStreamWithSnapshotChatApi(
      snapshots = mutableListOf(
        SessionSnapshot(
          history = listOf(
            SessionHistoryMessage(role = MessageRole.USER, content = "local user turn", status = MessageStatus.COMPLETE),
          ),
        ),
      ),
    )

    val viewModel = testViewModel(api = api, prefs = prefs, startBackgroundWork = true)
    advanceUntilIdle()

    val state = viewModel.uiState.value.agentStates.getValue(AgentTab.ALL)
    assertTrue(state.messages.any { it.role == MessageRole.USER && it.content == "local user turn" })
    assertTrue(state.messages.any { it.role == MessageRole.ASSISTANT && it.content == "local assistant reply" })
    assertFalse(state.messages.any { it.role == MessageRole.USER && it.content == "recover if needed" })
    assertEquals(1, api.snapshotCalls)
  }

  @Test
  fun capDialogSuggestsCapAboveCurrentTurnCount() = runTest(dispatcher) {
    val api = object : FakeChatApi() {
      override suspend fun getSessionStats(threadId: String): SessionStats {
        return SessionStats(
          threadId = threadId,
          maxTurns = 7593,
          turns = 19643,
          messages = 34167,
          chars = 27927284,
          lines = 375126,
          atCap = true,
          willCycleNext = true,
          storagePath = "",
          storageFileBytes = 0,
        )
      }
    }
    val viewModel = testViewModel(api = api)

    viewModel.onDraftChanged("send anyway")
    viewModel.onSendClicked()
    advanceUntilIdle()

    val state = viewModel.uiState.value.agentStates.getValue(AgentTab.ALL)
    val dialog = state.capDialog
    assertTrue(dialog != null)
    assertEquals(20143, dialog?.suggestedNewCap)
    assertTrue((dialog?.suggestedNewCap ?: 0) > dialog!!.stats.turns)
    assertEquals("send anyway", state.draft)
  }

  @Test
  fun capIncreaseKeepsDialogOpenWhenServerStillReportsAtCap() = runTest(dispatcher) {
    val requestedCaps = mutableListOf<Int>()
    val api = object : FakeChatApi() {
      override suspend fun getSessionStats(threadId: String): SessionStats {
        return SessionStats(
          threadId = threadId,
          maxTurns = 7593,
          turns = 19643,
          messages = 34167,
          chars = 27927284,
          lines = 375126,
          atCap = true,
          willCycleNext = true,
          storagePath = "",
          storageFileBytes = 0,
        )
      }

      override suspend fun setSessionCap(threadId: String, maxTurns: Int): SessionStats {
        requestedCaps += maxTurns
        return SessionStats(
          threadId = threadId,
          maxTurns = maxTurns,
          turns = 20500,
          messages = 35000,
          chars = 28000000,
          lines = 380000,
          atCap = true,
          willCycleNext = true,
          storagePath = "",
          storageFileBytes = 0,
        )
      }
    }
    val viewModel = testViewModel(api = api)

    viewModel.onDraftChanged("send anyway")
    viewModel.onSendClicked()
    advanceUntilIdle()
    viewModel.onCapIncreaseClicked()
    advanceUntilIdle()

    val state = viewModel.uiState.value.agentStates.getValue(AgentTab.ALL)
    val dialog = state.capDialog
    assertEquals(listOf(20143), requestedCaps)
    assertTrue(dialog != null)
    assertEquals(21000, dialog?.suggestedNewCap)
    assertEquals(20500, dialog?.stats?.turns)
    assertEquals(
      "Memory cap is still below the current turn count. Increase again or continue with cycling.",
      state.lastError,
    )
    assertEquals("send anyway", state.draft)
  }

  @Test
  fun queuedPromptsRestoreFromPreferencesPerAgent() = runTest(dispatcher) {
    val prefs = InMemorySharedPreferences(
      mutableMapOf(
        "queued_prompts_a" to """["one","two"]""",
        "queued_prompts_b" to """["build next"]""",
      ),
    )
    val viewModel = testViewModel(prefs = prefs)

    assertEquals(
      listOf("one", "two"),
      viewModel.uiState.value.agentStates.getValue(AgentTab.ALL).queuedPrompts,
    )
    assertEquals(
      listOf("build next"),
      viewModel.uiState.value.agentStates.getValue(AgentTab.BUILDER).queuedPrompts,
    )
  }

  private open class FakeChatApi : ChatApi {
    override fun stream(request: SendRequest): Flow<StreamEvent> = emptyFlow()
    override suspend fun cancel(threadId: String) = Unit
    override suspend fun isServerHealthy(): Boolean = true
    override suspend fun getSessionStats(threadId: String): SessionStats {
      return SessionStats(
        threadId = threadId,
        maxTurns = 20000,
        turns = 0,
        messages = 0,
        chars = 0,
        lines = 0,
        atCap = false,
        willCycleNext = false,
        storagePath = "",
        storageFileBytes = 0,
      )
    }

    override suspend fun getSessionSnapshot(threadId: String): SessionSnapshot {
      return SessionSnapshot(history = getSessionHistory(threadId))
    }

    override suspend fun getSessionHistory(threadId: String): List<SessionHistoryMessage> = emptyList()
    override suspend fun setSessionCap(threadId: String, maxTurns: Int): SessionStats = getSessionStats(threadId)
    override suspend fun startWorkflow(request: WorkflowStartRequest): WorkflowStartResponse = WorkflowStartResponse(
      workflowId = "wf-123",
      phaseLabel = "WF-01",
      objective = request.objective,
      maxIterations = request.maxIterations ?: 0,
    )
    override suspend fun runWorkflow(workflowId: String, maxSteps: Int) = Unit
    override suspend fun getWorkflowStatus(workflowId: String, eventsLimit: Int): WorkflowStatus {
      return WorkflowStatus(
        workflowId = workflowId,
        status = "running",
        turn = "builder_run",
        iteration = 1,
        maxIterations = 3,
        phaseLabel = "WF-01",
        objective = "Implement PIS-006",
        verifierVerdict = "",
        lastError = "",
        intentValidationReport = "",
        events = listOf(
          WorkflowEvent(
            utc = "2026-03-07T12:00:00Z",
            event = "builder_run_started",
            payload = emptyMap(),
          ),
        ),
      )
    }

    override suspend fun cancelWorkflow(workflowId: String, reason: String) = Unit
  }

  private class RecordingChatApi : FakeChatApi() {
    val started = mutableListOf<WorkflowStartRequest>()
    var startResponse = WorkflowStartResponse(
      workflowId = "wf-123",
      phaseLabel = "WF-01",
      objective = "Implement and verify PIS-20260307-151515Z-AAAA with WF-01.",
      maxIterations = 4,
    )

    override suspend fun startWorkflow(request: WorkflowStartRequest): WorkflowStartResponse {
      started += request
      return startResponse
    }
  }

  private class RecordingStreamChatApi : FakeChatApi() {
    val streamRequests = mutableListOf<SendRequest>()

    override fun stream(request: SendRequest): Flow<StreamEvent> {
      streamRequests += request
      return emptyFlow()
    }
  }

  private open class ControlledStreamChatApi : FakeChatApi() {
    val streamRequests = mutableListOf<SendRequest>()
    private val channels = mutableListOf<Channel<StreamEvent>>()

    override fun stream(request: SendRequest): Flow<StreamEvent> {
      streamRequests += request
      val channel = Channel<StreamEvent>(capacity = Channel.UNLIMITED)
      channels += channel
      return flow {
        for (event in channel) {
          emit(event)
        }
      }
    }

    fun emitProgress(index: Int, text: String) {
      val channel = channels[index]
      check(channel.trySend(StreamEvent.Progress(text)).isSuccess)
    }

    fun emitStart(index: Int, turnId: String) {
      val channel = channels[index]
      check(channel.trySend(StreamEvent.Start(turnId)).isSuccess)
    }

    fun emitProgress(index: Int, text: String, phase: StreamPhase?) {
      val channel = channels[index]
      check(channel.trySend(StreamEvent.Progress(text, phase = phase)).isSuccess)
    }

    fun emitToken(index: Int, token: String) {
      val channel = channels[index]
      check(channel.trySend(StreamEvent.Token(token)).isSuccess)
    }

    fun failRequest(index: Int, reason: String) {
      val channel = channels[index]
      check(channel.trySend(StreamEvent.Error(reason)).isSuccess)
      channel.close()
    }

    fun finishRequest(index: Int, tokens: List<String> = emptyList(), title: String? = null) {
      val channel = channels[index]
      tokens.forEach { token -> check(channel.trySend(StreamEvent.Token(token)).isSuccess) }
      title?.let { check(channel.trySend(StreamEvent.Title(it)).isSuccess) }
      check(channel.trySend(StreamEvent.Done).isSuccess)
      channel.close()
    }
  }

  private class CancelRecordingControlledStreamChatApi : ControlledStreamChatApi() {
    val cancelCalls = mutableListOf<String>()

    override suspend fun cancel(threadId: String) {
      cancelCalls += threadId
    }
  }

  private class CancelFailingControlledStreamChatApi(
    private val error: Throwable,
  ) : ControlledStreamChatApi() {
    val cancelCalls = mutableListOf<String>()

    override suspend fun cancel(threadId: String) {
      cancelCalls += threadId
      throw error
    }
  }

  private class ToggleHealthyControlledStreamChatApi : ControlledStreamChatApi() {
    var healthy: Boolean = true

    override suspend fun isServerHealthy(): Boolean = healthy

    override suspend fun serverHealthProbe(): ServerHealthProbe {
      return ServerHealthProbe(
        isHealthy = healthy,
        baseUrl = "http://test-server:8787",
        detail = if (healthy) "" else "health probe timeout",
      )
    }
  }

  private class ControlledStreamWithSnapshotChatApi(
    private val snapshots: MutableList<SessionSnapshot>,
  ) : ControlledStreamChatApi() {
    var snapshotCalls: Int = 0
      private set

    override suspend fun getSessionSnapshot(threadId: String): SessionSnapshot {
      snapshotCalls += 1
      return when {
        snapshots.isEmpty() -> SessionSnapshot(history = emptyList())
        snapshotCalls <= snapshots.size -> snapshots[snapshotCalls - 1]
        else -> snapshots.last()
      }
    }
  }

  private class BlockingSnapshotChatApi(
    private val snapshot: SessionSnapshot,
  ) : ControlledStreamChatApi() {
    var snapshotCalls: Int = 0
      private set
    private val snapshotGate = Channel<Unit>(capacity = 1)

    override suspend fun getSessionSnapshot(threadId: String): SessionSnapshot {
      snapshotCalls += 1
      snapshotGate.receive()
      return snapshot
    }

    fun releaseSnapshot() {
      check(snapshotGate.trySend(Unit).isSuccess)
    }
  }

  private class BlockingPreflightChatApi : FakeChatApi() {
    val streamRequests = mutableListOf<SendRequest>()
    val cancelCalls = mutableListOf<String>()
    private val preflightGate = Channel<Unit>(capacity = 1)

    override suspend fun isServerHealthy(): Boolean {
      preflightGate.receive()
      return true
    }

    override fun stream(request: SendRequest): Flow<StreamEvent> {
      streamRequests += request
      return emptyFlow()
    }

    override suspend fun cancel(threadId: String) {
      cancelCalls += threadId
    }

    fun releasePreflight() {
      check(preflightGate.trySend(Unit).isSuccess)
    }
  }

  private class HistoryChatApi(
    private val history: List<SessionHistoryMessage>,
  ) : FakeChatApi() {
    override suspend fun getSessionHistory(threadId: String): List<SessionHistoryMessage> = history
  }

  private class RecoverySnapshotChatApi(
    private val snapshots: MutableList<SessionSnapshot>,
  ) : FakeChatApi() {
    var snapshotCalls: Int = 0
      private set

    override suspend fun getSessionSnapshot(threadId: String): SessionSnapshot {
      snapshotCalls += 1
      return when {
        snapshots.isEmpty() -> SessionSnapshot(history = emptyList())
        snapshotCalls <= snapshots.size -> snapshots[snapshotCalls - 1]
        else -> snapshots.last()
      }
    }
  }

  private class SnapshotFailureChatApi : FakeChatApi() {
    override suspend fun getSessionSnapshot(threadId: String): SessionSnapshot {
      throw IllegalStateException("session snapshot failed: HTTP 503")
    }
  }

  private class RestoredWorkflowResumeChatApi(
    private val statuses: MutableList<WorkflowStatus>,
  ) : FakeChatApi() {
    val runWorkflowCalls = mutableListOf<String>()
    var statusCalls: Int = 0
      private set

    override suspend fun runWorkflow(workflowId: String, maxSteps: Int) {
      runWorkflowCalls += workflowId
    }

    override suspend fun getWorkflowStatus(workflowId: String, eventsLimit: Int): WorkflowStatus {
      statusCalls += 1
      return when {
        statuses.isEmpty() -> super.getWorkflowStatus(workflowId, eventsLimit)
        statusCalls <= statuses.size -> statuses[statusCalls - 1]
        else -> statuses.last()
      }
    }
  }

  private fun runningWorkflowStatus(workflowId: String): WorkflowStatus {
    return WorkflowStatus(
      workflowId = workflowId,
      status = "running",
      turn = "builder_run",
      iteration = 2,
      maxIterations = 4,
      phaseLabel = "WF-01",
      objective = "Implement and verify PIS-20260310-114704Z-DDD3 with WF-01.",
      verifierVerdict = "",
      lastError = "",
      intentValidationReport = "",
      events = emptyList(),
    )
  }

  private class CancelWorkflowChatApi(
    private val status: WorkflowStatus,
  ) : FakeChatApi() {
    val cancelCalls = mutableListOf<String>()

    override suspend fun cancelWorkflow(workflowId: String, reason: String) {
      cancelCalls += "$workflowId|$reason"
    }

    override suspend fun getWorkflowStatus(workflowId: String, eventsLimit: Int): WorkflowStatus = status
  }

  private class MissingWorkflowChatApi : FakeChatApi() {
    var runCalls: Int = 0
      private set

    override suspend fun runWorkflow(workflowId: String, maxSteps: Int) {
      runCalls += 1
      throw IllegalStateException("workflow run failed: HTTP 404")
    }

    override suspend fun getWorkflowStatus(workflowId: String, eventsLimit: Int): WorkflowStatus {
      throw IllegalStateException("workflow status failed: HTTP 404")
    }
  }

  private class FakeLedgerItemsSource(
    private val snapshots: MutableList<List<LedgerItemSummary>>,
  ) : LedgerItemsSource {
    var calls: Int = 0
      private set

    override suspend fun listLedgerItems(limit: Int): List<LedgerItemSummary> {
      calls += 1
      return when {
        snapshots.isEmpty() -> emptyList()
        calls <= snapshots.size -> snapshots[calls - 1]
        else -> snapshots.last()
      }
    }
  }

  private class FakeMobileRuntimePolicySource(
    currentPolicy: MobileRuntimePolicy = MobileRuntimePolicy(
      chatTurnTimeoutS = 900,
      chatIdleTimeoutS = 120,
      workflowTimeoutS = 7000,
      fetchedAtMs = 0L,
    ),
    var refreshedPolicy: MobileRuntimePolicy? = null,
  ) : MobileRuntimePolicySource {
    var currentPolicy: MobileRuntimePolicy = currentPolicy
      private set
    var refreshCalls: Int = 0
      private set

    override fun current(): MobileRuntimePolicy = currentPolicy

    override suspend fun refresh(): MobileRuntimePolicy? {
      refreshCalls += 1
      val next = refreshedPolicy ?: return null
      currentPolicy = next
      return next
    }
  }

  private fun TestScope.testViewModel(
    api: ChatApi = FakeChatApi(),
    prefs: SharedPreferences = InMemorySharedPreferences(),
    ledgerItemsSource: LedgerItemsSource = FakeLedgerItemsSource(mutableListOf(emptyList())),
    runtimePolicySource: MobileRuntimePolicySource = FakeMobileRuntimePolicySource(),
    startBackgroundWork: Boolean = false,
    enableHeartbeatPolling: Boolean = false,
    enableWorkflowStatusPolling: Boolean = false,
    networkChecker: () -> Boolean = { true },
  ): ChatViewModel {
    return ChatViewModel(
      application = Application(),
      runtimePolicySource = runtimePolicySource,
      repository = ChatRepository(api),
      sharedPrefs = prefs,
      networkChecker = networkChecker,
      ledgerItemsSource = ledgerItemsSource,
      startBackgroundWork = startBackgroundWork,
      enableHeartbeatPolling = enableHeartbeatPolling,
      sessionRecoveryPollCycleLimit = 4,
      enableWorkflowStatusPolling = enableWorkflowStatusPolling,
      currentTimeMsProvider = { scheduler.currentTime },
    ).also { viewModel ->
      createdViewModels += viewModel
      backgroundScope.launch {
        try {
          awaitCancellation()
        } finally {
          clearViewModelForTest(viewModel)
        }
      }
    }
  }

  private fun clearViewModelForTest(viewModel: ChatViewModel) {
    val clearMethod = androidx.lifecycle.ViewModel::class.java.getDeclaredMethod("clear\$lifecycle_viewmodel_release")
    clearMethod.isAccessible = true
    clearMethod.invoke(viewModel)
  }

  private fun releaseViewModelForTest(viewModel: ChatViewModel) {
    createdViewModels.remove(viewModel)
    clearViewModelForTest(viewModel)
  }

  private fun TestScope.workflowViewModel(
    prefs: SharedPreferences = InMemorySharedPreferences(),
  ): ChatViewModel {
    return testViewModel(prefs = prefs)
  }

  private fun ledgerItem(
    uid: String,
    title: String,
    status: String,
    updatedUtc: String,
    releaseState: String = "",
  ): LedgerItemSummary {
    return LedgerItemSummary(
      uid = uid,
      title = title,
      status = status,
      releaseState = releaseState,
      updatedUtc = updatedUtc,
    )
  }

  private fun assertTerminalFailureBubbleCounts(
    viewModel: ChatViewModel,
    expectedCount: Int,
    expectedContentSnippet: String,
  ) {
    listOf(AgentTab.ORCHESTRATOR, AgentTab.BUILDER, AgentTab.VERIFIER).forEach { tab ->
      val messages = viewModel.uiState.value.agentStates.getValue(tab).messages
      val matching = messages.filter { message ->
        message.role == MessageRole.WORKFLOW &&
          message.content.contains(expectedContentSnippet)
      }
      assertEquals("tab=${tab.key}", expectedCount, matching.size)
    }
  }

  private fun jsonStringLiteral(value: String): String {
    return buildString {
      append('"')
      value.forEach { ch ->
        when (ch) {
          '\\' -> append("\\\\")
          '"' -> append("\\\"")
          '\n' -> append("\\n")
          '\r' -> append("\\r")
          '\t' -> append("\\t")
          else -> append(ch)
        }
      }
      append('"')
    }
  }

  private class InMemorySharedPreferences(
    private val values: MutableMap<String, Any?> = mutableMapOf(),
  ) : SharedPreferences {
    override fun getAll(): MutableMap<String, *> = values.toMutableMap()
    override fun getString(key: String?, defValue: String?): String? = values[key] as? String ?: defValue
    override fun getStringSet(key: String?, defValues: Set<String>?): Set<String>? = defValues
    override fun getInt(key: String?, defValue: Int): Int = values[key] as? Int ?: defValue
    override fun getLong(key: String?, defValue: Long): Long = values[key] as? Long ?: defValue
    override fun getFloat(key: String?, defValue: Float): Float = values[key] as? Float ?: defValue
    override fun getBoolean(key: String?, defValue: Boolean): Boolean = values[key] as? Boolean ?: defValue
    override fun contains(key: String?): Boolean = values.containsKey(key)
    override fun edit(): SharedPreferences.Editor = Editor(values)
    override fun registerOnSharedPreferenceChangeListener(listener: SharedPreferences.OnSharedPreferenceChangeListener?) = Unit
    override fun unregisterOnSharedPreferenceChangeListener(listener: SharedPreferences.OnSharedPreferenceChangeListener?) = Unit

    private class Editor(
      private val values: MutableMap<String, Any?>,
    ) : SharedPreferences.Editor {
      private val updates = mutableMapOf<String, Any?>()
      private val removals = mutableSetOf<String>()

      override fun putString(key: String?, value: String?): SharedPreferences.Editor = apply {
        if (key != null) updates[key] = value
      }

      override fun putStringSet(key: String?, values: Set<String>?): SharedPreferences.Editor = apply {
        if (key != null) updates[key] = values?.toSet()
      }
      override fun putInt(key: String?, value: Int): SharedPreferences.Editor = apply {
        if (key != null) updates[key] = value
      }

      override fun putLong(key: String?, value: Long): SharedPreferences.Editor = apply {
        if (key != null) updates[key] = value
      }

      override fun putFloat(key: String?, value: Float): SharedPreferences.Editor = apply {
        if (key != null) updates[key] = value
      }

      override fun putBoolean(key: String?, value: Boolean): SharedPreferences.Editor = apply {
        if (key != null) updates[key] = value
      }

      override fun remove(key: String?): SharedPreferences.Editor = apply {
        if (key != null) removals += key
      }

      override fun clear(): SharedPreferences.Editor = apply {
        removals += values.keys
        updates.clear()
      }

      override fun commit(): Boolean {
        apply()
        return true
      }

      override fun apply() {
        removals.forEach(values::remove)
        updates.forEach { (key, value) ->
          values[key] = value
        }
      }
    }
  }
}
