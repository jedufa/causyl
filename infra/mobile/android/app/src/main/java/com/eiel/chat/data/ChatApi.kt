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

data class ServerHealthProbe(
  val isHealthy: Boolean,
  val baseUrl: String = "",
  val httpCode: Int? = null,
  val status: String = "",
  val detail: String = "",
)

interface ChatApi {
  fun stream(request: SendRequest): Flow<StreamEvent>
  suspend fun cancel(threadId: String)
  suspend fun isServerHealthy(): Boolean
  suspend fun serverHealthProbe(): ServerHealthProbe = ServerHealthProbe(isHealthy = isServerHealthy())
  suspend fun getSessionStats(threadId: String): SessionStats
  suspend fun getSessionSnapshot(threadId: String): SessionSnapshot
  suspend fun getSessionHistory(threadId: String): List<SessionHistoryMessage>
  suspend fun setSessionCap(threadId: String, maxTurns: Int): SessionStats
  suspend fun startWorkflow(request: WorkflowStartRequest): WorkflowStartResponse
  suspend fun runWorkflow(workflowId: String, maxSteps: Int = 40)
  suspend fun getWorkflowStatus(workflowId: String, eventsLimit: Int = 120): WorkflowStatus
  suspend fun cancelWorkflow(workflowId: String, reason: String = "")
}
