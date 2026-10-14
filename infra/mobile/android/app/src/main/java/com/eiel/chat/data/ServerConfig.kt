package com.eiel.chat.data

import android.content.Context
import com.eiel.chat.BuildConfig
import java.net.URI

object ServerConfig {
  private const val PREFS_NAME = "server_config"
  private const val KEY_BASE_URL = "server_base_url"

  fun resolveBaseUrl(context: Context): String {
    val prefs = context.getSharedPreferences(PREFS_NAME, Context.MODE_PRIVATE)
    val stored = prefs.getString(KEY_BASE_URL, null)
    return normalizeBaseUrl(stored ?: BuildConfig.SERVER_BASE_URL)
  }

  fun saveBaseUrl(context: Context, raw: String) {
    val normalized = normalizeBaseUrl(raw)
    context
      .getSharedPreferences(PREFS_NAME, Context.MODE_PRIVATE)
      .edit()
      .putString(KEY_BASE_URL, normalized)
      .apply()
  }

  fun resetBaseUrl(context: Context) {
    context
      .getSharedPreferences(PREFS_NAME, Context.MODE_PRIVATE)
      .edit()
      .remove(KEY_BASE_URL)
      .apply()
  }

  fun normalizeBaseUrl(raw: String?): String {
    val fallback = sanitizeBaseUrl(BuildConfig.SERVER_BASE_URL) ?: BuildConfig.SERVER_BASE_URL.trim().trimEnd('/')
    return sanitizeBaseUrl(raw) ?: fallback
  }

  private fun sanitizeBaseUrl(raw: String?): String? {
    var candidate = raw?.trim().orEmpty()
    if (candidate.isBlank()) return null
    candidate = candidate
      .replace('“', '"')
      .replace('”', '"')
      .replace('‘', '\'')
      .replace('’', '\'')
      .replace(Regex("(?<=\\d)[\"'](?=\\d{2,5}(?:/|$))"), ":")
      .replace("\"", "")
      .replace("'", "")
      .replace(Regex("\\s+"), "")
      .trimEnd('/')
    if (!candidate.contains("://")) {
      candidate = "http://$candidate"
    }
    val uri = runCatching { URI(candidate) }.getOrNull() ?: return null
    val scheme = (uri.scheme ?: "http").lowercase()
    val host = uri.host ?: return null
    val path = uri.rawPath?.trimEnd('/').orEmpty()
    return buildString {
      append(scheme)
      append("://")
      append(host)
      if (uri.port >= 0) {
        append(':')
        append(uri.port)
      }
      if (path.isNotBlank() && path != "/") {
        append(path)
      }
    }
  }
}
