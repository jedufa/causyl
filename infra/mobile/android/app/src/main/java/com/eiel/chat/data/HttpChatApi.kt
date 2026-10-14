package com.eiel.chat.data

import com.eiel.chat.BuildConfig
import com.eiel.chat.domain.SendRequest
import com.eiel.chat.domain.SessionHistoryMessage
import com.eiel.chat.domain.MessageRole
import com.eiel.chat.domain.SessionSnapshot
import com.eiel.chat.domain.SessionStreamState
import com.eiel.chat.domain.SessionStats
import com.eiel.chat.domain.StreamEvent
import com.eiel.chat.domain.StreamPhase
import com.eiel.chat.domain.parsePersistedMessageStatus
import com.eiel.chat.domain.WorkflowEvent
import com.eiel.chat.domain.WorkflowStartRequest
import com.eiel.chat.domain.WorkflowStartResponse
import com.eiel.chat.domain.WorkflowStatus
import com.eiel.chat.util.AppLogger
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.flow.Flow
import kotlinx.coroutines.flow.channelFlow
import kotlinx.coroutines.withContext
import okhttp3.OkHttpClient
import okhttp3.Request
import okhttp3.MediaType.Companion.toMediaType
import okhttp3.RequestBody.Companion.toRequestBody
import org.json.JSONObject
import java.net.URLEncoder
import java.io.IOException
import java.util.concurrent.TimeUnit

class ChatRemoteCancelConflictException(message: String) : IllegalStateException(message)
class ChatRemoteCancelFailedException(message: String) : IllegalStateException(message)
class ChatRemoteCancelPendingException(message: String) : IllegalStateException(message)

