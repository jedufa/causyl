package com.eiel.chat.ui

import androidx.compose.foundation.background
import androidx.compose.foundation.border
import androidx.compose.foundation.clickable
import androidx.compose.foundation.horizontalScroll
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.foundation.text.ClickableText
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.outlined.ContentCopy
import androidx.compose.material3.Icon
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Surface
import androidx.compose.material3.Text
import androidx.compose.material3.TextButton
import androidx.compose.runtime.Composable
import androidx.compose.runtime.remember
import androidx.compose.runtime.saveable.rememberSaveable
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.text.AnnotatedString
import androidx.compose.ui.text.SpanStyle
import androidx.compose.ui.text.buildAnnotatedString
import androidx.compose.ui.text.font.FontFamily
import androidx.compose.ui.text.font.FontStyle
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.style.TextDecoration
import androidx.compose.ui.text.withStyle
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import java.io.File

sealed interface MarkdownBlock {
  data class Paragraph(val text: String) : MarkdownBlock
  data class Heading(val level: Int, val text: String) : MarkdownBlock
  data class BulletList(val items: List<String>) : MarkdownBlock
  data class Quote(val text: String) : MarkdownBlock
  data class CodeFence(val language: String, val code: String) : MarkdownBlock
}

internal enum class SemanticBlockKind(
  val marker: String,
  val label: String,
) {
  DECISION(marker = "decision", label = "Decision"),
  NEXT(marker = "next", label = "Next"),
  WARNING(marker = "warning", label = "Warning");

  companion object {
    fun fromMarker(raw: String): SemanticBlockKind? {
      return entries.firstOrNull { it.marker == raw.trim().lowercase() }
    }
  }
}

internal sealed interface MessageRenderBlock {
  data class Markdown(val block: MarkdownBlock) : MessageRenderBlock
  data class SemanticCallout(val kind: SemanticBlockKind, val text: String) : MessageRenderBlock
  data class TechnicalDetails(
    val title: String,
    val summary: String,
    val blocks: List<MarkdownBlock>,
  ) : MessageRenderBlock
}

internal data class CodeHighlightPalette(
  val keyword: Color,
  val string: Color,
  val comment: Color,
  val number: Color,
  val syntax: Color,
)

private data class InlineSemanticMatch(
  val displayText: String,
  val style: SpanStyle,
  val consumedChars: Int,
  val fileReference: String? = null,
)

private data class InlineRenderModel(
  val text: AnnotatedString,
  val fileReferences: List<String>,
)

private const val CurrentRepoAbsolutePrefix = "/home/user/causyl/"
private const val HomeRepoAbsolutePrefix = "/home/user/"
private val RepoAbsoluteDisplayRegex = Regex("""/home/user/[^/\s]+/[A-Za-z0-9._/\-]+""")
private val CurrentRepoRoot = File(CurrentRepoAbsolutePrefix.removeSuffix("/"))
private const val HostVerificationRequestMarker = "HOST_VERIFICATION_REQUEST_JSON:"
internal const val FileReferenceAnnotationTag = "repo_file_reference"

private fun normalizeChatTextForDisplay(raw: String): String {
  val withoutControlPayload = stripHostVerificationRequestPayload(raw)
  return compactLegacyHostVerificationResult(withoutControlPayload)
}

private fun stripHostVerificationRequestPayload(raw: String): String {
  val markerIndex = raw.indexOf(HostVerificationRequestMarker)
  if (markerIndex < 0) return raw
  val visible = raw.substring(0, markerIndex).trimEnd()
  if (visible.isNotBlank()) return visible
  val actionId = Regex("\"action_id\"\\s*:\\s*\"([^\"]+)\"")
    .find(raw.substring(markerIndex))
    ?.groupValues
    ?.getOrNull(1)
    ?.trim()
    .orEmpty()
  return if (actionId.isNotBlank()) {
    "Requested host verification for $actionId."
  } else {
    "Requested host verification."
  }
}

private fun compactLegacyHostVerificationResult(raw: String): String {
  val text = raw.trim()
  if (!text.startsWith("Host verification result:")) return text
  val lines = text.lines()
  if (lines.size <= 1) return text
  val parsed = linkedMapOf<String, String>()
  for (line in lines.drop(1)) {
    val match = Regex("""^\s*-\s*([a-z_]+)\s*:\s*(.*)$""").matchEntire(line) ?: return text
    parsed[match.groupValues[1].trim().lowercase()] = match.groupValues[2].trim()
  }
  if (parsed.isEmpty()) return text
  val ok = parsed["ok"].orEmpty().equals("true", ignoreCase = true)
  val actionId = parsed["action_id"].orEmpty().ifBlank { "host verification" }
  val failureKind = parsed["failure_kind"].orEmpty()
  val summary = parsed["summary"].orEmpty()
  val statusLine = buildString {
    append(if (ok) "Passed" else "Failed")
    append(": ")
    append(actionId)
    if (!ok && failureKind.isNotBlank() && failureKind != "(none)") {
      append(" (")
      append(failureKind)
      append(")")
    }
  }
  return buildString {
    append("Host verification result:")
    append('\n')
    append(statusLine)
    if (summary.isNotBlank() && summary != "(no summary)") {
      append('\n')
      append(summary)
    }
  }
}

