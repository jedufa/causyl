package com.eiel.chat.ui

internal const val WORKFLOW_MAX_ITERATIONS_MIN = 1
internal const val WORKFLOW_MAX_ITERATIONS_MAX = 8

private val LEADING_INTEGER_REGEX = Regex("""^(\d+)(?:\s+|$)""")

internal data class WorkflowLaunchDirective(
  val instruction: String,
  val maxIterations: Int?,
)

internal fun parseWorkflowLaunchDirective(raw: String): WorkflowLaunchDirective {
  val trimmed = raw.trim()
  if (trimmed.isBlank()) return WorkflowLaunchDirective(instruction = "", maxIterations = null)
  val match = LEADING_INTEGER_REGEX.find(trimmed)
    ?: return WorkflowLaunchDirective(instruction = trimmed, maxIterations = null)
  val parsed = match.groupValues.getOrNull(1)?.toIntOrNull()
  if (parsed == null || parsed !in WORKFLOW_MAX_ITERATIONS_MIN..WORKFLOW_MAX_ITERATIONS_MAX) {
    return WorkflowLaunchDirective(instruction = trimmed, maxIterations = null)
  }
  return WorkflowLaunchDirective(
    instruction = trimmed.removeRange(match.range).trimStart(),
    maxIterations = parsed,
  )
}