class HttpChatApi(
  private val client: OkHttpClient = OkHttpClient.Builder()
    .retryOnConnectionFailure(false)
    .connectTimeout(90, TimeUnit.SECONDS)
    .writeTimeout(90, TimeUnit.SECONDS)
    .readTimeout(0, TimeUnit.MILLISECONDS)
    .callTimeout(0, TimeUnit.MILLISECONDS)
    .build(),
  serverBaseUrl: String = BuildConfig.SERVER_BASE_URL,
  private val serverBaseUrlProvider: (() -> String)? = null,
  chatStreamPath: String = BuildConfig.CHAT_STREAM_PATH,
  authToken: String = BuildConfig.AUTH_TOKEN,
  private val chatTimeoutS: Int = BuildConfig.CHAT_TIMEOUT_S,
  private val chatTimeoutProvider: (() -> Int)? = null,
  private val workflowTimeoutS: Int = BuildConfig.WORKFLOW_TIMEOUT_S,
  private val workflowTimeoutProvider: (() -> Int)? = null,
  private val workingBaseUrlRecorder: ((String) -> Unit)? = null,
) : ChatApi {
  private companion object {
    const val HOST_VERIFICATION_PROGRESS_TEXT = "Running host verification..."
  }

  private val fallbackBaseUrl = ServerConfig.normalizeBaseUrl(serverBaseUrl)
  @Volatile
  private var preferredBaseUrlOverride: String = ""
  private val streamPath = if (chatStreamPath.startsWith("/")) chatStreamPath else "/$chatStreamPath"
  private val token = authToken
  private val healthClient: OkHttpClient = client.newBuilder()
    .connectTimeout(5, TimeUnit.SECONDS)
    .readTimeout(5, TimeUnit.SECONDS)
    .writeTimeout(5, TimeUnit.SECONDS)
    .callTimeout(6, TimeUnit.SECONDS)
    .build()

  private fun configuredBaseUrl(): String = ServerConfig.normalizeBaseUrl(serverBaseUrlProvider?.invoke() ?: fallbackBaseUrl)
  private fun baseUrl(): String = preferredBaseUrlOverride.ifBlank { configuredBaseUrl() }
  private fun streamUrl(): String = "${baseUrl()}$streamPath"
  private fun currentChatTimeoutS(): Int = (chatTimeoutProvider?.invoke() ?: chatTimeoutS).coerceAtLeast(30)
  private fun currentWorkflowTimeoutS(): Int =
    (workflowTimeoutProvider?.invoke() ?: workflowTimeoutS).coerceIn(30, 6 * 60 * 60)

  private fun rememberWorkingBaseUrl(url: String) {
    val normalized = ServerConfig.normalizeBaseUrl(url)
    preferredBaseUrlOverride = normalized
    runCatching { workingBaseUrlRecorder?.invoke(normalized) }
  }

  private suspend fun probeBaseUrl(resolvedBaseUrl: String): ServerHealthProbe = withContext(Dispatchers.IO) {
    return@withContext try {
      val req = Request.Builder().url("$resolvedBaseUrl/api/health").get().build()
      healthClient.newCall(req).execute().use { response ->
        val body = response.body?.string().orEmpty()
        val status = runCatching { JSONObject(body).optString("status", "") }.getOrDefault("")
        when {
          !response.isSuccessful -> ServerHealthProbe(
            isHealthy = false,
            baseUrl = resolvedBaseUrl,
            httpCode = response.code,
            status = status,
            detail = "HTTP ${response.code}",
          )
          status.isNotBlank() && status.lowercase() != "ok" -> ServerHealthProbe(
            isHealthy = false,
            baseUrl = resolvedBaseUrl,
            httpCode = response.code,
            status = status,
            detail = "health status=$status",
          )
          else -> {
            rememberWorkingBaseUrl(resolvedBaseUrl)
            ServerHealthProbe(
              isHealthy = true,
              baseUrl = resolvedBaseUrl,
              httpCode = response.code,
              status = if (status.isBlank()) "ok" else status,
            )
          }
        }
      }
    } catch (error: Throwable) {
      ServerHealthProbe(
        isHealthy = false,
        baseUrl = resolvedBaseUrl,
        detail = error.message ?: error::class.java.simpleName,
      )
    }
  }

  override fun stream(request: SendRequest): Flow<StreamEvent> = channelFlow {
    val timeoutS = currentChatTimeoutS()
    val requestId = request.requestId.trim().ifBlank { "mobile_send_${java.util.UUID.randomUUID()}" }
    val idempotencyKey = request.idempotencyKey.trim().ifBlank { requestId }
    val reqPayload = JSONObject()
      .put("agent_key", request.agentKey)
      .put("message", request.prompt)
      .put("thread_id", request.threadId)
      .put("prompt", request.prompt)
      .put("request_id", requestId)
      .put("idempotency_key", idempotencyKey)
      .put("timeout_s", timeoutS)
      .also { payload ->
        if (request.resumeTurnId.isNotBlank()) {
          payload.put("resume_turn_id", request.resumeTurnId)
        }
      }
      .toString()
    val req = Request.Builder()
      .url(streamUrl())
      .post(reqPayload.toRequestBody("application/json; charset=utf-8".toMediaType()))
      .also { b ->
        b.addHeader("Accept", "text/event-stream")
        if (token.isNotBlank()) b.addHeader("Authorization", "Bearer $token")
      }
      .build()

    withContext(Dispatchers.IO) {
      var attempt = 0
      val retryDeadlineMs = System.currentTimeMillis() + maxOf(120_000L, timeoutS.toLong() * 1000L)
      while (true) {
        attempt += 1
        var emittedToken = false
        try {
          AppLogger.i("stream attempt=$attempt timeoutS=$timeoutS")
          client.newCall(req).execute().use { response ->
            if (!response.isSuccessful) {
              AppLogger.w("stream failed http=${response.code}")
              trySend(StreamEvent.Error("stream failed: HTTP ${response.code}"))
              return@withContext
            }

            val body = response.body ?: run {
              AppLogger.w("stream failed: empty body")
              trySend(StreamEvent.Error("stream failed: empty body"))
              return@withContext
            }

            val source = body.source()
            while (!source.exhausted()) {
              val line = source.readUtf8Line() ?: break
              if (line.startsWith(":")) {
                if (line.contains("keepalive", ignoreCase = true)) {
                  trySend(StreamEvent.Keepalive)
                }
                continue
              }
              if (line.startsWith("data:")) {
                val payload = line.removePrefix("data:").trim()
                if (payload == "[DONE]") {
                  trySend(StreamEvent.Done)
                  return@withContext
                }
                if (extractJsonBooleanField(payload, "plane_routed") == true) {
                  trySend(StreamEvent.PlaneRouted(true))
                }
                val eventType = extractJsonStringField(payload, "type")?.trim()?.lowercase().orEmpty()
                if (eventType == "start") {
                  trySend(StreamEvent.Start(extractJsonStringField(payload, "turn_id").orEmpty()))
                  continue
                }
                extractErrorMessage(payload)?.let {
                  val reason = if (emittedToken) {
                    interruptedReason("stream failed: $it")
                  } else {
                    "stream failed: $it"
                  }
                  AppLogger.w("stream error payload: $reason")
                  trySend(StreamEvent.Error(reason))
                  return@withContext
                }
                if (isExplicitSuccessTerminal(payload)) {
                  trySend(StreamEvent.Done)
                  return@withContext
                }
                val progressMessage = extractProgressMessage(payload)
                if (progressMessage != null) {
                  trySend(StreamEvent.Progress(progressMessage, phase = extractProgressPhase(payload, progressMessage)))
                  continue
                }
                val title = extractConversationTitle(payload)
                if (title != null) {
                  trySend(StreamEvent.Title(title))
                  continue
                }
                if (isKeepalivePayload(payload)) {
                  trySend(StreamEvent.Keepalive)
                  continue
                }
                if (shouldIgnorePayload(payload)) {
                  continue
                }
                if (payload.isNotBlank()) {
                  val token = extractToken(payload)
                  if (token == null) {
                    continue
                  }
                  trySend(StreamEvent.Token(token))
                  emittedToken = true
                }
              }
            }
          }
          val reason = if (emittedToken) {
            interruptedReason("stream ended before completion")
          } else {
            "stream ended before completion"
          }
          AppLogger.w(reason)
          trySend(StreamEvent.Error(reason))
          return@withContext
        } catch (t: Throwable) {
          val transient = isTransientNetworkError(t)
          val nowMs = System.currentTimeMillis()
          if (transient && emittedToken) {
            val reason = interruptedReason(t.message ?: t.javaClass.simpleName)
            AppLogger.w(
              "stream interrupted after partial output: $reason",
            )
            trySend(StreamEvent.Error(reason))
            return@withContext
          }
          if (transient && nowMs < retryDeadlineMs) {
            val backoffMs = nextBackoffMs(attempt)
            val remainingMs = retryDeadlineMs - nowMs
            val sleepMs = backoffMs.coerceAtMost(maxOf(250L, remainingMs))
            AppLogger.w(
              "transient stream failure; retrying in ${sleepMs}ms (attempt=$attempt): " +
                (t.message ?: t.javaClass.simpleName),
            )
            Thread.sleep(sleepMs)
            continue
          }
          trySend(StreamEvent.Error("stream failed: ${t.message ?: "network error"}"))
          return@withContext
        }
      }
    }
  }

  override suspend fun cancel(threadId: String): Unit = withContext(Dispatchers.IO) {
    val payload = """{"thread_id":"${escapeJson(threadId)}","reason":"cancelled from mobile"}"""
    val req = Request.Builder()
      .url("${baseUrl()}/api/mobile/chat/cancel")
      .post(payload.toRequestBody("application/json; charset=utf-8".toMediaType()))
      .also { b -> if (token.isNotBlank()) b.addHeader("Authorization", "Bearer $token") }
      .build()
    client.newCall(req).execute().use { response ->
      val body = response.body?.string().orEmpty()
      val status = parseCancelStatus(body)
      val message = parseCancelMessage(body)
      if (response.isSuccessful) {
        when (status) {
          "cancelled" -> return@withContext Unit
          "cancel_requested" -> throw ChatRemoteCancelPendingException(
            message.ifBlank {
              "Local rendering stopped, but the server is still resolving the cancel request. The reply may still complete; refresh to confirm the final server state."
            },
          )
          "already_completed", "not_found" -> throw ChatRemoteCancelConflictException(
            message.ifBlank { "Local rendering stopped, but the server had no active turn to cancel." },
          )
          "cancel_failed" -> throw ChatRemoteCancelFailedException(
            message.ifBlank { "Local rendering stopped, but server cancellation failed." },
          )
          else -> return@withContext Unit
        }
      }
      if (status == "already_completed" || status == "not_found") {
        throw ChatRemoteCancelConflictException(
          message.ifBlank { "Local rendering stopped, but the server had no active turn to cancel." },
        )
      }
      throw ChatRemoteCancelFailedException(
        message.ifBlank { "chat cancel failed: HTTP ${response.code}" },
      )
    }
  }

  override suspend fun isServerHealthy(): Boolean = withContext(Dispatchers.IO) {
    return@withContext serverHealthProbe().isHealthy
  }

  override suspend fun serverHealthProbe(): ServerHealthProbe = withContext(Dispatchers.IO) {
    val activeBaseUrl = baseUrl()
    val activeProbe = probeBaseUrl(activeBaseUrl)
    if (activeProbe.isHealthy) {
      return@withContext activeProbe
    }
    if (activeBaseUrl != fallbackBaseUrl) {
      val fallbackProbe = probeBaseUrl(fallbackBaseUrl)
      if (fallbackProbe.isHealthy) {
        AppLogger.w("health probe recovered using fallback base URL $fallbackBaseUrl")
        return@withContext fallbackProbe
      }
    }
    return@withContext activeProbe
  }

  override suspend fun getSessionStats(threadId: String): SessionStats = withContext(Dispatchers.IO) {
    val tid = URLEncoder.encode(threadId, "UTF-8")
    val req = Request.Builder()
      .url("${baseUrl()}/api/mobile/chat/session_stats?thread_id=$tid")
      .get()
      .also { b -> if (token.isNotBlank()) b.addHeader("Authorization", "Bearer $token") }
      .build()
    client.newCall(req).execute().use { response ->
      if (!response.isSuccessful) throw IllegalStateException("session_stats failed: HTTP ${response.code}")
      val body = response.body?.string() ?: throw IllegalStateException("session_stats failed: empty body")
      return@withContext parseSessionStats(body)
    }
  }

  override suspend fun setSessionCap(threadId: String, maxTurns: Int): SessionStats = withContext(Dispatchers.IO) {
    val payload = """{"thread_id":"${escapeJson(threadId)}","max_turns":$maxTurns}"""
    val req = Request.Builder()
      .url("${baseUrl()}/api/mobile/chat/session_cap")
      .post(payload.toRequestBody("application/json; charset=utf-8".toMediaType()))
      .also { b -> if (token.isNotBlank()) b.addHeader("Authorization", "Bearer $token") }
      .build()
    client.newCall(req).execute().use { response ->
      if (!response.isSuccessful) throw IllegalStateException("session_cap failed: HTTP ${response.code}")
      val body = response.body?.string() ?: throw IllegalStateException("session_cap failed: empty body")
      return@withContext parseSessionStats(body)
    }
  }

  override suspend fun getSessionSnapshot(threadId: String): SessionSnapshot = withContext(Dispatchers.IO) {
    val tid = URLEncoder.encode(threadId, "UTF-8")
    val req = Request.Builder()
      .url("${baseUrl()}/api/mobile/chat/session_state?thread_id=$tid")
      .get()
      .also { b -> if (token.isNotBlank()) b.addHeader("Authorization", "Bearer $token") }
      .build()
    client.newCall(req).execute().use { response ->
      if (response.code == 404) {
        return@withContext SessionSnapshot(history = getSessionHistory(threadId))
      }
      if (!response.isSuccessful) throw IllegalStateException("session_state failed: HTTP ${response.code}")
      val body = response.body?.string() ?: throw IllegalStateException("session_state failed: empty body")
      return@withContext parseSessionSnapshotPayload(body)
    }
  }

  override suspend fun getSessionHistory(threadId: String): List<SessionHistoryMessage> = withContext(Dispatchers.IO) {
    val tid = URLEncoder.encode(threadId, "UTF-8")
    val req = Request.Builder()
      .url("${baseUrl()}/api/mobile/chat/session_history?thread_id=$tid")
      .get()
      .also { b -> if (token.isNotBlank()) b.addHeader("Authorization", "Bearer $token") }
      .build()
    client.newCall(req).execute().use { response ->
      if (!response.isSuccessful) throw IllegalStateException("session_history failed: HTTP ${response.code}")
      val body = response.body?.string() ?: throw IllegalStateException("session_history failed: empty body")
      return@withContext parseSessionHistoryPayload(body)
    }
  }

  override suspend fun startWorkflow(request: WorkflowStartRequest): WorkflowStartResponse = withContext(Dispatchers.IO) {
    val optionsEntries = buildList {
      request.maxIterations?.let { add("\"max_iterations\":$it") }
      add("\"execution_directive\":\"${escapeJson(request.executionDirective)}\"")
    }.joinToString(",")
    val payload =
      """
      {
        "binding":{"type":"${escapeJson(request.bindingType)}","id":"${escapeJson(request.bindingId)}"},
        "phase_label":"${escapeJson(request.phaseLabel)}",
        "objective":"${escapeJson(request.objective)}",
        "timeout_s":${currentWorkflowTimeoutS()},
        "options":{$optionsEntries}
      }
      """.trimIndent()
    val req = Request.Builder()
      .url("${baseUrl()}/api/agents/orchestration/start")
      .post(payload.toRequestBody("application/json; charset=utf-8".toMediaType()))
      .also { b -> if (token.isNotBlank()) b.addHeader("Authorization", "Bearer $token") }
      .build()
    client.newCall(req).execute().use { response ->
      if (!response.isSuccessful) throw IllegalStateException("workflow start failed: HTTP ${response.code}")
      val body = response.body?.string() ?: throw IllegalStateException("workflow start failed: empty body")
      val root = parseJsonObject(body, "workflow start failed: malformed payload")
      val state = root.objectOrEmpty("state")
      return@withContext WorkflowStartResponse(
        workflowId = root.stringOrNull("workflow_id").orEmpty().ifBlank {
          throw IllegalStateException("workflow start failed: missing workflow_id")
        },
        phaseLabel = state.stringOrNull("phase_label").orEmpty(),
        objective = state.stringOrNull("objective").orEmpty(),
        maxIterations = state.intOrDefault("max_iterations", 0),
      )
    }
  }

  override suspend fun runWorkflow(workflowId: String, maxSteps: Int): Unit = withContext(Dispatchers.IO) {
    val payload =
      """{"workflow_id":"${escapeJson(workflowId)}","max_steps":${maxSteps.coerceIn(1, 200)},"timeout_s":${currentWorkflowTimeoutS()}}"""
    val req = Request.Builder()
      .url("${baseUrl()}/api/agents/orchestration/run")
      .post(payload.toRequestBody("application/json; charset=utf-8".toMediaType()))
      .also { b -> if (token.isNotBlank()) b.addHeader("Authorization", "Bearer $token") }
      .build()
    client.newCall(req).execute().use { response ->
      if (!response.isSuccessful) throw IllegalStateException("workflow run failed: HTTP ${response.code}")
      response.body?.close()
    }
  }

  override suspend fun getWorkflowStatus(workflowId: String, eventsLimit: Int): WorkflowStatus = withContext(Dispatchers.IO) {
    val wid = URLEncoder.encode(workflowId, "UTF-8")
    val req = Request.Builder()
      .url("${baseUrl()}/api/agents/orchestration/status?workflow_id=$wid&events_limit=${eventsLimit.coerceIn(1, 500)}")
      .get()
      .also { b -> if (token.isNotBlank()) b.addHeader("Authorization", "Bearer $token") }
      .build()
    client.newCall(req).execute().use { response ->
      if (!response.isSuccessful) throw IllegalStateException("workflow status failed: HTTP ${response.code}")
      val body = response.body?.string() ?: throw IllegalStateException("workflow status failed: empty body")
      return@withContext parseWorkflowStatus(body)
    }
  }

  override suspend fun cancelWorkflow(workflowId: String, reason: String): Unit = withContext(Dispatchers.IO) {
    val payload =
      """{"workflow_id":"${escapeJson(workflowId)}","reason":"${escapeJson(reason)}"}"""
    val req = Request.Builder()
      .url("${baseUrl()}/api/agents/orchestration/cancel")
      .post(payload.toRequestBody("application/json; charset=utf-8".toMediaType()))
      .also { b -> if (token.isNotBlank()) b.addHeader("Authorization", "Bearer $token") }
      .build()
    client.newCall(req).execute().use { response ->
      if (!response.isSuccessful) throw IllegalStateException("workflow cancel failed: HTTP ${response.code}")
      response.body?.close()
    }
  }

  private fun extractToken(payload: String): String? {
    return try {
      if (!payload.startsWith("{")) return payload
      if (containsDoneEvent(payload)) return null
      val eventValue = extractJsonStringField(payload, "event")
      val typeValue = extractJsonStringField(payload, "type")
      if (eventValue == "commentary" || eventValue == "progress" || typeValue == "commentary" || typeValue == "progress") {
        return null
      }
      val directKeys = listOf("token", "delta", "text", "content")
      for (key in directKeys) {
        extractJsonStringField(payload, key)?.let { return it }
      }

      extractNestedJsonStringField(payload, outer = "delta", inner = "content")?.let { return it }
      extractNestedJsonStringField(payload, outer = "message", inner = "content")?.let { return it }
      extractChoicesDeltaContent(payload)?.let { return it }
      null
    } catch (_: Throwable) {
      payload
    }
  }

  private fun containsDoneEvent(payload: String): Boolean {
    val eventValue = extractJsonStringField(payload, "event")
    if (eventValue == "done") return true
    val typeValue = extractJsonStringField(payload, "type")
    return typeValue == "done"
  }

  private fun isExplicitSuccessTerminal(payload: String): Boolean {
    if (!containsDoneEvent(payload)) return false
    return extractJsonBooleanField(payload, "ok") == true
  }

  private fun extractErrorMessage(payload: String): String? {
    val eventValue = extractJsonStringField(payload, "event")
    if (eventValue == "error") {
      return extractJsonStringField(payload, "error") ?: "stream failed"
    }
    val typeValue = extractJsonStringField(payload, "type")
    if (typeValue == "done") {
      extractJsonStringField(payload, "error")?.takeIf { it.isNotBlank() }?.let { return it }
      val okValue = extractJsonBooleanField(payload, "ok")
      if (okValue == false) {
        return extractJsonStringField(payload, "error") ?: "stream failed"
      }
    }
    return null
  }

  private fun extractProgressMessage(payload: String): String? {
    val eventValue = extractJsonStringField(payload, "event")
    val typeValue = extractJsonStringField(payload, "type")
    if (eventValue == "commentary" || eventValue == "progress" || typeValue == "commentary" || typeValue == "progress") {
      return extractJsonStringField(payload, "commentary")
        ?: extractJsonStringField(payload, "progress")
        ?: extractJsonStringField(payload, "text")
        ?: extractJsonStringField(payload, "content")
    }
    return null
  }

  /** Map structured or legacy commentary payloads onto the mobile stream phase model. */
  private fun extractProgressPhase(payload: String, progressMessage: String): StreamPhase? {
    StreamPhase.fromWireValue(extractJsonStringField(payload, "phase"))?.let { return it }
    return if (progressMessage.trim() == HOST_VERIFICATION_PROGRESS_TEXT) {
      StreamPhase.HOST_VERIFICATION_WAIT
    } else {
      null
    }
  }

  private fun extractJsonBooleanField(payload: String, field: String): Boolean? {
    val regex = Regex("\"$field\"\\s*:\\s*(true|false)")
    val raw = regex.find(payload)?.groupValues?.getOrNull(1) ?: return null
    return raw == "true"
  }

  private fun extractJsonIntField(payload: String, field: String): Int? {
    val regex = Regex("\"$field\"\\s*:\\s*(-?\\d+)")
    val raw = regex.find(payload)?.groupValues?.getOrNull(1) ?: return null
    return raw.toIntOrNull()
  }

  private fun shouldIgnorePayload(payload: String): Boolean {
    if (!payload.startsWith("{")) return false
    val typeValue = extractJsonStringField(payload, "type")
    if (typeValue == "start") return true
    val eventValue = extractJsonStringField(payload, "event")
    if (eventValue == "start") return true
    return false
  }

  private fun isKeepalivePayload(payload: String): Boolean {
    if (!payload.startsWith("{")) return false
    val typeValue = extractJsonStringField(payload, "type")?.trim()?.lowercase()
    val eventValue = extractJsonStringField(payload, "event")?.trim()?.lowercase()
    return typeValue == "keepalive" || eventValue == "keepalive"
  }

  private fun extractConversationTitle(payload: String): String? {
    if (extractJsonStringField(payload, "event") != "metadata") return null
    val title = extractJsonStringField(payload, "chat_title")?.trim().orEmpty()
    return title.ifBlank { null }
  }

  private fun extractJsonStringField(payload: String, field: String): String? {
    val regex = Regex("\"$field\"\\s*:\\s*\"((?:\\\\.|[^\"\\\\])*)\"")
    val raw = regex.find(payload)?.groupValues?.getOrNull(1) ?: return null
    return unescapeJson(raw)
  }

  private fun extractNestedJsonStringField(payload: String, outer: String, inner: String): String? {
    val regex = Regex(
      "\"$outer\"\\s*:\\s*\\{[^}]*\"$inner\"\\s*:\\s*\"((?:\\\\.|[^\"\\\\])*)\"",
      setOf(RegexOption.DOT_MATCHES_ALL),
    )
    val raw = regex.find(payload)?.groupValues?.getOrNull(1) ?: return null
    return unescapeJson(raw)
  }

  private fun extractChoicesDeltaContent(payload: String): String? {
    val regex = Regex(
      "\"choices\"\\s*:\\s*\\[\\s*\\{.*?\"delta\"\\s*:\\s*\\{[^}]*\"content\"\\s*:\\s*\"((?:\\\\.|[^\"\\\\])*)\"",
      setOf(RegexOption.DOT_MATCHES_ALL),
    )
    val raw = regex.find(payload)?.groupValues?.getOrNull(1) ?: return null
    return unescapeJson(raw)
  }

  private fun parseSessionStats(payload: String): SessionStats {
    return SessionStats(
      threadId = extractJsonStringField(payload, "thread_id") ?: "mbl",
      maxTurns = extractJsonIntField(payload, "max_turns") ?: 20000,
      turns = extractJsonIntField(payload, "turns") ?: 0,
      messages = extractJsonIntField(payload, "messages") ?: 0,
      chars = extractJsonIntField(payload, "chars") ?: 0,
      lines = extractJsonIntField(payload, "lines") ?: 0,
      atCap = extractJsonBooleanField(payload, "at_cap") ?: false,
      willCycleNext = extractJsonBooleanField(payload, "will_cycle_next") ?: false,
      storagePath = extractJsonStringField(payload, "storage_path") ?: "",
      storageFileBytes = extractJsonIntField(payload, "storage_file_bytes") ?: 0,
    )
  }

  internal fun parseSessionHistoryPayload(payload: String): List<SessionHistoryMessage> {
    return parseSessionSnapshotPayload(payload).history
  }

  internal fun parseSessionSnapshotPayload(payload: String): SessionSnapshot {
    return try {
      val root = parseJsonObject(payload)
      val history = root.listOrEmpty("history")
      val rows = buildList {
        for (entry in history) {
          val row = entry as? Map<String, Any?> ?: continue
          val roleRaw = row.stringOrNull("role")?.lowercase().orEmpty()
          val content = row.stringValue("content")
          if (content.isBlank()) continue
          val role = when (roleRaw) {
            "assistant" -> MessageRole.ASSISTANT
            "workflow" -> MessageRole.WORKFLOW
            "user" -> MessageRole.USER
            else -> continue
          }
          val label = row.stringOrNull("label")
          val status = parsePersistedMessageStatus(
            rawStatus = row.stringOrNull("status"),
            label = label,
          )
          add(
            SessionHistoryMessage(
              role = role,
              content = content,
              status = status,
              label = label,
              workflowId = row.stringOrNull("workflow_id"),
              eventKey = row.stringOrNull("event_key"),
              planeRouted = row.booleanOrFalse("plane_routed"),
            ),
          )
        }
      }
      val streamObj = root.objectOrNull("stream_state")
      val streamState = streamObj?.let {
        val status = it.stringOrNull("status")?.lowercase().orEmpty()
        val content = it.stringValue("content")
        if (status.isBlank() && content.isBlank()) {
          null
        } else {
          SessionStreamState(
            status = status,
            content = content,
            startedUtc = it.stringOrNull("started_utc"),
            updatedUtc = it.stringOrNull("updated_utc"),
            turnId = it.stringOrNull("turn_id"),
          )
        }
      }
      SessionSnapshot(history = rows, streamState = streamState)
    } catch (t: Throwable) {
      throw IllegalStateException("session snapshot failed: malformed payload", t)
    }
  }

  private fun parseWorkflowStatus(payload: String): WorkflowStatus {
    return try {
      val root = parseJsonObject(payload)
      val state = root.objectOrEmpty("state")
      val events = root.listOrEmpty("events")
      WorkflowStatus(
        workflowId = root.stringOrNull("workflow_id").orEmpty(),
        status = state.stringOrNull("status").orEmpty(),
        statusDetail = state.stringOrNull("status_detail").orEmpty(),
        turn = state.stringOrNull("turn").orEmpty(),
        activeRole = state.stringOrNull("active_role").orEmpty(),
        activeWaitReason = state.stringOrNull("active_wait_reason").orEmpty(),
        activeProgressNote = state.stringOrNull("active_progress_note").orEmpty(),
        lastMeaningfulProgressUtc = state.stringOrNull("last_meaningful_progress_utc").orEmpty(),
        lastMeaningfulProgressEvent = state.stringOrNull("last_meaningful_progress_event").orEmpty(),
        stuckReason = state.stringOrNull("stuck_reason").orEmpty(),
        iteration = state.intOrDefault("iteration", 1),
        maxIterations = state.intOrDefault("max_iterations", 4),
        phaseLabel = state.stringOrNull("phase_label").orEmpty(),
        objective = state.stringOrNull("objective").orEmpty(),
        verifierVerdict = state.stringOrNull("verifier_verdict").orEmpty(),
        lastError = state.stringOrNull("last_error").orEmpty(),
        intentValidationReport = state.stringOrNull("intent_validation_report").orEmpty(),
        events = buildList {
          for (entry in events) {
            val row = entry as? Map<String, Any?> ?: continue
            val payloadObj = row.objectOrEmpty("payload")
            val payloadMap = buildMap {
              payloadObj.forEach { (key, value) ->
                put(key, value?.toString().orEmpty())
              }
            }
            add(
              WorkflowEvent(
                utc = row.stringOrNull("utc").orEmpty(),
                event = row.stringOrNull("event").orEmpty(),
                payload = payloadMap,
              ),
            )
          }
        },
      )
    } catch (t: Throwable) {
      throw IllegalStateException("workflow status failed: malformed payload", t)
    }
  }

  private fun parseCancelStatus(payload: String): String {
    return try {
      parseJsonObject(payload).stringOrNull("status")?.trim()?.lowercase().orEmpty()
    } catch (_: Throwable) {
      ""
    }
  }

  private fun parseCancelMessage(payload: String): String {
    return try {
      parseJsonObject(payload).stringOrNull("message")?.trim().orEmpty()
    } catch (_: Throwable) {
      ""
    }
  }

  private fun unescapeJson(value: String): String {
    val out = StringBuilder(value.length)
    var i = 0
    while (i < value.length) {
      val c = value[i]
      if (c == '\\' && i + 1 < value.length) {
        val n = value[i + 1]
        when (n) {
          '\\' -> out.append('\\')
          '"' -> out.append('"')
          'n' -> out.append('\n')
          'r' -> out.append('\r')
          't' -> out.append('\t')
          'b' -> out.append('\b')
          'f' -> out.append('\u000c')
          'u' -> {
            if (i + 5 < value.length) {
              val hex = value.substring(i + 2, i + 6)
              val code = hex.toIntOrNull(16)
              if (code != null) {
                out.append(code.toChar())
                i += 6
                continue
              }
            }
            out.append('u')
          }
          else -> out.append(n)
        }
        i += 2
        continue
      }
      out.append(c)
      i += 1
    }
    return out.toString()
  }

  private fun escapeJson(value: String): String {
    val out = StringBuilder(value.length + 8)
    value.forEach { ch ->
      when (ch) {
        '\\' -> out.append("\\\\")
        '"' -> out.append("\\\"")
        '\n' -> out.append("\\n")
        '\r' -> out.append("\\r")
        '\t' -> out.append("\\t")
        '\b' -> out.append("\\b")
        '\u000c' -> out.append("\\f")
        in '\u0000'..'\u001f' -> out.append(String.format("\\u%04x", ch.code))
        else -> out.append(ch)
      }
    }
    return out.toString()
  }

  private fun isTransientNetworkError(t: Throwable): Boolean {
    if (t is IOException) return true
    val msg = (t.message ?: "").lowercase()
    return msg.contains("connection abort") ||
      msg.contains("connection reset") ||
      msg.contains("broken pipe") ||
      msg.contains("socket closed") ||
      msg.contains("timeout")
  }

  private fun nextBackoffMs(attempt: Int): Long {
    val clamped = attempt.coerceIn(1, 10)
    return when (clamped) {
      1 -> 700L
      2 -> 1_200L
      3 -> 2_200L
      4 -> 3_500L
      5 -> 5_000L
      else -> 8_000L
    }
  }

  private fun interruptedReason(detail: String): String {
    val suffix = detail.trim().ifBlank { "stream interrupted before completion" }
    return if (suffix.contains("before completion")) {
      suffix
    } else {
      "stream interrupted before completion: $suffix"
    }
  }
}

