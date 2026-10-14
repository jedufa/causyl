package com.eiel.chat.data

import android.content.Context
import android.content.SharedPreferences
import com.eiel.chat.BuildConfig
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.withContext
import okhttp3.OkHttpClient
import okhttp3.Request
import org.json.JSONObject
import java.util.concurrent.TimeUnit

data class MobileRuntimePolicy(
  val chatTurnTimeoutS: Int,
  val chatIdleTimeoutS: Int,
  val workflowTimeoutS: Int,
  val fetchedAtMs: Long,
)

interface MobileRuntimePolicySource {
  fun current(): MobileRuntimePolicy
  suspend fun refresh(): MobileRuntimePolicy?
}

class MobileRuntimePolicyStore(
  private val prefs: SharedPreferences,
) {
  fun read(): MobileRuntimePolicy {
    return MobileRuntimePolicy(
      chatTurnTimeoutS = coerceTimeout(prefs.getInt(KEY_CHAT_TURN_TIMEOUT_S, BuildConfig.CHAT_TIMEOUT_S), BuildConfig.CHAT_TIMEOUT_S),
      chatIdleTimeoutS = coerceTimeout(prefs.getInt(KEY_CHAT_IDLE_TIMEOUT_S, BuildConfig.CHAT_IDLE_TIMEOUT_S), BuildConfig.CHAT_IDLE_TIMEOUT_S),
      workflowTimeoutS = coerceTimeout(prefs.getInt(KEY_WORKFLOW_TIMEOUT_S, BuildConfig.WORKFLOW_TIMEOUT_S), BuildConfig.WORKFLOW_TIMEOUT_S),
      fetchedAtMs = prefs.getLong(KEY_FETCHED_AT_MS, 0L),
    )
  }

  fun write(policy: MobileRuntimePolicy) {
    prefs.edit()
      .putInt(KEY_CHAT_TURN_TIMEOUT_S, policy.chatTurnTimeoutS)
      .putInt(KEY_CHAT_IDLE_TIMEOUT_S, policy.chatIdleTimeoutS)
      .putInt(KEY_WORKFLOW_TIMEOUT_S, policy.workflowTimeoutS)
      .putLong(KEY_FETCHED_AT_MS, policy.fetchedAtMs)
      .apply()
  }

  companion object {
    private const val PREFS_NAME = "mobile_runtime_policy"
    private const val KEY_CHAT_TURN_TIMEOUT_S = "chat_turn_timeout_s"
    private const val KEY_CHAT_IDLE_TIMEOUT_S = "chat_idle_timeout_s"
    private const val KEY_WORKFLOW_TIMEOUT_S = "workflow_timeout_s"
    private const val KEY_FETCHED_AT_MS = "fetched_at_ms"

    fun fromContext(context: Context): MobileRuntimePolicyStore {
      val prefs = context.getSharedPreferences(PREFS_NAME, Context.MODE_PRIVATE)
      return MobileRuntimePolicyStore(prefs)
    }

    private fun coerceTimeout(value: Int, fallback: Int): Int {
      val candidate = if (value > 0) value else fallback
      return candidate.coerceIn(30, 6 * 60 * 60)
    }
  }
}

class HttpMobileRuntimePolicySource(
  context: Context,
  private val client: OkHttpClient = OkHttpClient.Builder()
    .retryOnConnectionFailure(true)
    .connectTimeout(10, TimeUnit.SECONDS)
    .writeTimeout(10, TimeUnit.SECONDS)
    .readTimeout(10, TimeUnit.SECONDS)
    .callTimeout(15, TimeUnit.SECONDS)
    .build(),
  serverBaseUrl: String = BuildConfig.SERVER_BASE_URL,
  private val serverBaseUrlProvider: (() -> String)? = null,
  private val authToken: String = BuildConfig.AUTH_TOKEN,
  private val store: MobileRuntimePolicyStore = MobileRuntimePolicyStore.fromContext(context),
) : MobileRuntimePolicySource {
  private val fallbackBaseUrl = ServerConfig.normalizeBaseUrl(serverBaseUrl)

  override fun current(): MobileRuntimePolicy = store.read()

  override suspend fun refresh(): MobileRuntimePolicy? = withContext(Dispatchers.IO) {
    val req = Request.Builder()
      .url("${baseUrl()}/api/mobile/runtime_policy")
      .get()
      .also { builder ->
        if (authToken.isNotBlank()) builder.addHeader("Authorization", "Bearer $authToken")
      }
      .build()
    return@withContext try {
      client.newCall(req).execute().use { response ->
        if (!response.isSuccessful) return@use null
        val body = response.body?.string().orEmpty()
        val parsed = parsePolicy(body, current())
        store.write(parsed)
        parsed
      }
    } catch (_: Throwable) {
      null
    }
  }

  private fun baseUrl(): String = ServerConfig.normalizeBaseUrl(serverBaseUrlProvider?.invoke() ?: fallbackBaseUrl)

  private fun parsePolicy(body: String, fallback: MobileRuntimePolicy): MobileRuntimePolicy {
    val obj = JSONObject(body)
    return MobileRuntimePolicy(
      chatTurnTimeoutS = parseTimeout(obj, "chat_turn_timeout_s", fallback.chatTurnTimeoutS),
      chatIdleTimeoutS = parseTimeout(obj, "chat_idle_timeout_s", fallback.chatIdleTimeoutS),
      workflowTimeoutS = parseTimeout(obj, "workflow_timeout_s", fallback.workflowTimeoutS),
      fetchedAtMs = System.currentTimeMillis(),
    )
  }

  private fun parseTimeout(obj: JSONObject, key: String, fallback: Int): Int {
    val candidate = when (val value = obj.opt(key)) {
      is Number -> value.toInt()
      is String -> value.toIntOrNull() ?: fallback
      else -> fallback
    }
    return candidate.coerceIn(30, 6 * 60 * 60)
  }
}
