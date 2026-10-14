package com.eiel.chat.ui

import com.eiel.chat.domain.AgentTab
import com.eiel.chat.domain.WorkflowEvent
import com.eiel.chat.domain.WorkflowStatus

internal data class WorkflowTraceMessage(
  val tabs: Set<AgentTab>,
  val label: String,
  val content: String,
)

internal object WorkflowTraceMapper {
  fun hasTerminalFailureEvent(status: WorkflowStatus): Boolean {
    return status.events.any { event ->
      event.event == "workflow_failed" && messageForEvent(status, event) != null
    }
  }

  fun messageForEvent(status: WorkflowStatus, event: WorkflowEvent): WorkflowTraceMessage? {
    val role = event.payload["role"].orEmpty()
    val turn = event.payload["turn"].orEmpty().ifBlank { status.turn }
    return when (event.event) {
      "workflow_started" -> WorkflowTraceMessage(
        tabs = setOf(AgentTab.ORCHESTRATOR),
        label = "WF-01",
        content = "Started for ${status.objective.ifBlank { status.phaseLabel }}. Configured max_iterations=${status.maxIterations}.",
      )
      "role_turn_started" -> when (turn) {
        "builder_run" -> WorkflowTraceMessage(
          tabs = setOf(AgentTab.ORCHESTRATOR, AgentTab.BUILDER),
          label = "B",
          content = "Builder started iteration ${status.iteration}/${status.maxIterations}.",
        )
        "verifier_run" -> WorkflowTraceMessage(
          tabs = setOf(AgentTab.ORCHESTRATOR, AgentTab.VERIFIER),
          label = "V",
          content = "Verifier started iteration ${status.iteration}/${status.maxIterations}.",
        )
        "intent_validation" -> WorkflowTraceMessage(
          tabs = setOf(AgentTab.ORCHESTRATOR),
          label = "O",
          content = "Orchestrator is preparing the final workflow report.",
        )
        "se_tasking" -> WorkflowTraceMessage(
          tabs = setOf(AgentTab.ORCHESTRATOR),
          label = "O",
          content = "Orchestrator is preparing the builder brief.",
        )
        "se_decide" -> WorkflowTraceMessage(
          tabs = setOf(AgentTab.ORCHESTRATOR),
          label = "O",
          content = "Orchestrator is deciding the next workflow step.",
        )
        else -> if (role.isNotBlank()) {
          WorkflowTraceMessage(
            tabs = setOf(AgentTab.ORCHESTRATOR),
            label = role.uppercase(),
            content = "${role.replaceFirstChar { it.uppercase() }} started.",
          )
        } else {
          null
        }
      }
      "builder_run_done" -> WorkflowTraceMessage(
        tabs = setOf(AgentTab.ORCHESTRATOR, AgentTab.BUILDER),
        label = "B",
        content = "Builder finished iteration ${event.payload["iteration"].orEmpty().ifBlank { status.iteration.toString() }}.",
      )
      "verifier_run_done" -> {
        val verdict = event.payload["verdict"].orEmpty().ifBlank { status.verifierVerdict }.uppercase()
        WorkflowTraceMessage(
          tabs = setOf(AgentTab.ORCHESTRATOR, AgentTab.VERIFIER),
          label = "V",
          content = "Verifier finished iteration ${event.payload["iteration"].orEmpty().ifBlank { status.iteration.toString() }} with verdict $verdict.",
        )
      }
      "role_progress_note" -> {
        val note = event.payload["note"].orEmpty()
        val label = when (role) {
          "builder" -> "B"
          "assurer" -> "V"
          else -> "O"
        }
        val tabs = when (label) {
          "B" -> setOf(AgentTab.ORCHESTRATOR, AgentTab.BUILDER)
          "V" -> setOf(AgentTab.ORCHESTRATOR, AgentTab.VERIFIER)
          else -> setOf(AgentTab.ORCHESTRATOR)
        }
        if (note.isBlank()) null else WorkflowTraceMessage(tabs = tabs, label = label, content = note)
      }
      "role_waiting_host_verification" -> WorkflowTraceMessage(
        tabs = setOf(AgentTab.ORCHESTRATOR, AgentTab.VERIFIER),
        label = "V",
        content = event.payload["reason"].orEmpty().ifBlank { "Waiting for host verification." },
      )
      "role_stuck_detected" -> WorkflowTraceMessage(
        tabs = setOf(AgentTab.ORCHESTRATOR, AgentTab.BUILDER, AgentTab.VERIFIER),
        label = "WF",
        content = event.payload["reason"].orEmpty().ifBlank { "Workflow detected a stuck role." },
      )
      "role_escalated", "se_unblock_done" -> WorkflowTraceMessage(
        tabs = setOf(AgentTab.ORCHESTRATOR),
        label = "O",
        content = event.payload["reason"].orEmpty().ifBlank {
          if (event.event == "se_unblock_done") {
            "Orchestrator chose the next bounded recovery step."
          } else {
            "Orchestrator is taking over a stuck role."
          }
        },
      )
      "se_decision_done" -> {
        val decision = event.payload["decision"].orEmpty()
        val reason = event.payload["reason"].orEmpty()
        val text = when (decision) {
          "extra_iteration" -> "Verifier requested changes. Orchestrator sent feedback back to Builder."
          "next_subphase" -> "Current step passed. Orchestrator advanced the workflow."
          "stop_phase" -> "Workflow stopped after reaching its decision gate."
          else -> "Orchestrator recorded decision ${decision.ifBlank { "unknown" }}."
        }
        WorkflowTraceMessage(
          tabs = when (decision) {
            "extra_iteration" -> setOf(AgentTab.ORCHESTRATOR, AgentTab.BUILDER)
            else -> setOf(AgentTab.ORCHESTRATOR)
          },
          label = "O",
          content = if (reason.isBlank()) text else "$text\nReason: $reason",
        )
      }
      "intent_validation_done" -> WorkflowTraceMessage(
        tabs = setOf(AgentTab.ORCHESTRATOR, AgentTab.BUILDER, AgentTab.VERIFIER),
        label = "WF-01",
        content = "Workflow completed. Final validation report is ready.",
      )
      "workflow_failed" -> {
        val error = event.payload["error"].orEmpty()
        val failedTurn = event.payload["turn"].orEmpty()
        WorkflowTraceMessage(
          tabs = setOf(AgentTab.ORCHESTRATOR, AgentTab.BUILDER, AgentTab.VERIFIER),
          label = "WF-01",
          content = "Workflow failed${if (failedTurn.isBlank()) "" else " during $failedTurn"}${if (error.isBlank()) "." else ": $error"}",
        )
      }
      "workflow_cancelled" -> WorkflowTraceMessage(
        tabs = setOf(AgentTab.ORCHESTRATOR, AgentTab.BUILDER, AgentTab.VERIFIER),
        label = "WF-01",
        content = "Workflow cancelled.",
      )
      else -> null
    }
  }
}