/** Parse a small markdown subset suitable for chat replies. */
fun parseMarkdownBlocks(raw: String): List<MarkdownBlock> {
  val src = shortenDisplayPaths(normalizeChatTextForDisplay(raw).replace("\r\n", "\n").replace('\r', '\n'))
  if (src.isBlank()) return listOf(MarkdownBlock.Paragraph(""))
  val lines = src.split('\n')
  val blocks = mutableListOf<MarkdownBlock>()
  var i = 0

  fun isBulletLine(line: String): Boolean {
    val t = line.trimStart()
    if (t.startsWith("- ") || t.startsWith("* ") || t.startsWith("+ ")) return true
    var j = 0
    while (j < t.length && t[j].isDigit()) j += 1
    return j > 0 && j + 1 < t.length && t[j] == '.' && t[j + 1] == ' '
  }

  fun isHeadingLine(line: String): Boolean {
    val t = line.trim()
    if (!t.startsWith("#")) return false
    var level = 0
    while (level < t.length && t[level] == '#') level += 1
    return level in 1..6 && level < t.length && t[level] == ' '
  }

  while (i < lines.size) {
    val line = lines[i]
    val trimmed = line.trim()
    if (trimmed.isBlank()) {
      i += 1
      continue
    }

    if (trimmed.startsWith("```")) {
      val language = trimmed.removePrefix("```").trim()
      i += 1
      val codeLines = mutableListOf<String>()
      while (i < lines.size && !lines[i].trim().startsWith("```")) {
        codeLines += lines[i]
        i += 1
      }
      if (i < lines.size && lines[i].trim().startsWith("```")) i += 1
      blocks += MarkdownBlock.CodeFence(language = language, code = codeLines.joinToString("\n"))
      continue
    }

    if (isHeadingLine(trimmed)) {
      var level = 0
      while (level < trimmed.length && trimmed[level] == '#') level += 1
      blocks += MarkdownBlock.Heading(level = level, text = trimmed.substring(level + 1).trim())
      i += 1
      continue
    }

    if (isBulletLine(line)) {
      val items = mutableListOf<String>()
      while (i < lines.size && isBulletLine(lines[i])) {
        val t = lines[i].trimStart()
        val content = when {
          t.startsWith("- ") || t.startsWith("* ") || t.startsWith("+ ") -> t.substring(2).trim()
          else -> t.substringAfter(". ", "").trim()
        }
        items += content
        i += 1
      }
      blocks += MarkdownBlock.BulletList(items)
      continue
    }

    if (trimmed.startsWith(">")) {
      val parts = mutableListOf<String>()
      while (i < lines.size && lines[i].trimStart().startsWith(">")) {
        parts += lines[i].trimStart().removePrefix(">").trim()
        i += 1
      }
      blocks += MarkdownBlock.Quote(parts.joinToString("\n"))
      continue
    }

    val paragraph = mutableListOf<String>()
    while (i < lines.size) {
      val candidate = lines[i]
      val candidateTrimmed = candidate.trim()
      if (
        candidateTrimmed.isBlank() ||
        candidateTrimmed.startsWith("```") ||
        candidateTrimmed.startsWith(">") ||
        isBulletLine(candidate) ||
        isHeadingLine(candidateTrimmed)
      ) {
        break
      }
      paragraph += candidate.trimEnd()
      i += 1
    }
    if (paragraph.isNotEmpty()) {
      blocks += MarkdownBlock.Paragraph(paragraph.joinToString("\n"))
    }
  }

  return if (blocks.isEmpty()) listOf(MarkdownBlock.Paragraph(src)) else blocks
}

internal fun parseMessageRenderBlocks(raw: String): List<MessageRenderBlock> {
  val src = shortenDisplayPaths(normalizeChatTextForDisplay(raw).replace("\r\n", "\n").replace('\r', '\n'))
  if (src.isBlank()) return listOf(MessageRenderBlock.Markdown(MarkdownBlock.Paragraph("")))
  val lines = src.split('\n')
  val blocks = mutableListOf<MessageRenderBlock>()
  val markdownBuffer = mutableListOf<String>()
  var i = 0
  var inFence = false

  fun flushMarkdownBuffer() {
    if (markdownBuffer.isEmpty()) return
    val chunk = markdownBuffer.joinToString("\n")
    parseMarkdownBlocks(chunk).forEach { blocks += MessageRenderBlock.Markdown(it) }
    markdownBuffer.clear()
  }

  while (i < lines.size) {
    val line = lines[i]
    val trimmed = line.trim()

    if (!inFence && trimmed.startsWith(":::")) {
      val singleLine = parseSingleLineSemanticCallout(trimmed)
      if (singleLine != null) {
        flushMarkdownBuffer()
        blocks += singleLine
        i += 1
        continue
      }
      val kind = SemanticBlockKind.fromMarker(trimmed.removePrefix(":::"))
      if (kind != null) {
        var j = i + 1
        var blockFence = false
        var closed = false
        while (j < lines.size) {
          val candidateTrimmed = lines[j].trim()
          if (candidateTrimmed.startsWith("```")) {
            blockFence = !blockFence
          } else if (!blockFence && candidateTrimmed == ":::") {
            closed = true
            break
          }
          j += 1
        }
        if (closed) {
          flushMarkdownBuffer()
          val body = lines.subList(i + 1, j).joinToString("\n").trim('\n')
          blocks += MessageRenderBlock.SemanticCallout(kind = kind, text = body)
          i = j + 1
          continue
        }
      }
    }

    markdownBuffer += line
    if (trimmed.startsWith("```")) {
      inFence = !inFence
    }
    i += 1
  }

  flushMarkdownBuffer()
  val compacted = compactTechnicalSections(blocks)
  return if (compacted.isEmpty()) listOf(MessageRenderBlock.Markdown(MarkdownBlock.Paragraph(src))) else compacted
}

private fun compactTechnicalSections(blocks: List<MessageRenderBlock>): List<MessageRenderBlock> {
  if (blocks.isEmpty()) return blocks
  val out = mutableListOf<MessageRenderBlock>()
  var idx = 0
  while (idx < blocks.size) {
    val current = blocks[idx]
    val currentMarkdown = (current as? MessageRenderBlock.Markdown)?.block as? MarkdownBlock.Paragraph
    if (currentMarkdown != null) {
      val title = currentMarkdown.text.trim()
      if (title.endsWith(":")) {
        val titleBody = title.removeSuffix(":").trim()
        val detailBlocks = mutableListOf<MarkdownBlock>()
        var next = idx + 1
        while (next < blocks.size) {
          val markdown = (blocks[next] as? MessageRenderBlock.Markdown)?.block ?: break
          if (detailBlocks.isNotEmpty() && markdown is MarkdownBlock.Paragraph && markdown.text.trim().endsWith(":")) {
            break
          }
          detailBlocks += markdown
          next += 1
        }
        if (detailBlocks.isNotEmpty() && shouldCollapseTechnicalSection(titleBody = titleBody, detailBlocks = detailBlocks)) {
          out += MessageRenderBlock.TechnicalDetails(
            title = technicalSectionDisplayTitle(titleBody),
            summary = summarizeTechnicalSection(detailBlocks),
            blocks = detailBlocks,
          )
          idx = next
          continue
        }
      }
    }
    out += current
    idx += 1
  }
  return out
}

private fun shouldCollapseTechnicalSection(
  titleBody: String,
  detailBlocks: List<MarkdownBlock>,
): Boolean {
  val normalizedTitle = titleBody.trim().lowercase()
  if (normalizedTitle in setOf(
      "the path is wired end to end",
      "what i could verify here",
      "files changed",
      "local validation",
      "validation",
      "reasoning",
      "raw verification",
    )
  ) {
    return true
  }
  return detailBlocks.sumOf(::technicalSignalScore) >= 3
}

