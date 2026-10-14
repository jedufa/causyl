package com.eiel.chat.ui

import androidx.compose.foundation.background
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.foundation.text.selection.SelectionContainer
import androidx.compose.foundation.verticalScroll
import androidx.compose.material3.Card
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Surface
import androidx.compose.material3.Text
import androidx.compose.material3.TextButton
import androidx.compose.runtime.Composable
import androidx.compose.runtime.remember
import androidx.compose.ui.Modifier
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.text.font.FontFamily
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.unit.dp
import androidx.compose.ui.window.Dialog
import androidx.compose.ui.window.DialogProperties

private val RepoViewerMarkdownExtensions = setOf("md", "markdown")

@Composable
internal fun RepoFileViewerDialog(
  path: String,
  text: String,
  truncated: Boolean,
  loading: Boolean,
  error: String,
  onDismiss: () -> Unit,
  onOpenFileReference: (String) -> Unit,
) {
  Dialog(
    onDismissRequest = onDismiss,
    properties = DialogProperties(usePlatformDefaultWidth = false),
  ) {
    Surface(
      modifier = Modifier
        .fillMaxSize()
        .padding(10.dp),
      shape = RoundedCornerShape(24.dp),
      color = MaterialTheme.colorScheme.surface,
    ) {
      Column(
        modifier = Modifier
          .fillMaxSize()
          .padding(horizontal = 14.dp, vertical = 12.dp),
        verticalArrangement = Arrangement.spacedBy(10.dp),
      ) {
        Row(modifier = Modifier.fillMaxWidth()) {
          Column(modifier = Modifier.weight(1f), verticalArrangement = Arrangement.spacedBy(4.dp)) {
            Text(
              text = "Repo Viewer",
              style = MaterialTheme.typography.titleMedium,
              fontWeight = FontWeight.SemiBold,
            )
            Text(
              text = path.ifBlank { "Opening file..." },
              style = ChatLabelTextStyle,
              color = MaterialTheme.colorScheme.onSurfaceVariant,
            )
          }
          TextButton(onClick = onDismiss) {
            Text("Close")
          }
        }
        RepoFileViewerBody(
          path = path,
          text = text,
          truncated = truncated,
          loading = loading,
          error = error,
          onOpenFileReference = onOpenFileReference,
          emptyMessage = "Open a repo file link from chat to preview it here.",
          modifier = Modifier.weight(1f),
        )
      }
    }
  }
}

@Composable
internal fun RepoFileViewerBody(
  path: String,
  text: String,
  truncated: Boolean,
  loading: Boolean,
  error: String,
  onOpenFileReference: ((String) -> Unit)?,
  emptyMessage: String,
  modifier: Modifier = Modifier,
) {
  val dark = androidx.compose.foundation.isSystemInDarkTheme()
  val background = if (dark) Color(0xFF121416) else Color.White
  val contentColor = if (dark) Color(0xFFF2F2EF) else Color(0xFF1A1A1A)
  val extension = remember(path) { path.substringAfterLast('.', "").lowercase() }

  when {
    loading -> Surface(
      modifier = modifier.fillMaxWidth(),
      shape = RoundedCornerShape(18.dp),
      color = MaterialTheme.colorScheme.surfaceVariant.copy(alpha = 0.42f),
    ) {
      Column(
        modifier = Modifier
          .fillMaxWidth()
          .padding(horizontal = 14.dp, vertical = 16.dp),
        verticalArrangement = Arrangement.spacedBy(6.dp),
      ) {
        Text("Opening file", style = ChatLabelTextStyle, color = MaterialTheme.colorScheme.primary)
        Text(
          path.ifBlank { "Loading preview..." },
          style = ChatBodyTextStyle,
          color = MaterialTheme.colorScheme.onSurface,
        )
      }
    }

    error.isNotBlank() -> Surface(
      modifier = modifier.fillMaxWidth(),
      shape = RoundedCornerShape(18.dp),
      color = MaterialTheme.colorScheme.errorContainer.copy(alpha = 0.58f),
    ) {
      Column(
        modifier = Modifier
          .fillMaxWidth()
          .padding(horizontal = 14.dp, vertical = 16.dp),
        verticalArrangement = Arrangement.spacedBy(6.dp),
      ) {
        Text("Viewer error", style = ChatLabelTextStyle, color = MaterialTheme.colorScheme.error)
        Text(
          error,
          style = ChatBodyTextStyle,
          color = MaterialTheme.colorScheme.onErrorContainer,
        )
      }
    }

    path.isBlank() -> Text(
      text = emptyMessage,
      style = ChatLabelTextStyle,
      color = MaterialTheme.colorScheme.onSurfaceVariant,
      modifier = modifier,
    )

    else -> Card(
      modifier = modifier.fillMaxWidth(),
      shape = RoundedCornerShape(18.dp),
    ) {
      Column(
        modifier = Modifier
          .fillMaxSize()
          .background(background)
          .verticalScroll(rememberScrollState())
          .padding(14.dp),
        verticalArrangement = Arrangement.spacedBy(10.dp),
      ) {
        if (truncated) {
          Text(
            text = "Preview truncated to keep the phone responsive.",
            style = ChatLabelTextStyle,
            color = MaterialTheme.colorScheme.primary,
          )
        }
        SelectionContainer {
          if (extension in RepoViewerMarkdownExtensions) {
            MarkdownMessage(
              text = text.ifBlank { "[empty file]" },
              textColor = contentColor,
              onCopyCodeBlock = {},
              onOpenFileReference = onOpenFileReference,
            )
          } else {
            Text(
              text = text.ifBlank { "[empty file]" },
              style = ChatBodyTextStyle.copy(fontFamily = FontFamily.Monospace),
              color = contentColor,
            )
          }
        }
      }
    }
  }
}