private fun parseJsonObject(raw: String, errorMessage: String = "malformed json"): Map<String, Any?> {
  return try {
    SimpleJsonParser(raw).parseObject()
  } catch (t: Throwable) {
    throw IllegalStateException(errorMessage, t)
  }
}

private fun Map<String, Any?>.stringValue(key: String): String {
  return (this[key] as? String).orEmpty()
}

private fun Map<String, Any?>.stringOrNull(key: String): String? {
  return (this[key] as? String)?.trim()?.ifBlank { null }
}

private fun Map<String, Any?>.intOrDefault(key: String, default: Int): Int {
  return when (val value = this[key]) {
    is Number -> value.toInt()
    is String -> value.toIntOrNull() ?: default
    else -> default
  }
}

private fun Map<String, Any?>.objectOrNull(key: String): Map<String, Any?>? {
  @Suppress("UNCHECKED_CAST")
  return this[key] as? Map<String, Any?>
}

private fun Map<String, Any?>.objectOrEmpty(key: String): Map<String, Any?> {
  return objectOrNull(key) ?: emptyMap()
}

private fun Map<String, Any?>.listOrEmpty(key: String): List<Any?> {
  return (this[key] as? List<*>).orEmpty()
}

private fun Map<String, Any?>.booleanOrFalse(key: String): Boolean {
  return when (val value = this[key]) {
    is Boolean -> value
    is String -> value.equals("true", ignoreCase = true)
    is Number -> value.toInt() != 0
    else -> false
  }
}

