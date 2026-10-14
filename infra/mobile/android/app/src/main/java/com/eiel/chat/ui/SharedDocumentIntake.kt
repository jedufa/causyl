package com.eiel.chat.ui

import java.util.Locale

enum class SharedDocumentType(
  val label: String,
  val routeName: String,
) {
  PIS("PIS", "BACKLOG"),
  PATCH("Patch", "ADMIN"),
  AUDIT_REPORT("Audit report", "BACKLOG"),
  PATCH_REPORT("Patch report", "BACKLOG"),
}

data class SharedDocumentPayload(
  val requestKey: String,
  val filename: String,
  val mimeType: String,
  val text: String,
  val bytes: ByteArray = byteArrayOf(),
  val truncated: Boolean = false,
)

internal fun inferSharedDocumentType(payload: SharedDocumentPayload): SharedDocumentType {
  val name = payload.filename.lowercase(Locale.US)
  val mime = payload.mimeType.lowercase(Locale.US)
  val text = payload.text.lowercase(Locale.US)
  return when {
    name.endsWith(".patch") || name.endsWith(".diff") || mime.contains("diff") -> SharedDocumentType.PATCH
    "patch report" in text || "patch report" in name -> SharedDocumentType.PATCH_REPORT
    "audit report" in text || "audit report" in name -> SharedDocumentType.AUDIT_REPORT
    "pis-" in text || name.contains("pis") || text.contains("\n## problem") -> SharedDocumentType.PIS
    else -> SharedDocumentType.PIS
  }
}

internal fun sharedDocumentTitle(
  type: SharedDocumentType,
  filename: String,
  text: String,
): String {
  val heading = text.lineSequence()
    .map { it.trim() }
    .firstOrNull { it.startsWith("# ") }
    ?.removePrefix("# ")
    ?.trim()
    .orEmpty()
  if (heading.isNotBlank()) return heading
  val firstLine = text.lineSequence()
    .map { it.trim() }
    .firstOrNull { it.isNotBlank() }
    .orEmpty()
    .take(120)
  val baseName = filename.substringBeforeLast('.').replace('_', ' ').replace('-', ' ').trim()
  val fallback = when (type) {
    SharedDocumentType.PIS -> "Shared PIS import"
    SharedDocumentType.PATCH -> "Shared patch"
    SharedDocumentType.AUDIT_REPORT -> "Shared audit report"
    SharedDocumentType.PATCH_REPORT -> "Shared patch report"
  }
  return heading.ifBlank {
    firstLine.ifBlank {
      baseName.ifBlank { fallback }
    }
  }
}

internal fun buildSharedImportMarkdown(
  type: SharedDocumentType,
  title: String,
  text: String,
  filename: String,
  mimeType: String,
): String {
  val body = text.trim().ifBlank { "Shared document content was empty." }
  val normalizedTitle = title.trim().ifBlank { sharedDocumentTitle(type, filename, text) }
  return when {
    type == SharedDocumentType.PIS && body.startsWith("# ") -> body
    else -> buildString {
      appendLine("# $normalizedTitle")
      appendLine()
      appendLine("## Imported document")
      appendLine()
      appendLine("- Type: ${type.label}")
      appendLine("- Source filename: ${filename.ifBlank { "shared_document.txt" }}")
      appendLine("- Source mime: ${mimeType.ifBlank { "unknown" }}")
      appendLine()
      appendLine("## Body")
      appendLine()
      appendLine(body)
      appendLine()
      appendLine("## Intake note")
      appendLine()
      appendLine("- Imported from Android share into the plane.")
      appendLine("- Preserve the original document text as imported source.")
    }
  }
}