private fun technicalSectionDisplayTitle(titleBody: String): String {
  return when (titleBody.trim().lowercase()) {
    "the path is wired end to end" -> "Implementation details"
    "what i could verify here" -> "Verification details"
    "files changed" -> "Files"
    "local validation", "validation" -> "Validation"
    "reasoning" -> "Reasoning"
    "raw verification" -> "Raw verification"
    else -> titleBody.trim().ifBlank { "Technical details" }
  }
}

private fun summarizeTechnicalSection(detailBlocks: List<MarkdownBlock>): String {
  val bulletItems = detailBlocks
    .filterIsInstance<MarkdownBlock.BulletList>()
    .sumOf { it.items.size }
  if (bulletItems > 0) {
    val noun = if (bulletItems == 1) "item" else "items"
    return "$bulletItems $noun hidden"
  }
  return "${detailBlocks.size} details hidden"
}

private fun technicalSignalScore(block: MarkdownBlock): Int {
  return when (block) {
    is MarkdownBlock.Paragraph -> technicalSignalScore(block.text)
    is MarkdownBlock.Heading -> technicalSignalScore(block.text)
    is MarkdownBlock.Quote -> technicalSignalScore(block.text)
    is MarkdownBlock.CodeFence -> technicalSignalScore(block.code) + if (block.language.lowercase() in setOf("json", "bash", "shell")) 1 else 0
    is MarkdownBlock.BulletList -> block.items.sumOf(::technicalSignalScore)
  }
}

private fun technicalSignalScore(text: String): Int {
  val raw = text.trim()
  if (raw.isBlank()) return 0
  var score = 0
  val lowercase = raw.lowercase()
  score += Regex("""\[[^\]]+]\(([^)]+)\)""").findAll(raw).count()
  if (containsRepoAbsoluteDisplayPath(raw)) score += 1
  if ("<file>" in raw) score += 1
  if ("<cmd>" in raw) score += 1
  if ("host_verification_request_json:" in lowercase) score += 2
  if ("stdout_path" in lowercase || "stderr_path" in lowercase || "artifact_paths" in lowercase) score += 1
  if ("./gradlew" in raw || "pytest " in raw || "python -m py_compile" in raw) score += 1
  return score
}

private fun parseSingleLineSemanticCallout(trimmedLine: String): MessageRenderBlock.SemanticCallout? {
  val match = Regex("^:::\\s*([a-zA-Z]+)\\s+(.+?)\\s*:::$").matchEntire(trimmedLine) ?: return null
  val kind = SemanticBlockKind.fromMarker(match.groupValues[1]) ?: return null
  val body = match.groupValues[2].trim()
  if (body.isEmpty()) return null
  return MessageRenderBlock.SemanticCallout(kind = kind, text = body)
}

private fun shortenDisplayPaths(raw: String): String {
  if (raw.isBlank() || !containsRepoAbsoluteDisplayPath(raw)) return raw
  val lines = raw.split('\n')
  val out = ArrayList<String>(lines.size)
  var inFence = false
  for (line in lines) {
    val trimmed = line.trim()
    if (trimmed.startsWith("```")) {
      out += line
      inFence = !inFence
      continue
    }
    out += if (inFence) line else shortenRepoAbsolutePathsInLine(line)
  }
  return out.joinToString("\n")
}

private fun shortenRepoAbsolutePathsInLine(line: String): String {
  return RepoAbsoluteDisplayRegex.replace(line) { match ->
    val absolute = match.value
    val prefix = repoAbsoluteDisplayPrefix(absolute).orEmpty()
    val relative = absolute.removePrefix(prefix)
    compactDisplayPath(relative)
  }
}

private fun containsRepoAbsoluteDisplayPath(raw: String): Boolean {
  return RepoAbsoluteDisplayRegex.containsMatchIn(raw)
}

private fun repoAbsoluteDisplayPrefix(path: String): String? {
  val trimmed = path.trim()
  if (!trimmed.startsWith(HomeRepoAbsolutePrefix)) return null
  val remainder = trimmed.removePrefix(HomeRepoAbsolutePrefix)
  val repoNameEnd = remainder.indexOf('/')
  if (repoNameEnd <= 0) return null
  return HomeRepoAbsolutePrefix + remainder.substring(0, repoNameEnd + 1)
}

private fun compactDisplayPath(relativePath: String): String {
  val normalized = relativePath.trim().trim('/')
  if (normalized.isBlank()) return "."
  if (normalized.length <= 72) return normalized
  val parts = normalized.split('/').filter { it.isNotBlank() }
  if (parts.size <= 4) return normalized
  return buildString {
    append(parts[0])
    append('/')
    append(parts[1])
    append("/.../")
    append(parts.last())
  }
}

internal fun isCompactPathOnlyParagraph(text: String): Boolean {
  val trimmed = text.trim()
  if (trimmed.isBlank() || trimmed.contains('\n')) return false
  if (trimmed.contains(' ')) return false
  if ('/' !in trimmed) return false
  if (trimmed.startsWith("/") || trimmed.startsWith("./") || trimmed.startsWith("../")) return true
  val parts = trimmed.split('/').filter { it.isNotBlank() }
  return parts.size >= 2
}

