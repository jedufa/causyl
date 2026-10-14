package com.eiel.chat

import com.eiel.chat.domain.AgentTab
import com.eiel.chat.domain.WorkflowEvent
import com.eiel.chat.domain.WorkflowStatus
import com.eiel.chat.ui.WorkflowTraceMapper
import org.junit.Assert.assertEquals
import org.junit.Assert.assertTrue
import org.junit.Test

class WorkflowTraceMapperTest {
  private fun status(
    iteration: Int = 1,
    maxIterations: Int = 4,
    verifierVerdict: String = "FAIL",
  ): WorkflowStatus {
    return WorkflowStatus(
      workflowId = "wf-123",
      status = "running",
      turn = "builder_run",
      iteration = iteration,
      maxIterations = maxIterations,
      phaseLabel = "WF-01",
      objective = "Implement PIS-006",
      verifierVerdict = verifierVerdict,
      lastError = "",
      intentValidationReport = "",
      events = emptyList(),
    )
  }

  @Test
  fun builderEventsMirrorToBuilderAndOrchestratorTabs() {
    val message = WorkflowTraceMapper.messageForEvent(
      status = status(iteration = 2),
      event = WorkflowEvent(
        utc = "2026-03-07T12:00:00Z",
        event = "builder_run_done",
        payload = mapOf("iteration" to "2", "role" to "builder"),
      ),
    )

    requireNotNull(message)
    assertEquals(setOf(AgentTab.ORCHESTRATOR, AgentTab.BUILDER), message.tabs)
    assertEquals("B", message.label)
    assertTrue(message.content.contains("iteration 2"))
  }

  @Test
  fun verifierEventsMirrorToVerifierAndOrchestratorTabs() {
    val message = WorkflowTraceMapper.messageForEvent(
      status = status(iteration = 1, verifierVerdict = "PASS"),
      event = WorkflowEvent(
        utc = "2026-03-07T12:00:01Z",
        event = "verifier_run_done",
        payload = mapOf("iteration" to "1", "role" to "verifier", "verdict" to "pass"),
      ),
    )

    requireNotNull(message)
    assertEquals(setOf(AgentTab.ORCHESTRATOR, AgentTab.VERIFIER), message.tabs)
    assertEquals("V", message.label)
    assertTrue(message.content.contains("PASS"))
  }

  @Test
  fun retryDecisionAlsoAppearsInBuilderChat() {
    val message = WorkflowTraceMapper.messageForEvent(
      status = status(),
      event = WorkflowEvent(
        utc = "2026-03-07T12:00:02Z",
        event = "se_decision_done",
        payload = mapOf("decision" to "extra_iteration", "reason" to "Verifier requested boundary checks"),
      ),
    )

    requireNotNull(message)
    assertEquals(setOf(AgentTab.ORCHESTRATOR, AgentTab.BUILDER), message.tabs)
    assertEquals("O", message.label)
    assertTrue(message.content.contains("feedback back to Builder"))
    assertTrue(message.content.contains("Reason: Verifier requested boundary checks"))
  }

  @Test
  fun roleTurnStartedFallsBackToStatusTurnForLiveBuilderTrace() {
    val message = WorkflowTraceMapper.messageForEvent(
      status = status(iteration = 1),
      event = WorkflowEvent(
        utc = "2026-03-07T12:00:03Z",
        event = "role_turn_started",
        payload = mapOf("role" to "builder"),
      ),
    )

    requireNotNull(message)
    assertEquals(setOf(AgentTab.ORCHESTRATOR, AgentTab.BUILDER), message.tabs)
    assertEquals("B", message.label)
    assertTrue(message.content.contains("Builder started iteration 1/4"))
  }

  @Test
  fun workflowFailureMirrorsToAllParticipatingChats() {
    val message = WorkflowTraceMapper.messageForEvent(
      status = status(),
      event = WorkflowEvent(
        utc = "2026-03-07T12:00:04Z",
        event = "workflow_failed",
        payload = mapOf("turn" to "verifier_run", "error" to "Audit did not pass"),
      ),
    )

    requireNotNull(message)
    assertEquals(setOf(AgentTab.ORCHESTRATOR, AgentTab.BUILDER, AgentTab.VERIFIER), message.tabs)
    assertEquals("WF-01", message.label)
    assertTrue(message.content.contains("Audit did not pass"))
  }

  @Test
  fun workflowStartedEchoesConfiguredMaxIterations() {
    val message = WorkflowTraceMapper.messageForEvent(
      status = status(maxIterations = 4),
      event = WorkflowEvent(
        utc = "2026-03-07T12:00:05Z",
        event = "workflow_started",
        payload = emptyMap(),
      ),
    )

    requireNotNull(message)
    assertEquals(setOf(AgentTab.ORCHESTRATOR), message.tabs)
    assertEquals("WF-01", message.label)
    assertTrue(message.content.contains("Configured max_iterations=4"))
  }
}
