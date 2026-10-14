package com.eiel.chat.ui

import android.content.Context
import android.os.Handler
import android.os.Looper
import android.os.SystemClock
import android.os.Bundle
import android.speech.tts.TextToSpeech
import android.speech.tts.UtteranceProgressListener
import java.util.Locale

private enum class SpeechInitState {
  PENDING,
  READY,
  FAILED,
}

private data class PendingSpeechRequest(
  val messageId: String,
  val text: String,
)

/** Small controller around Android TextToSpeech for bubble playback. */
internal class BubbleSpeechController(
  context: Context,
) {
  private val appContext = context.applicationContext
  private val mainHandler = Handler(Looper.getMainLooper())
  private var tts: TextToSpeech? = null
  private var initState: SpeechInitState = SpeechInitState.PENDING
  private var pendingRequest: PendingSpeechRequest? = null
  private var activeMessageId: String? = null
  private var activeUtteranceId: String? = null
  private var activeUtteranceIds: Set<String> = emptySet()
  private var shutdownRequested: Boolean = false
  private var engineGeneration: Long = 0L

  var onSpeechStateChanged: ((String?) -> Unit)? = null
  var onError: ((String) -> Unit)? = null

  init {
    initializeTts()
  }

  private fun handleOnInit(generation: Long, status: Int) {
    if (!isCurrentEngine(generation)) return
    if (status != TextToSpeech.SUCCESS) {
      initState = SpeechInitState.FAILED
      emitError("Text to speech unavailable")
      return
    }

    val resolvedTts = tts
    if (resolvedTts == null) {
      initState = SpeechInitState.FAILED
      emitError("Text to speech unavailable")
      return
    }

    if (!configureVoice(resolvedTts)) {
      initState = SpeechInitState.FAILED
      emitError("No supported voice found")
      return
    }

    initState = SpeechInitState.READY
    pendingRequest?.also {
      pendingRequest = null
      speakInternal(it)
    }
  }

  /** Start speaking the given message body, or queue it until TTS is ready. */
  fun speak(messageId: String, text: String) {
    val normalized = text.trim()
    if (normalized.isBlank()) {
      emitError("Nothing to speak")
      return
    }
    if (shutdownRequested) {
      emitError("Text to speech unavailable")
      return
    }
    if (tts == null || initState == SpeechInitState.FAILED) {
      pendingRequest = PendingSpeechRequest(messageId = messageId, text = normalized)
      initializeTts()
      return
    }
    when (initState) {
      SpeechInitState.PENDING -> pendingRequest = PendingSpeechRequest(messageId = messageId, text = normalized)
      SpeechInitState.READY -> speakInternal(PendingSpeechRequest(messageId = messageId, text = normalized))
      SpeechInitState.FAILED -> emitError("Text to speech unavailable")
    }
  }

  /** Stop the current utterance and clear active bubble state. */
  fun stop() {
    pendingRequest = null
    activeMessageId = null
    activeUtteranceId = null
    activeUtteranceIds = emptySet()
    tts?.stop()
    emitSpeechState(null)
  }

  /** Release TTS resources when the chat screen leaves composition. */
  fun shutdown() {
    shutdownRequested = true
    pendingRequest = null
    activeMessageId = null
    activeUtteranceId = null
    activeUtteranceIds = emptySet()
    onSpeechStateChanged = null
    onError = null
    engineGeneration += 1L
    tts?.stop()
    tts?.shutdown()
    tts = null
    initState = SpeechInitState.FAILED
  }

  private fun initializeTts() {
    if (shutdownRequested) return
    val oldTts = tts
    engineGeneration += 1L
    val generation = engineGeneration
    tts = null
    activeMessageId = null
    activeUtteranceId = null
    activeUtteranceIds = emptySet()
    initState = SpeechInitState.PENDING
    val engine = TextToSpeech(appContext) { status ->
      handleOnInit(generation, status)
    }
    engine.setOnUtteranceProgressListener(
      object : UtteranceProgressListener() {
        override fun onStart(utteranceId: String?) {
          if (!isCurrentEngine(generation)) return
          if (utteranceId != null && utteranceId == activeUtteranceId) {
            emitSpeechState(activeMessageId)
          }
        }

        override fun onDone(utteranceId: String?) {
          clearIfMatching(generation, utteranceId)
        }

        @Deprecated("Deprecated in Java")
        override fun onError(utteranceId: String?) {
          if (!isCurrentEngine(generation)) return
          clearIfMatching(generation, utteranceId)
          emitError("Speech playback failed")
          initializeTts()
        }

        override fun onError(utteranceId: String?, errorCode: Int) {
          if (!isCurrentEngine(generation)) return
          clearIfMatching(generation, utteranceId)
          emitError("Speech playback failed")
          initializeTts()
        }

        override fun onStop(utteranceId: String?, interrupted: Boolean) {
          clearIfMatching(generation, utteranceId)
        }
      },
    )
    tts = engine
    oldTts?.shutdown()
  }

  private fun speakInternal(request: PendingSpeechRequest) {
    val resolvedTts = tts
    if (resolvedTts == null) {
      emitError("Text to speech unavailable")
      return
    }
    val chunks = chunkSpeechText(request.text)
    if (chunks.isEmpty()) {
      emitError("Nothing to speak")
      return
    }
    activeMessageId = request.messageId
    val utteranceSeed = "${request.messageId}:${SystemClock.uptimeMillis()}"
    val utteranceIds = chunks.indices.map { idx -> "$utteranceSeed:$idx" }
    activeUtteranceId = utteranceIds.lastOrNull()
    activeUtteranceIds = utteranceIds.toSet()
    emitSpeechState(request.messageId)
    val params = Bundle()
    var failed = false
    chunks.forEachIndexed { index, chunk ->
      val result = resolvedTts.speak(
        chunk,
        if (index == 0) TextToSpeech.QUEUE_FLUSH else TextToSpeech.QUEUE_ADD,
        params,
        utteranceIds[index],
      )
      if (result != TextToSpeech.SUCCESS) {
        failed = true
        return@forEachIndexed
      }
    }
    if (failed) {
      clearActiveSpeech()
      emitError("Speech playback failed")
      initializeTts()
    }
  }

  private fun clearIfMatching(generation: Long, utteranceId: String?) {
    if (!isCurrentEngine(generation)) return
    if (utteranceId == null) {
      clearActiveSpeech()
      return
    }
    if (utteranceId !in activeUtteranceIds) return
    if (utteranceId != activeUtteranceId) return
    clearActiveSpeech()
  }

  private fun isCurrentEngine(generation: Long): Boolean {
    return !shutdownRequested && generation == engineGeneration
  }

  private fun configureVoice(resolvedTts: TextToSpeech): Boolean {
    val preferredLocales = buildList {
      add(Locale.getDefault())
      resolvedTts.voice?.locale?.let { add(it) }
      add(Locale.US)
    }.distinct()
    for (locale in preferredLocales) {
      val result = resolvedTts.setLanguage(locale)
      if (result != TextToSpeech.LANG_MISSING_DATA && result != TextToSpeech.LANG_NOT_SUPPORTED) {
        return true
      }
    }
    return false
  }

  private fun clearActiveSpeech() {
    activeMessageId = null
    activeUtteranceId = null
    activeUtteranceIds = emptySet()
    emitSpeechState(null)
  }

  private fun chunkSpeechText(text: String): List<String> {
    val normalized = text
      .replace('\u0000', ' ')
      .replace(Regex("\\s+"), " ")
      .trim()
    if (normalized.isBlank()) return emptyList()
    val maxLen = TextToSpeech.getMaxSpeechInputLength().coerceAtLeast(1)
    if (normalized.length <= maxLen) return listOf(normalized)

    val chunks = mutableListOf<String>()
    var remaining = normalized
    while (remaining.isNotBlank()) {
      if (remaining.length <= maxLen) {
        chunks += remaining.trim()
        break
      }
      var splitAt = remaining.lastIndexOf(' ', startIndex = maxLen)
      if (splitAt <= 0) {
        splitAt = maxLen
      }
      chunks += remaining.substring(0, splitAt).trim()
      remaining = remaining.substring(splitAt).trimStart()
    }
    return chunks.filter { it.isNotBlank() }
  }

  private fun emitSpeechState(messageId: String?) {
    mainHandler.post {
      onSpeechStateChanged?.invoke(messageId)
    }
  }

  private fun emitError(message: String) {
    mainHandler.post {
      onError?.invoke(message)
    }
  }
}
