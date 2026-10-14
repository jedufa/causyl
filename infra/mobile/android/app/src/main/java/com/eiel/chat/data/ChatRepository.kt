package com.eiel.chat.data

import com.eiel.chat.domain.SendRequest
import com.eiel.chat.domain.SessionHistoryMessage
import com.eiel.chat.domain.SessionSnapshot
import com.eiel.chat.domain.SessionStats
import com.eiel.chat.domain.StreamEvent
import com.eiel.chat.domain.WorkflowStartRequest
import com.eiel.chat.domain.WorkflowStartResponse
import com.eiel.chat.domain.WorkflowStatus
import kotlinx.coroutines.flow.Flow

class ChatRepository(
  private val api: ChatApi,
) {
  fun stream(
    threadId: String,
    prompt: String,
    agentKey: String,
    requestId: String = "",
    idempotencyKey: String = "",
    resumeTurnId: String = "",
  ): Flow<StreamEvent> {
    val request = SendRequest(threadId = threadId, prompt = prompt, agentKey = agentKey)
    request.requestId = requestId
    request.idempotencyKey = idempotencyKey
    request.resumeTurnId = resumeTurnId
    return api.stream(request.ensureTransportIdentity())
  }

  suspend fun cancel(threadId: String) {
    api.cancel(threadId)
  }

  suspend fun isServerHealthy(): Boolean {
    return api.isServerHealthy()
  }

  suspend fun serverHealthProbe(): ServerHealthProbe {
    return api.serverHealthProbe()
  }

  suspend fun getSessionStats(threadId: String): SessionStats {
    return api.getSessionStats(threadId)
  }

  suspend fun getSessionSnapshot(threadId: String): SessionSnapshot {
    return api.getSessionSnapshot(threadId)
  }

  suspend fun getSessionHistory(threadId: String): List<SessionHistoryMessage> {
    return api.getSessionHistory(threadId)
  }

  suspend fun setSessionCap(threadId: String, maxTurns: Int): SessionStats {
    return api.setSessionCap(threadId, maxTurns)
  }

  suspend fun startWorkflow(request: WorkflowStartRequest): WorkflowStartResponse {
    return api.startWorkflow(request)
  }

  suspend fun runWorkflow(workflowId: String, maxSteps: Int = 40) {
    api.runWorkflow(workflowId, maxSteps)
  }

  suspend fun getWorkflowStatus(workflowId: String, eventsLimit: Int = 120): WorkflowStatus {
    return api.getWorkflowStatus(workflowId, eventsLimit)
  }

  suspend fun cancelWorkflow(workflowId: String, reason: String = "") {
    api.cancelWorkflow(workflowId, reason)
  }
}
