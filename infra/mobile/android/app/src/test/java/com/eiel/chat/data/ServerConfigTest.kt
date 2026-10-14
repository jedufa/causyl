package com.eiel.chat.data

import org.junit.Assert.assertEquals
import org.junit.Assert.assertTrue
import org.junit.Test

class ServerConfigTest {
  @Test
  fun normalizeBaseUrl_repairs_quote_before_port() {
    assertEquals(
      "http://192.0.2.1:8787",
      ServerConfig.normalizeBaseUrl("http://192.0.2.1\"8787"),
    )
  }

  @Test
  fun normalizeBaseUrl_strips_quotes_and_trailing_slash() {
    assertEquals(
      "http://192.0.2.1:8787",
      ServerConfig.normalizeBaseUrl("  \"http://192.0.2.1:8787/\"  "),
    )
  }

  @Test
  fun normalizeBaseUrl_fallsBack_when_input_is_invalid() {
    val normalized = ServerConfig.normalizeBaseUrl("http://")
    assertTrue(normalized.startsWith("http://"))
  }
}
