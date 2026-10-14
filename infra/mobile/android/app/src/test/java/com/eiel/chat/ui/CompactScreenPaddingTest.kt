package com.eiel.chat.ui

import androidx.compose.ui.unit.dp
import org.junit.Assert.assertEquals
import org.junit.Assert.assertTrue
import org.junit.Test

class CompactScreenPaddingTest {
  @Test
  fun addsPeekPaddingEvenWithoutNavigationInset() {
    val bottomPadding = compactScreenBottomPadding(0.dp)

    assertEquals(CompactScreenPadding + CompactPaneBottomPeekPadding, bottomPadding)
    assertTrue(bottomPadding > CompactScreenPadding)
  }

  @Test
  fun includesNavigationInsetInBottomPadding() {
    val navigationInset = 24.dp

    assertEquals(
      CompactScreenPadding + CompactPaneBottomPeekPadding + navigationInset,
      compactScreenBottomPadding(navigationInset),
    )
  }
}