@Composable
fun MarkdownMessage(
  text: String,
  textColor: Color,
  onCopyCodeBlock: (String) -> Unit,
  modifier: Modifier = Modifier,
  onOpenFileReference: ((String) -> Unit)? = null,
) {
  val blocks = remember(text) { parseMessageRenderBlocks(text) }
  val colorScheme = MaterialTheme.colorScheme
  val inlineCodeColor = colorScheme.onSurface
  val codeBlockBg = if (colorScheme.background == Color.Black) Color(0xFF1A1C20) else Color(0xFFECEFF3)
  val codeBlockText = colorScheme.onSurface
  val codePalette = remember(colorScheme, codeBlockText) {
    if (colorScheme.background == Color.Black) {
      CodeHighlightPalette(
        keyword = Color(0xFF7CC7FF),
        string = Color(0xFF8FD18A),
        comment = Color(0xFF8E98A3),
        number = Color(0xFFF1B36B),
        syntax = Color(0xFF7FB7FF),
      )
    } else {
      CodeHighlightPalette(
        keyword = Color(0xFF1D63C7),
        string = Color(0xFF2E7D32),
        comment = Color(0xFF7A7F87),
        number = Color(0xFFB26A00),
        syntax = Color(0xFF4C6FA8),
      )
    }
  }
  val codeTextStyle = ChatBodyTextStyle.copy(
    fontFamily = FontFamily.Monospace,
    fontSize = 14.sp,
    lineHeight = 20.sp,
  )

  Column(
    modifier = modifier.fillMaxWidth(),
    verticalArrangement = Arrangement.spacedBy(8.dp),
  ) {
    blocks.forEach { block ->
      when (block) {
        is MessageRenderBlock.Markdown -> RenderMarkdownBlock(
          block = block.block,
          textColor = textColor,
          inlineCodeColor = inlineCodeColor,
          codeBlockBg = codeBlockBg,
          codeBlockText = codeBlockText,
          codePalette = codePalette,
          codeTextStyle = codeTextStyle,
          onCopyCodeBlock = onCopyCodeBlock,
          onOpenFileReference = onOpenFileReference,
        )

        is MessageRenderBlock.SemanticCallout -> SemanticCalloutBlock(
          kind = block.kind,
          text = block.text,
          textColor = textColor,
          inlineCodeColor = inlineCodeColor,
          codeBlockBg = codeBlockBg,
          codeBlockText = codeBlockText,
          codePalette = codePalette,
          codeTextStyle = codeTextStyle,
          onCopyCodeBlock = onCopyCodeBlock,
          onOpenFileReference = onOpenFileReference,
        )

        is MessageRenderBlock.TechnicalDetails -> TechnicalDetailsBlock(
          block = block,
          textColor = textColor,
          inlineCodeColor = inlineCodeColor,
          codeBlockBg = codeBlockBg,
          codeBlockText = codeBlockText,
          codePalette = codePalette,
          codeTextStyle = codeTextStyle,
          onCopyCodeBlock = onCopyCodeBlock,
          onOpenFileReference = onOpenFileReference,
        )
      }
    }
  }
}

@Composable
private fun RenderMarkdownBlock(
  block: MarkdownBlock,
  textColor: Color,
  inlineCodeColor: Color,
  codeBlockBg: Color,
  codeBlockText: Color,
  codePalette: CodeHighlightPalette,
  codeTextStyle: androidx.compose.ui.text.TextStyle,
  onCopyCodeBlock: (String) -> Unit,
  onOpenFileReference: ((String) -> Unit)?,
) {
  val colorScheme = MaterialTheme.colorScheme
  when (block) {
    is MarkdownBlock.Paragraph -> {
      val compactFileReference = compactParagraphFileReference(block.text)
      if (isCompactPathOnlyParagraph(block.text)) {
        Surface(
          shape = RoundedCornerShape(12.dp),
          color = codeBlockBg,
          tonalElevation = 0.dp,
          modifier = Modifier
            .fillMaxWidth()
            .then(
              if (compactFileReference != null && onOpenFileReference != null) {
                Modifier.clickable { onOpenFileReference(compactFileReference) }
              } else {
                Modifier
              },
            )
            .then(
              if (compactFileReference != null && onOpenFileReference != null) {
                Modifier.border(
                  width = 1.dp,
                  color = MaterialTheme.colorScheme.primary.copy(alpha = 0.24f),
                  shape = RoundedCornerShape(12.dp),
                )
              } else {
                Modifier
              },
            ),
        ) {
          Text(
            text = block.text.trim(),
            color = codeBlockText,
            style = codeTextStyle,
            modifier = Modifier.padding(horizontal = 12.dp, vertical = 10.dp),
          )
        }
      } else {
        InlineMarkdownText(
          rawText = block.text,
          textColor = textColor,
          inlineCodeColor = inlineCodeColor,
          style = ChatBodyTextStyle,
          onOpenFileReference = onOpenFileReference,
        )
      }
    }

    is MarkdownBlock.Heading -> {
      val style = when (block.level) {
        1 -> MaterialTheme.typography.headlineSmall
        2 -> MaterialTheme.typography.titleLarge
        else -> MaterialTheme.typography.titleMedium
      }
      InlineMarkdownText(
        rawText = block.text,
        textColor = textColor,
        inlineCodeColor = inlineCodeColor,
        style = style,
        onOpenFileReference = onOpenFileReference,
      )
    }

    is MarkdownBlock.BulletList -> Column(verticalArrangement = Arrangement.spacedBy(4.dp)) {
      block.items.forEach { item ->
        Row(horizontalArrangement = Arrangement.spacedBy(8.dp)) {
          Text(text = "-", color = textColor, style = ChatBodyTextStyle)
          InlineMarkdownText(
            rawText = item,
            textColor = textColor,
            inlineCodeColor = inlineCodeColor,
            style = ChatBodyTextStyle,
            modifier = Modifier.weight(1f),
            onOpenFileReference = onOpenFileReference,
          )
        }
      }
    }

    is MarkdownBlock.Quote -> Row(horizontalArrangement = Arrangement.spacedBy(8.dp)) {
      Text(text = ">", color = colorScheme.onSurfaceVariant, style = ChatBodyTextStyle)
      InlineMarkdownText(
        rawText = block.text,
        textColor = textColor,
        inlineCodeColor = inlineCodeColor,
        style = ChatBodyTextStyle,
        modifier = Modifier.weight(1f),
        onOpenFileReference = onOpenFileReference,
      )
    }

    is MarkdownBlock.CodeFence -> Box(
      modifier = Modifier
        .fillMaxWidth()
        .background(color = codeBlockBg, shape = RoundedCornerShape(14.dp))
        .padding(horizontal = 12.dp, vertical = 8.dp),
    ) {
      Column(
        modifier = Modifier.fillMaxWidth(),
        verticalArrangement = Arrangement.spacedBy(6.dp),
      ) {
        Row(
          modifier = Modifier.fillMaxWidth(),
          verticalAlignment = Alignment.CenterVertically,
        ) {
          if (block.language.isNotBlank()) {
            Text(
              text = block.language,
              color = colorScheme.onSurfaceVariant,
              style = ChatLabelTextStyle,
            )
          }
          Spacer(modifier = Modifier.weight(1f))
          TextButton(
            onClick = { onCopyCodeBlock(block.code) },
            contentPadding = androidx.compose.foundation.layout.PaddingValues(horizontal = 6.dp, vertical = 0.dp),
          ) {
            Icon(
              imageVector = Icons.Outlined.ContentCopy,
              contentDescription = "Copy block",
              modifier = Modifier.size(16.dp),
            )
            Text(
              text = "Copy block",
              style = ChatLabelTextStyle,
              modifier = Modifier.padding(start = 6.dp),
            )
          }
        }
        Text(
          text = highlightedCodeAnnotatedText(
            language = block.language,
            code = block.code,
            palette = codePalette,
          ),
          color = codeBlockText,
          style = codeTextStyle,
          modifier = Modifier.horizontalScroll(rememberScrollState()),
        )
      }
    }
  }
}

