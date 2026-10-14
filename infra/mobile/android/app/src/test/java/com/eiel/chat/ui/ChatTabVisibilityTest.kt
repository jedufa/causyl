package com.eiel.chat.ui

import com.eiel.chat.domain.AgentTab
import com.eiel.chat.domain.ChatUiState
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Test

class ChatTabVisibilityTest {
  @Test
  fun visibleTabsKeepFixedChatLanesAndHidePlaneShortcut() {
    val state = ChatUiState()

    assertEquals(AgentTab.ALL, AgentTab.default)
    assertEquals(
      listOf(AgentTab.ALL, AgentTab.ALL_2, AgentTab.ALL_3, AgentTab.ORCHESTRATOR, AgentTab.BUILDER, AgentTab.VERIFIER),
      state.tabs,
    )
    assertFalse(state.tabs.contains(AgentTab.PLANE))
  }

  @Test
  fun hiddenPlaneKeyResolvesBackToVisibleDefault() {
    assertEquals(AgentTab.ORCHESTRATOR, AgentTab.visibleOrDefault("o"))
    assertEquals(AgentTab.BUILDER, AgentTab.visibleOrDefault("b"))
    assertEquals(AgentTab.VERIFIER, AgentTab.visibleOrDefault("v"))
    assertEquals(AgentTab.ALL, AgentTab.visibleOrDefault("p"))
    assertTrue(AgentTab.visibleOrDefault("a2").visibleInChatTabs)
  }
}
