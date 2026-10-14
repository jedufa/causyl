package com.eiel.chat.util

import android.content.Context
import android.util.Log
import java.io.File
import java.io.PrintWriter
import java.io.StringWriter
import java.time.Instant

object AppLogger {
  private const val TAG = "EIELChat"
  private const val LOG_FILE = "mobile_app.log"
  private const val MAX_BYTES = 1_000_000L
  private const val BACKUPS = 2

  @Volatile
  private var initialized = false
  private var logDir: File? = null

  fun init(context: Context) {
    if (initialized) return
    synchronized(this) {
      if (initialized) return
      logDir = context.filesDir
      initialized = true
      i("logger initialized")
    }
  }

  fun i(message: String) = write("I", message, null)
  fun w(message: String) = write("W", message, null)
  fun e(message: String, t: Throwable? = null) = write("E", message, t)

  private fun write(level: String, message: String, t: Throwable?) {
    try {
      when (level) {
        "E" -> Log.e(TAG, message, t)
        "W" -> Log.w(TAG, message, t)
        else -> Log.i(TAG, message, t)
      }
    } catch (_: Throwable) {
      // JVM unit tests may not have android.util.Log runtime.
    }
    val dir = logDir ?: return
    synchronized(this) {
      try {
        val log = File(dir, LOG_FILE)
        rotateIfNeeded(log)
        val line = buildLine(level, message, t)
        log.appendText(line)
      } catch (_: Throwable) {
        // Never crash app because logging fails.
      }
    }
  }

  private fun rotateIfNeeded(log: File) {
    val cur = if (log.exists()) log.length() else 0L
    if (cur < MAX_BYTES) return
    for (i in BACKUPS downTo 1) {
      val src = if (i == 1) log else File(log.parentFile, "$LOG_FILE.${i - 1}")
      val dst = File(log.parentFile, "$LOG_FILE.$i")
      if (src.exists()) src.renameTo(dst)
    }
  }

  private fun buildLine(level: String, message: String, t: Throwable?): String {
    val ts = Instant.now().toString()
    val b = StringBuilder(256)
    b.append(ts).append(' ').append(level).append(' ').append(message)
    if (t != null) {
      b.append('\n')
      b.append(stackTrace(t))
    }
    b.append('\n')
    return b.toString()
  }

  private fun stackTrace(t: Throwable): String {
    val sw = StringWriter()
    val pw = PrintWriter(sw)
    t.printStackTrace(pw)
    pw.flush()
    return sw.toString()
  }
}