@Composable
private fun InlineMarkdownText(
  rawText: String,
  textColor: Color,
  inlineCodeColor: Color,
  style: androidx.compose.ui.text.TextStyle,
  modifier: Modifier = Modifier,
  onOpenFileReference: ((String) -> Unit)? = null,
) {
  val inlineModel = remember(rawText, textColor, inlineCodeColor) {
    buildInlineRenderModel(
      text = rawText,
      textColor = textColor,
      inlineCodeColor = inlineCodeColor,
    )
  }

  Column(
    modifier = modifier,
    verticalArrangement = Arrangement.spacedBy(6.dp),
  ) {
    if (onOpenFileReference != null && inlineModel.text.getStringAnnotations(FileReferenceAnnotationTag, 0, inlineModel.text.length).isNotEmpty()) {
      ClickableText(
        text = inlineModel.text,
        style = style.copy(color = textColor),
        onClick = { offset ->
          inlineModel.text.getStringAnnotations(FileReferenceAnnotationTag, offset, offset)
            .firstOrNull()
            ?.item
            ?.let(onOpenFileReference)
        },
      )
    } else {
      Text(
        text = inlineModel.text,
        color = textColor,
        style = style,
      )
    }
    if (inlineModel.fileReferences.isNotEmpty()) {
      FileReferenceStrip(
        fileReferences = inlineModel.fileReferences,
        textColor = textColor,
        onOpenFileReference = onOpenFileReference,
      )
    }
  }
}

@Composable
private fun FileReferenceStrip(
  fileReferences: List<String>,
  textColor: Color,
  onOpenFileReference: ((String) -> Unit)? = null,
) {
  val uniqueRefs = remember(fileReferences) { fileReferences.distinct() }
  Row(
    modifier = Modifier.horizontalScroll(rememberScrollState()),
    horizontalArrangement = Arrangement.spacedBy(6.dp),
  ) {
    uniqueRefs.forEach { path ->
      Text(
        text = fileReferenceLabel(path),
        color = textColor,
        style = ChatLabelTextStyle,
        modifier = Modifier
          .then(
            if (onOpenFileReference != null) {
              Modifier.border(
                width = 1.dp,
                color = MaterialTheme.colorScheme.primary.copy(alpha = 0.20f),
                shape = RoundedCornerShape(999.dp),
              )
            } else {
              Modifier
            },
          )
          .background(
            color = textColor.copy(alpha = 0.08f),
            shape = RoundedCornerShape(999.dp),
          )
          .border(
            width = 1.dp,
            color = textColor.copy(alpha = 0.14f),
            shape = RoundedCornerShape(999.dp),
          )
          .then(
            if (onOpenFileReference != null) {
              Modifier.clickable { onOpenFileReference(path) }
            } else {
              Modifier
            },
          )
          .padding(horizontal = 10.dp, vertical = 5.dp),
      )
    }
  }
}

@Composable
private fun SemanticCalloutBlock(
  kind: SemanticBlockKind,
  text: String,
  textColor: Color,
  inlineCodeColor: Color,
  codeBlockBg: Color,
  codeBlockText: Color,
  codePalette: CodeHighlightPalette,
  codeTextStyle: androidx.compose.ui.text.TextStyle,
  onCopyCodeBlock: (String) -> Unit,
  onOpenFileReference: ((String) -> Unit)?,
) {
  val colorScheme = MaterialTheme.colorScheme
  val (background, accent, labelText) = when (kind) {
    SemanticBlockKind.DECISION -> Triple(
      colorScheme.primaryContainer.copy(alpha = 0.45f),
      colorScheme.primary,
      kind.label,
    )
    SemanticBlockKind.NEXT -> Triple(
      colorScheme.secondaryContainer.copy(alpha = 0.50f),
      colorScheme.secondary,
      kind.label,
    )
    SemanticBlockKind.WARNING -> Triple(
      colorScheme.tertiaryContainer.copy(alpha = 0.55f),
      colorScheme.tertiary,
      kind.label,
    )
  }
  val innerBlocks = remember(text) { parseMarkdownBlocks(text) }

  Row(
    modifier = Modifier
      .fillMaxWidth()
      .background(background, RoundedCornerShape(16.dp))
      .border(width = 1.dp, color = accent.copy(alpha = 0.55f), shape = RoundedCornerShape(16.dp))
      .padding(12.dp),
    horizontalArrangement = Arrangement.spacedBy(10.dp),
  ) {
    Box(
      modifier = Modifier
        .size(width = 4.dp, height = 44.dp)
        .background(accent, RoundedCornerShape(999.dp)),
    )
    Column(
      modifier = Modifier.weight(1f),
      verticalArrangement = Arrangement.spacedBy(8.dp),
    ) {
      Text(
        text = labelText,
        color = accent,
        style = ChatLabelTextStyle,
      )
      innerBlocks.forEach { block ->
        RenderMarkdownBlock(
          block = block,
          textColor = textColor,
          inlineCodeColor = inlineCodeColor,
          codeBlockBg = codeBlockBg,
          codeBlockText = codeBlockText,
          codePalette = codePalette,
          codeTextStyle = codeTextStyle,
          onCopyCodeBlock = onCopyCodeBlock,
          onOpenFileReference = onOpenFileReference,
        )
      }
    }
  }
}