private class SimpleJsonParser(private val raw: String) {
  private var index: Int = 0

  fun parseObject(): Map<String, Any?> {
    val parsed = parseValue()
    return parsed as? Map<String, Any?>
      ?: throw IllegalArgumentException("Expected top-level object")
  }

  private fun parseValue(): Any? {
    skipWhitespace()
    return when (val ch = peek()) {
      '{' -> parseObjectBody()
      '[' -> parseArray()
      '"' -> parseString()
      '-', in '0'..'9' -> parseNumber()
      't' -> parseLiteral("true", true)
      'f' -> parseLiteral("false", false)
      'n' -> parseLiteral("null", null)
      else -> throw IllegalArgumentException("Unexpected JSON token '$ch' at $index")
    }
  }

  private fun parseObjectBody(): Map<String, Any?> {
    expect('{')
    skipWhitespace()
    if (peek() == '}') {
      index += 1
      return emptyMap()
    }
    val out = linkedMapOf<String, Any?>()
    while (true) {
      skipWhitespace()
      val key = parseString()
      skipWhitespace()
      expect(':')
      val value = parseValue()
      out[key] = value
      skipWhitespace()
      when (peek()) {
        ',' -> index += 1
        '}' -> {
          index += 1
          return out
        }
        else -> throw IllegalArgumentException("Expected ',' or '}' at $index")
      }
    }
  }

