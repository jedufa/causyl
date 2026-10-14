package com.eiel.chat

import android.content.Intent
import android.net.Uri
import android.os.Bundle
import androidx.activity.ComponentActivity
import androidx.activity.compose.setContent
import androidx.compose.foundation.isSystemInDarkTheme
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Surface
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.SideEffect
import androidx.compose.runtime.setValue
import androidx.core.view.WindowCompat
import androidx.core.view.WindowInsetsCompat
import androidx.core.view.WindowInsetsControllerCompat
import com.eiel.chat.ui.AppShell
import com.eiel.chat.ui.SharedDocumentPayload
import com.eiel.chat.ui.theme.EielChatTheme
import com.eiel.chat.util.AppLogger
import java.util.UUID

private const val SHARED_DOCUMENT_MAX_BYTES = 512 * 1024

class MainActivity : ComponentActivity() {
  private var pendingSharedDocument by mutableStateOf<SharedDocumentPayload?>(null)

  override fun onCreate(savedInstanceState: Bundle?) {
    super.onCreate(savedInstanceState)
    AppLogger.init(applicationContext)
    AppLogger.i("MainActivity onCreate")
    pendingSharedDocument = extractSharedDocument(intent)
    WindowCompat.setDecorFitsSystemWindows(window, true)
    setContent {
      val isDarkTheme = isSystemInDarkTheme()
      SideEffect {
        WindowInsetsControllerCompat(window, window.decorView).apply {
          show(WindowInsetsCompat.Type.statusBars())
          isAppearanceLightStatusBars = !isDarkTheme
        }
      }
      EielChatTheme(darkTheme = isDarkTheme) {
        Surface(color = MaterialTheme.colorScheme.background) {
          AppShell(
            sharedDocument = pendingSharedDocument,
            onSharedDocumentConsumed = { pendingSharedDocument = null },
          )
        }
      }
    }
  }

  override fun onNewIntent(intent: Intent) {
    super.onNewIntent(intent)
    setIntent(intent)
    extractSharedDocument(intent)?.let { pendingSharedDocument = it }
  }

  private fun extractSharedDocument(intent: Intent?): SharedDocumentPayload? {
    val safeIntent = intent ?: return null
    if (safeIntent.action != Intent.ACTION_SEND) return null
    val sharedText = safeIntent.getStringExtra(Intent.EXTRA_TEXT).orEmpty()
    val stream = safeIntent.getParcelableExtra(Intent.EXTRA_STREAM, Uri::class.java)
    val mimeType = safeIntent.type.orEmpty()
    val sharedBytes = readSharedBytes(stream)
    val filename = resolveSharedFilename(stream).ifBlank {
      if (sharedText.isNotBlank()) "shared_text.txt" else "shared_document"
    }
    val text = sharedText.ifBlank { decodeSharedText(sharedBytes.bytes) }
    if (text.isBlank() && sharedBytes.bytes.isEmpty()) return null
    return SharedDocumentPayload(
      requestKey = UUID.randomUUID().toString(),
      filename = filename,
      mimeType = mimeType,
      text = text,
      bytes = sharedBytes.bytes,
      truncated = sharedBytes.truncated,
    )
  }

  private fun resolveSharedFilename(uri: Uri?): String {
    val safeUri = uri ?: return ""
    return runCatching {
      contentResolver.query(safeUri, arrayOf(android.provider.OpenableColumns.DISPLAY_NAME), null, null, null)
        ?.use { cursor ->
          if (cursor.moveToFirst()) {
            cursor.getString(0).orEmpty()
          } else {
            ""
          }
        }
        .orEmpty()
    }.getOrElse { safeUri.lastPathSegment.orEmpty() }
  }

  private fun readSharedBytes(uri: Uri?): SharedReadResult {
    val safeUri = uri ?: return SharedReadResult()
    return runCatching {
      contentResolver.openInputStream(safeUri)?.use { input ->
        val buffer = ByteArray(16 * 1024)
        val out = java.io.ByteArrayOutputStream()
        var total = 0
        var truncated = false
        while (true) {
          val read = input.read(buffer)
          if (read <= 0) break
          val room = SHARED_DOCUMENT_MAX_BYTES - total
          if (room <= 0) {
            truncated = true
            break
          }
          val toWrite = minOf(read, room)
          out.write(buffer, 0, toWrite)
          total += toWrite
          if (toWrite < read) {
            truncated = true
            break
          }
        }
        SharedReadResult(bytes = out.toByteArray(), truncated = truncated)
      } ?: SharedReadResult()
    }.getOrDefault(SharedReadResult())
  }

  private fun decodeSharedText(bytes: ByteArray): String {
    if (bytes.isEmpty()) return ""
    return runCatching {
      val decoded = bytes.toString(Charsets.UTF_8)
      if (decoded.contains('\u0000')) "" else decoded
    }.getOrDefault("")
  }

  private data class SharedReadResult(
    val bytes: ByteArray = byteArrayOf(),
    val truncated: Boolean = false,
  )
}