@Composable
private fun TechnicalDetailsBlock(
  block: MessageRenderBlock.TechnicalDetails,
  textColor: Color,
  inlineCodeColor: Color,
  codeBlockBg: Color,
  codeBlockText: Color,
  codePalette: CodeHighlightPalette,
  codeTextStyle: androidx.compose.ui.text.TextStyle,
  onCopyCodeBlock: (String) -> Unit,
  onOpenFileReference: ((String) -> Unit)?,
) {
  val expandedState = rememberSaveable(block.title, block.summary) {
    androidx.compose.runtime.mutableStateOf(false)
  }
  val expanded = expandedState.value
  val colorScheme = MaterialTheme.colorScheme
  Surface(
    shape = RoundedCornerShape(16.dp),
    color = colorScheme.surfaceVariant.copy(alpha = 0.36f),
    tonalElevation = 0.dp,
    modifier = Modifier.fillMaxWidth(),
  ) {
    Column(
      modifier = Modifier.padding(horizontal = 12.dp, vertical = 10.dp),
      verticalArrangement = Arrangement.spacedBy(8.dp),
    ) {
      Row(
        modifier = Modifier.fillMaxWidth(),
        verticalAlignment = Alignment.CenterVertically,
      ) {
        Column(modifier = Modifier.weight(1f)) {
          Text(
            text = block.title,
            color = textColor,
            style = ChatLabelTextStyle,
          )
          Text(
            text = block.summary,
            color = colorScheme.onSurfaceVariant,
            style = ChatLabelTextStyle,
          )
        }
        TextButton(onClick = { expandedState.value = !expanded }) {
          Text(
            text = if (expanded) "Hide" else "Show",
            style = ChatLabelTextStyle,
          )
        }
      }
      if (expanded) {
        Column(verticalArrangement = Arrangement.spacedBy(8.dp)) {
          block.blocks.forEach { inner ->
            RenderMarkdownBlock(
              block = inner,
              textColor = textColor,
              inlineCodeColor = inlineCodeColor,
              codeBlockBg = codeBlockBg,
              codeBlockText = codeBlockText,
            codePalette = codePalette,
            codeTextStyle = codeTextStyle,
            onCopyCodeBlock = onCopyCodeBlock,
            onOpenFileReference = onOpenFileReference,
          )
        }
      }
    }
    }
  }
}

internal fun highlightedCodeAnnotatedText(
  language: String,
  code: String,
  palette: CodeHighlightPalette,
): AnnotatedString {
  return when (language.trim().lowercase()) {
    "python", "py" -> highlightPythonCode(code = code, palette = palette)
    "markdown", "md" -> highlightMarkdownCode(code = code, palette = palette)
    else -> AnnotatedString(code)
  }
}

private fun highlightPythonCode(
  code: String,
  palette: CodeHighlightPalette,
): AnnotatedString = buildAnnotatedString {
  val keywords = setOf(
    "and", "as", "assert", "async", "await", "break", "class", "continue", "def",
    "del", "elif", "else", "except", "False", "finally", "for", "from", "global",
    "if", "import", "in", "is", "lambda", "None", "nonlocal", "not", "or", "pass",
    "raise", "return", "True", "try", "while", "with", "yield",
  )
  var idx = 0
  while (idx < code.length) {
    if ((idx + 2) < code.length && (code.startsWith("\"\"\"", idx) || code.startsWith("'''", idx))) {
      val delim = code.substring(idx, idx + 3)
      val end = code.indexOf(delim, idx + 3)
      val stop = if (end >= 0) end + 3 else code.length
      withStyle(SpanStyle(color = palette.string)) {
        append(code.substring(idx, stop))
      }
      idx = stop
      continue
    }
    val ch = code[idx]
    if (ch == '#') {
      val end = code.indexOf('\n', idx).let { if (it >= 0) it else code.length }
      withStyle(SpanStyle(color = palette.comment, fontStyle = FontStyle.Italic)) {
        append(code.substring(idx, end))
      }
      idx = end
      continue
    }
    if (ch == '"' || ch == '\'') {
      val quote = ch
      var end = idx + 1
      var escaped = false
      while (end < code.length) {
        val c = code[end]
        if (escaped) {
          escaped = false
        } else if (c == '\\') {
          escaped = true
        } else if (c == quote) {
          end += 1
          break
        }
        end += 1
      }
      withStyle(SpanStyle(color = palette.string)) {
        append(code.substring(idx, end.coerceAtMost(code.length)))
      }
      idx = end.coerceAtMost(code.length)
      continue
    }
    if (ch.isDigit()) {
      var end = idx + 1
      while (end < code.length && (code[end].isDigit() || code[end] == '.' || code[end].lowercaseChar() == 'x')) {
        end += 1
      }
      withStyle(SpanStyle(color = palette.number)) {
        append(code.substring(idx, end))
      }
      idx = end
      continue
    }
    if (ch.isLetter() || ch == '_') {
      var end = idx + 1
      while (end < code.length && (code[end].isLetterOrDigit() || code[end] == '_')) {
        end += 1
      }
      val token = code.substring(idx, end)
      val style = if (token in keywords) SpanStyle(color = palette.keyword, fontWeight = FontWeight.SemiBold) else null
      if (style != null) {
        withStyle(style) { append(token) }
      } else {
        append(token)
      }
      idx = end
      continue
    }
    append(ch)
    idx += 1
  }
}

private fun highlightMarkdownCode(
  code: String,
  palette: CodeHighlightPalette,
): AnnotatedString = buildAnnotatedString {
  val lines = code.split('\n')
  lines.forEachIndexed { index, line ->
    append(highlightMarkdownLine(line = line, palette = palette))
    if (index < lines.lastIndex) {
      append('\n')
    }
  }
}

private fun highlightMarkdownLine(
  line: String,
  palette: CodeHighlightPalette,
): AnnotatedString = buildAnnotatedString {
  val trimmed = line.trimStart()
  val indent = line.length - trimmed.length
  if (indent > 0) append(line.substring(0, indent))

  when {
    trimmed.startsWith("```") -> {
      withStyle(SpanStyle(color = palette.syntax, fontWeight = FontWeight.SemiBold)) {
        append(trimmed)
      }
      return@buildAnnotatedString
    }
    trimmed.startsWith("#") -> {
      var level = 0
      while (level < trimmed.length && trimmed[level] == '#') level += 1
      if (level in 1..6 && level < trimmed.length && trimmed[level] == ' ') {
        withStyle(SpanStyle(color = palette.syntax, fontWeight = FontWeight.SemiBold)) {
          append(trimmed.substring(0, level))
        }
        append(' ')
        withStyle(SpanStyle(color = palette.keyword, fontWeight = FontWeight.SemiBold)) {
          append(trimmed.substring(level + 1))
        }
        return@buildAnnotatedString
      }
    }
    trimmed.startsWith("- ") || trimmed.startsWith("* ") || trimmed.startsWith("+ ") -> {
      withStyle(SpanStyle(color = palette.syntax, fontWeight = FontWeight.SemiBold)) {
        append(trimmed.substring(0, 1))
      }
      append(trimmed.substring(1))
      return@buildAnnotatedString
    }
    trimmed.startsWith(">") -> {
      withStyle(SpanStyle(color = palette.syntax, fontWeight = FontWeight.SemiBold)) {
        append(">")
      }
      append(trimmed.substring(1))
      return@buildAnnotatedString
    }
  }

  var idx = 0
  while (idx < trimmed.length) {
    if (trimmed[idx] == '`') {
      val end = trimmed.indexOf('`', idx + 1)
      if (end > idx) {
        withStyle(SpanStyle(color = palette.syntax)) {
          append("`")
        }
        withStyle(SpanStyle(color = palette.string, fontFamily = FontFamily.Monospace)) {
          append(trimmed.substring(idx + 1, end))
        }
        withStyle(SpanStyle(color = palette.syntax)) {
          append("`")
        }
        idx = end + 1
        continue
      }
    }
    append(trimmed[idx])
    idx += 1
  }
}

