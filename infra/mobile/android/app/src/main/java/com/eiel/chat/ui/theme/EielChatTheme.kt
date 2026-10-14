package com.eiel.chat.ui.theme

import androidx.compose.foundation.isSystemInDarkTheme
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.darkColorScheme
import androidx.compose.material3.lightColorScheme
import androidx.compose.runtime.Composable

private val LightColors = lightColorScheme(
  primary = androidx.compose.ui.graphics.Color(0xFF1E88E5),
  onPrimary = androidx.compose.ui.graphics.Color.White,
  background = androidx.compose.ui.graphics.Color(0xFFFEFEFE),
  onBackground = androidx.compose.ui.graphics.Color(0xFF111827),
  surface = androidx.compose.ui.graphics.Color(0xFFFEFEFE),
  onSurface = androidx.compose.ui.graphics.Color(0xFF111827),
  surfaceVariant = androidx.compose.ui.graphics.Color(0xFFF3F4F6),
  onSurfaceVariant = androidx.compose.ui.graphics.Color(0xFF6B7280),
  outline = androidx.compose.ui.graphics.Color(0xFFE5E7EB),
  error = androidx.compose.ui.graphics.Color(0xFFB42318),
)

private val DarkColors = darkColorScheme(
  primary = androidx.compose.ui.graphics.Color(0xFF5AA9FF),
  onPrimary = androidx.compose.ui.graphics.Color(0xFF0A2540),
  background = androidx.compose.ui.graphics.Color(0xFF000000),
  onBackground = androidx.compose.ui.graphics.Color(0xFFF3F4F6),
  surface = androidx.compose.ui.graphics.Color(0xFF181A1D),
  onSurface = androidx.compose.ui.graphics.Color(0xFFF3F4F6),
  surfaceVariant = androidx.compose.ui.graphics.Color(0xFF23262B),
  onSurfaceVariant = androidx.compose.ui.graphics.Color(0xFFB3BAC5),
  outline = androidx.compose.ui.graphics.Color(0xFF30343A),
  error = androidx.compose.ui.graphics.Color(0xFFFF8A80),
)

@Composable
fun EielChatTheme(
  darkTheme: Boolean = isSystemInDarkTheme(),
  content: @Composable () -> Unit,
) {
  MaterialTheme(
    colorScheme = if (darkTheme) DarkColors else LightColors,
    content = content,
  )
}
