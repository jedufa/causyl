package com.eiel.chat.data

import com.eiel.chat.domain.MessageRole
import com.eiel.chat.domain.MessageStatus
import com.eiel.chat.ui.MessageRenderBlock
import com.eiel.chat.ui.SemanticBlockKind
import com.eiel.chat.ui.parseMessageRenderBlocks
import org.junit.Assert.assertEquals
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Test

class HttpChatApiTest {
  @Test
  fun parseSessionHistoryPayloadPreservesWorkflowMetadata() {
    val api = HttpChatApi()

    val messages = api.parseSessionHistoryPayload(
      """
      {
        "history": [
          {
            "role": "workflow",
            "content": ":::warning\nCheck <cmd>./gradlew test</cmd>\n:::",
            "status": "failed",
            "label": "WF-01",
            "workflow_id": "wf-123",
            "event_key": "wf-123|warn"
          },
          {
            "role": "assistant",
            "content": "```python\nprint(\"ok\")\n```"
          }
        ]
      }
      """.trimIndent(),
    )

    assertEquals(2, messages.size)
    assertEquals(MessageRole.WORKFLOW, messages[0].role)
    assertEquals(MessageStatus.FAILED, messages[0].status)
    assertEquals("WF-01", messages[0].label)
    assertEquals("wf-123", messages[0].workflowId)
    assertEquals("wf-123|warn", messages[0].eventKey)
    assertEquals(MessageRole.ASSISTANT, messages[1].role)
    assertEquals(MessageStatus.COMPLETE, messages[1].status)
    assertNull(messages[1].label)

    val restoredBlocks = parseMessageRenderBlocks(messages[0].content)
    assertTrue(restoredBlocks.single() is MessageRenderBlock.SemanticCallout)
    assertEquals(
      SemanticBlockKind.WARNING,
      (restoredBlocks.single() as MessageRenderBlock.SemanticCallout).kind,
    )
  }
}