internal fun markdownAnnotatedText(
  text: String,
  textColor: Color,
  inlineCodeColor: Color,
): AnnotatedString = buildInlineRenderModel(
  text = text,
  textColor = textColor,
  inlineCodeColor = inlineCodeColor,
).text

/** Convert rendered chat markdown into plain speech-oriented text. */
internal fun markdownToSpeechText(raw: String): String {
  val speechParts = parseMessageRenderBlocks(raw).mapNotNull { block ->
    when (block) {
      is MessageRenderBlock.Markdown -> markdownBlockToSpeechText(block.block)
      is MessageRenderBlock.SemanticCallout -> {
        val body = parseMarkdownBlocks(block.text)
          .mapNotNull(::markdownBlockToSpeechText)
          .joinToString("\n")
          .trim()
        when {
          body.isBlank() -> block.kind.label
          else -> "${block.kind.label}. $body"
        }
      }
      is MessageRenderBlock.TechnicalDetails -> {
        val body = block.blocks
          .mapNotNull(::markdownBlockToSpeechText)
          .joinToString("\n")
          .trim()
        when {
          body.isBlank() -> block.title
          else -> "${block.title}. $body"
        }
      }
    }
  }
  return speechParts.joinToString("\n")
    .replace(Regex("[ \\t]+"), " ")
    .replace(Regex("\\n{3,}"), "\n\n")
    .trim()
}

private fun buildInlineRenderModel(
  text: String,
  textColor: Color,
  inlineCodeColor: Color,
): InlineRenderModel {
  val normalizedText = normalizeChatTextForDisplay(text)
  val fileReferences = mutableListOf<String>()
  val annotated = buildAnnotatedString {
    var idx = 0
    while (idx < normalizedText.length) {
      val markdownLinkMatch = matchMarkdownLink(
        text = normalizedText,
        idx = idx,
        textColor = textColor,
      )
      if (markdownLinkMatch != null) {
        markdownLinkMatch.fileReference?.let(fileReferences::add)
        if (markdownLinkMatch.fileReference != null) {
          pushStringAnnotation(FileReferenceAnnotationTag, markdownLinkMatch.fileReference)
        }
        withStyle(markdownLinkMatch.style) {
          append(markdownLinkMatch.displayText)
        }
        if (markdownLinkMatch.fileReference != null) {
          pop()
        }
        idx += markdownLinkMatch.consumedChars
        continue
      }
      val semanticMatch = matchInlineSemantic(
        text = normalizedText,
        idx = idx,
        textColor = textColor,
        inlineCodeColor = inlineCodeColor,
      )
      if (semanticMatch != null) {
        semanticMatch.fileReference?.let(fileReferences::add)
        if (semanticMatch.fileReference != null) {
          pushStringAnnotation(FileReferenceAnnotationTag, semanticMatch.fileReference)
        }
        withStyle(semanticMatch.style) {
          append(semanticMatch.displayText)
        }
        if (semanticMatch.fileReference != null) {
          pop()
        }
        idx += semanticMatch.consumedChars
        continue
      }
      val literalSemanticText = matchLiteralInlineSemanticText(normalizedText, idx)
      if (literalSemanticText != null) {
        append(literalSemanticText)
        idx += literalSemanticText.length
        continue
      }
      if (normalizedText.startsWith("**", idx)) {
        val end = normalizedText.indexOf("**", idx + 2)
        if (end > idx + 2) {
          withStyle(SpanStyle(fontWeight = FontWeight.SemiBold, color = textColor)) {
            append(normalizedText.substring(idx + 2, end))
          }
          idx = end + 2
          continue
        }
      }
      if (normalizedText[idx] == '`') {
        val end = normalizedText.indexOf('`', idx + 1)
        if (end > idx + 1) {
          withStyle(SpanStyle(fontFamily = FontFamily.Monospace, color = inlineCodeColor)) {
            append(normalizedText.substring(idx + 1, end))
          }
          idx = end + 1
          continue
        }
      }
      if (normalizedText[idx] == '*') {
        val end = normalizedText.indexOf('*', idx + 1)
        if (end > idx + 1) {
          withStyle(SpanStyle(fontStyle = FontStyle.Italic, color = textColor)) {
            append(normalizedText.substring(idx + 1, end))
          }
          idx = end + 1
          continue
        }
      }
      append(normalizedText[idx])
      idx += 1
    }
  }
  return InlineRenderModel(
    text = annotated,
    fileReferences = fileReferences,
  )
}

private fun matchMarkdownLink(
  text: String,
  idx: Int,
  textColor: Color,
): InlineSemanticMatch? {
  if (idx >= text.length || text[idx] != '[') return null
  val labelEnd = text.indexOf(']', idx + 1)
  if (labelEnd <= idx + 1 || labelEnd + 1 >= text.length || text[labelEnd + 1] != '(') return null
  val targetEnd = text.indexOf(')', labelEnd + 2)
  if (targetEnd <= labelEnd + 2) return null
  val label = text.substring(idx + 1, labelEnd).trim()
  val target = text.substring(labelEnd + 2, targetEnd).trim()
  if (label.isEmpty() || target.isEmpty()) return null
  return InlineSemanticMatch(
    displayText = label,
    style = SpanStyle(
      color = textColor,
      fontWeight = FontWeight.Medium,
      textDecoration = TextDecoration.Underline,
    ),
    consumedChars = (targetEnd + 1) - idx,
    fileReference = markdownLinkFileReference(target),
  )
}