  private fun parseArray(): List<Any?> {
    expect('[')
    skipWhitespace()
    if (peek() == ']') {
      index += 1
      return emptyList()
    }
    val out = mutableListOf<Any?>()
    while (true) {
      out += parseValue()
      skipWhitespace()
      when (peek()) {
        ',' -> index += 1
        ']' -> {
          index += 1
          return out
        }
        else -> throw IllegalArgumentException("Expected ',' or ']' at $index")
      }
    }
  }

  private fun parseString(): String {
    expect('"')
    val out = StringBuilder()
    while (index < raw.length) {
      val ch = raw[index++]
      when (ch) {
        '"' -> return out.toString()
        '\\' -> {
          val escaped = raw.getOrNull(index++) ?: throw IllegalArgumentException("Unterminated escape sequence")
          out.append(
            when (escaped) {
              '"', '\\', '/' -> escaped
              'b' -> '\b'
              'f' -> '\u000C'
              'n' -> '\n'
              'r' -> '\r'
              't' -> '\t'
              'u' -> {
                val hex = raw.substring(index, (index + 4).coerceAtMost(raw.length))
                if (hex.length != 4) throw IllegalArgumentException("Invalid unicode escape at $index")
                index += 4
                hex.toInt(16).toChar()
              }
              else -> throw IllegalArgumentException("Invalid escape '\\$escaped' at $index")
            },
          )
        }
        else -> out.append(ch)
      }
    }
    throw IllegalArgumentException("Unterminated string")
  }

  private fun parseNumber(): Number {
    val start = index
    if (peek() == '-') index += 1
    while (peek() in '0'..'9') index += 1
    val isFractional = if (peek() == '.') {
      index += 1
      while (peek() in '0'..'9') index += 1
      true
    } else {
      false
    }
    if (peek() == 'e' || peek() == 'E') {
      index += 1
      if (peek() == '+' || peek() == '-') index += 1
      while (peek() in '0'..'9') index += 1
      return raw.substring(start, index).toDouble()
    }
    val token = raw.substring(start, index)
    return if (isFractional) token.toDouble() else token.toLong()
  }

  private fun parseLiteral(token: String, value: Any?): Any? {
    if (!raw.startsWith(token, index)) {
      throw IllegalArgumentException("Expected '$token' at $index")
    }
    index += token.length
    return value
  }

  private fun skipWhitespace() {
    while (index < raw.length && raw[index].isWhitespace()) {
      index += 1
    }
  }

  private fun expect(ch: Char) {
    skipWhitespace()
    if (peek() != ch) {
      throw IllegalArgumentException("Expected '$ch' at $index")
    }
    index += 1
  }

  private fun peek(): Char {
    return raw.getOrNull(index) ?: '\u0000'
  }
}