private fun markdownLinkFileReference(target: String): String? {
  val cleaned = target
    .trim()
    .removePrefix("<")
    .removeSuffix(">")
    .substringBefore('#')
    .substringBefore('?')
    .trim()
  if (cleaned.isEmpty()) return null
  if (repoAbsoluteDisplayPrefix(cleaned) != null) return normalizeRepoFileReference(cleaned)
  if (cleaned.startsWith("/") || cleaned.startsWith("./") || cleaned.startsWith("../")) return cleaned
  val repoRelative = Regex("""^[A-Za-z0-9._-]+(?:/[A-Za-z0-9._-]+)+$""")
  return cleaned.takeIf { repoRelative.matches(it) }
}

internal fun normalizeRepoFileReference(path: String): String {
  val trimmed = path.trim()
  if (trimmed.startsWith(CurrentRepoAbsolutePrefix)) return trimmed
  val prefix = repoAbsoluteDisplayPrefix(trimmed) ?: return trimmed
  val relativePath = trimmed.removePrefix(prefix)
  if (relativePath.isBlank()) return trimmed
  val candidate = File(CurrentRepoRoot, relativePath)
  return if (candidate.exists()) {
    candidate.absolutePath
  } else {
    trimmed
  }
}

internal fun normalizeRepoFsReference(path: String): String {
  val trimmed = path.trim()
  if (trimmed.isBlank()) return ""
  if (trimmed.startsWith(CurrentRepoAbsolutePrefix)) {
    return trimmed.removePrefix(CurrentRepoAbsolutePrefix)
  }
  return normalizeRepoFileReference(trimmed)
}

private fun markdownBlockToSpeechText(block: MarkdownBlock): String? {
  return when (block) {
    is MarkdownBlock.Paragraph -> inlineMarkdownToSpeechText(block.text)
    is MarkdownBlock.Heading -> inlineMarkdownToSpeechText(block.text)
    is MarkdownBlock.BulletList -> block.items
      .map(::inlineMarkdownToSpeechText)
      .filter { it.isNotBlank() }
      .joinToString("\n")
      .ifBlank { null }
    is MarkdownBlock.Quote -> inlineMarkdownToSpeechText(block.text)
      .takeIf { it.isNotBlank() }
      ?.let { "Quote. $it" }
    is MarkdownBlock.CodeFence -> when {
      block.language.isBlank() -> "Code block."
      else -> "${block.language.uppercase()} code block."
    }
  }
}

private fun inlineMarkdownToSpeechText(text: String): String {
  if (text.isBlank()) return ""
  return text
    .replace(Regex("<file>(.*?)</file>")) { fileReferenceLabel(it.groupValues[1]) }
    .replace(Regex("<cmd>(.*?)</cmd>")) { "command ${it.groupValues[1].trim()}" }
    .replace(Regex("<term>(.*?)</term>")) { it.groupValues[1].trim() }
    .replace(Regex("`([^`]+)`")) { it.groupValues[1] }
    .replace(Regex("\\*\\*([^*]+)\\*\\*")) { it.groupValues[1] }
    .replace(Regex("\\*([^*]+)\\*")) { it.groupValues[1] }
    .replace(Regex("\\[(.+?)\\]\\((.+?)\\)")) { it.groupValues[1] }
    .replace(Regex("[ \\t]+"), " ")
    .trim()
}

private fun matchInlineSemantic(
  text: String,
  idx: Int,
  textColor: Color,
  inlineCodeColor: Color,
): InlineSemanticMatch? {
  val tags = listOf("file", "cmd", "term")
  val tag = tags.firstOrNull { text.startsWith("<$it>", idx) } ?: return null
  val closeTag = "</$tag>"
  val contentStart = idx + tag.length + 2
  val contentEnd = text.indexOf(closeTag, contentStart)
  if (contentEnd <= contentStart) return null
  val content = text.substring(contentStart, contentEnd)
  if (tags.any { nested -> content.contains("<$nested>") || content.contains("</$nested>") }) {
    return null
  }
  val (displayText, style) = when (tag) {
    "file" -> {
      fileReferenceLabel(content) to SpanStyle(
        color = textColor,
        background = textColor.copy(alpha = 0.12f),
        fontFamily = FontFamily.Monospace,
        fontWeight = FontWeight.SemiBold,
      )
    }
    "cmd" -> {
      "CMD $content" to SpanStyle(
        color = inlineCodeColor,
        background = inlineCodeColor.copy(alpha = 0.16f),
        fontFamily = FontFamily.Monospace,
        fontWeight = FontWeight.Bold,
      )
    }
    else -> {
      content to SpanStyle(
        color = textColor,
        fontWeight = FontWeight.SemiBold,
        textDecoration = TextDecoration.Underline,
      )
    }
  }
  return InlineSemanticMatch(
    displayText = displayText,
    style = style,
    consumedChars = (contentEnd + closeTag.length) - idx,
    fileReference = if (tag == "file") normalizeRepoFileReference(content) else null,
  )
}

private fun matchLiteralInlineSemanticText(
  text: String,
  idx: Int,
): String? {
  val tags = listOf("file", "cmd", "term")
  val tag = tags.firstOrNull { text.startsWith("<$it>", idx) } ?: return null
  val closeTag = "</$tag>"
  val contentStart = idx + tag.length + 2
  val contentEnd = text.indexOf(closeTag, contentStart)
  if (contentEnd <= contentStart) return null
  val content = text.substring(contentStart, contentEnd)
  if (!tags.any { nested -> content.contains("<$nested>") || content.contains("</$nested>") }) return null
  return text.substring(idx, contentEnd + closeTag.length)
}

private fun fileReferenceLabel(path: String): String {
  val trimmed = path.trim()
  if (trimmed.isEmpty()) return "file"
  val withoutTrailingSlash = trimmed.trimEnd('/', '\\')
  if (withoutTrailingSlash.isEmpty()) return "file"
  return withoutTrailingSlash.substringAfterLast('/').substringAfterLast('\\')
}

internal fun compactParagraphFileReference(text: String): String? {
  val trimmed = text.trim()
  if (!isCompactPathOnlyParagraph(trimmed)) return null
  if (trimmed.contains("/.../") || trimmed.contains("\\...\\")) return null
  return markdownLinkFileReference(trimmed)
}
