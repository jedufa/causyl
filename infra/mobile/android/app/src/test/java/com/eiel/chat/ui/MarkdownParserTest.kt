package com.eiel.chat.ui

import androidx.compose.ui.graphics.Color
import com.eiel.chat.domain.MessageRole
import com.eiel.chat.domain.AgentChatState
import com.eiel.chat.domain.AgentTab
import com.eiel.chat.domain.StreamPhase
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Test

class MarkdownParserTest {
  @Test
  fun parsesSemanticBlocksBeforeMarkdown() {
    val blocks = parseMessageRenderBlocks(
      """
      :::decision
      Choose the safer path.
      :::

      ## Notes
      """.trimIndent(),
    )

    assertEquals(2, blocks.size)
    val callout = blocks[0] as MessageRenderBlock.SemanticCallout
    assertEquals(SemanticBlockKind.DECISION, callout.kind)
    assertEquals("Choose the safer path.", callout.text)
    assertEquals(
      MessageRenderBlock.Markdown(MarkdownBlock.Heading(level = 2, text = "Notes")),
      blocks[1],
    )
  }

  @Test
  fun parsesSingleLineSemanticBlock() {
    val blocks = parseMessageRenderBlocks(":::decision Continue with the text-first restore path. :::")

    assertEquals(1, blocks.size)
    val callout = blocks.single() as MessageRenderBlock.SemanticCallout
    assertEquals(SemanticBlockKind.DECISION, callout.kind)
    assertEquals("Continue with the text-first restore path.", callout.text)
  }

  @Test
  fun ignoresSemanticBlockMarkersInsideCodeFence() {
    val blocks = parseMessageRenderBlocks(
      """
      ```markdown
      :::warning
      leave literal
      :::
      ```
      """.trimIndent(),
    )

    assertEquals(1, blocks.size)
    val markdown = blocks.single() as MessageRenderBlock.Markdown
    assertEquals(
      MarkdownBlock.CodeFence(
        language = "markdown",
        code = ":::warning\nleave literal\n:::",
      ),
      markdown.block,
    )
  }

  @Test
  fun malformedSemanticBlockDegradesToPlainMarkdown() {
    val blocks = parseMessageRenderBlocks(
      """
      :::next
      Finish the test pass.
      """.trimIndent(),
    )

    assertEquals(1, blocks.size)
    val markdown = blocks.single() as MessageRenderBlock.Markdown
    assertEquals(
      MarkdownBlock.Paragraph(":::next\nFinish the test pass."),
      markdown.block,
    )
  }

  @Test
  fun parsesHeadingBulletQuoteAndCodeFence() {
    val blocks = parseMarkdownBlocks(
      """
      # Title
      
      - first
      - second
      
      > quoted line
      
      ```kotlin
      val x = 1
      ```
      """.trimIndent(),
    )

    assertEquals(4, blocks.size)
    assertEquals(MarkdownBlock.Heading(level = 1, text = "Title"), blocks[0])
    assertEquals(MarkdownBlock.BulletList(items = listOf("first", "second")), blocks[1])
    assertEquals(MarkdownBlock.Quote(text = "quoted line"), blocks[2])
    assertEquals(MarkdownBlock.CodeFence(language = "kotlin", code = "val x = 1"), blocks[3])
  }

  @Test
  fun treatsUnclosedFenceAsCodeBlockUntilEnd() {
    val blocks = parseMarkdownBlocks(
      """
      Before
      
      ```python
      print("hi")
      print("bye")
      """.trimIndent(),
    )

    assertEquals(2, blocks.size)
    assertEquals(MarkdownBlock.Paragraph("Before"), blocks[0])
    assertEquals(
      MarkdownBlock.CodeFence(language = "python", code = "print(\"hi\")\nprint(\"bye\")"),
      blocks[1],
    )
  }

  @Test
  fun keepsOrderedListItems() {
    val blocks = parseMarkdownBlocks(
      """
      1. alpha
      2. beta
      """.trimIndent(),
    )

    assertEquals(1, blocks.size)
    assertTrue(blocks[0] is MarkdownBlock.BulletList)
    val list = blocks[0] as MarkdownBlock.BulletList
    assertEquals(listOf("alpha", "beta"), list.items)
  }

  @Test
  fun treatsHashPrefixedNonHeadingLineAsParagraph() {
    val blocks = parseMarkdownBlocks("#123 incident title")

    assertEquals(1, blocks.size)
    assertEquals(MarkdownBlock.Paragraph("#123 incident title"), blocks[0])
  }

  @Test
  fun inlineSemanticTagsStripMarkupAndApplyStyles() {
    val styled = markdownAnnotatedText(
      text = "Open <file>infra/mobile/android/app/src/main/java/com/eiel/chat/ui/MarkdownMessage.kt</file> and run <cmd>./gradlew test</cmd> for the <term>critical path</term>.",
      textColor = Color(0xFF101010),
      inlineCodeColor = Color(0xFF202020),
    )

    assertEquals(
      "Open MarkdownMessage.kt and run CMD ./gradlew test for the critical path.",
      styled.text,
    )
    assertTrue(styled.spanStyles.size >= 3)
  }

  @Test
  fun nestedInlineSemanticTagsFallBackToPlainText() {
    val styled = markdownAnnotatedText(
      text = "Use <cmd><file>bad</file></cmd> literally.",
      textColor = Color(0xFF101010),
      inlineCodeColor = Color(0xFF202020),
    )

    assertEquals("Use <cmd><file>bad</file></cmd> literally.", styled.text)
  }

  @Test
  fun highlightsPythonCodeWithSpanStyles() {
    val styled = highlightedCodeAnnotatedText(
      language = "python",
      code = "def run(x):\n    # note\n    return \"ok\"",
      palette = testCodePalette(),
    )

    assertEquals("def run(x):\n    # note\n    return \"ok\"", styled.text)
    assertFalse(styled.spanStyles.isEmpty())
  }

  @Test
  fun highlightsMarkdownCodeWithSpanStyles() {
    val styled = highlightedCodeAnnotatedText(
      language = "markdown",
      code = "# Title\n- item\nUse `code`",
      palette = testCodePalette(),
    )

    assertEquals("# Title\n- item\nUse `code`", styled.text)
    assertFalse(styled.spanStyles.isEmpty())
  }

  @Test
  fun markdownToSpeechTextStripsControlSyntaxButKeepsMeaning() {
    val speech = markdownToSpeechText(
      """
      :::decision
      Open <file>/tmp/MarkdownMessage.kt</file> and run <cmd>./gradlew testDebugUnitTest</cmd>.
      :::

      - First step
      - `inline code`

      ```kotlin
      val x = 1
      ```
      """.trimIndent(),
    )

    assertEquals(
      "Decision. Open MarkdownMessage.kt and run command ./gradlew testDebugUnitTest.\nFirst step\ninline code\nKOTLIN code block.",
      speech,
    )
  }

  @Test
  fun shortensAbsoluteRepoPathsInDisplayMarkdownOutsideCodeFences() {
    val blocks = parseMessageRenderBlocks(
      """
      Open /home/user/causyl/runtime/live/wrapper_jobs/wj_20260307_094644_991079bb_58ac7242fc_1c38532/stdout_stderr.log now.

      ```bash
      cat /home/user/causyl/runtime/live/wrapper_jobs/wj_20260307_094644_991079bb_58ac7242fc_1c38532/stdout_stderr.log
      ```
      """.trimIndent(),
    )

    val paragraph = (blocks[0] as MessageRenderBlock.Markdown).block as MarkdownBlock.Paragraph
    val codeFence = (blocks[1] as MessageRenderBlock.Markdown).block as MarkdownBlock.CodeFence

    assertTrue(paragraph.text.contains("runtime/live/.../stdout_stderr.log"))
    assertFalse(paragraph.text.contains("/home/user/causyl/"))
    assertTrue(codeFence.code.contains("/home/user/causyl/runtime/live/wrapper_jobs/"))
  }

  @Test
  fun stripsHostVerificationRequestPayloadFromDisplayMarkdown() {
    val blocks = parseMessageRenderBlocks(
      """
      Runner wait is already implemented.

      HOST_VERIFICATION_REQUEST_JSON:
      {"action_id":"android_unit_test","params":{"test_selector":"com.eiel.chat.HttpChatApiTest"}}
      """.trimIndent(),
    )

    assertEquals(1, blocks.size)
    val paragraph = (blocks.single() as MessageRenderBlock.Markdown).block as MarkdownBlock.Paragraph
    assertEquals("Runner wait is already implemented.", paragraph.text)
  }

  @Test
  fun compactsLegacyHostVerificationResultBlobForDisplay() {
    val blocks = parseMessageRenderBlocks(
      """
      Host verification result:
      - request_id: vrf-1
      - action_id: android_unit_test
      - ok: false
      - failure_kind: test_failed
      - exit_code: 1
      - summary: 2 tests completed, 1 failed
      - stdout_path: /tmp/stdout.txt
      - stderr_path: /tmp/stderr.txt
      - artifact_paths: /tmp/report.xml
      """.trimIndent(),
    )

    assertEquals(1, blocks.size)
    val paragraph = (blocks.single() as MessageRenderBlock.Markdown).block as MarkdownBlock.Paragraph
    assertEquals(
      "Host verification result:\nFailed: android_unit_test (test_failed)\n2 tests completed, 1 failed",
      paragraph.text,
    )
  }

  @Test
  fun inlineMarkdownFileLinksRenderAsReadableLabels() {
    val styled = markdownAnnotatedText(
      text = "Open [infra/webui/server_routes_and_main.py](/home/user/causyl/infra/webui/server_routes_and_main.py#L4384) next.",
      textColor = Color(0xFF101010),
      inlineCodeColor = Color(0xFF202020),
    )

    assertEquals("Open infra/webui/server_routes_and_main.py next.", styled.text)
  }

  @Test
  fun inlineFileLinksExposeClickableRepoFileAnnotations() {
    val styled = markdownAnnotatedText(
      text = "Open [server_routes_and_main.py](infra/webui/server_routes_and_main.py) and <file>infra/mobile/android/app/src/main/java/com/eiel/chat/ui/ChatScreen.kt</file>.",
      textColor = Color(0xFF101010),
      inlineCodeColor = Color(0xFF202020),
    )

    val annotations = styled.getStringAnnotations(FileReferenceAnnotationTag, 0, styled.length)
    assertEquals(
      listOf(
        "infra/webui/server_routes_and_main.py",
        "infra/mobile/android/app/src/main/java/com/eiel/chat/ui/ChatScreen.kt",
      ),
      annotations.map { it.item },
    )
  }

  @Test
  fun legacyAbsoluteRepoLinksAreNormalizedToCurrentRepoRoot() {
    val styled = markdownAnnotatedText(
      text =
        "Open [server_routes_and_main.py](/home/user/legacy_repo/infra/webui/server_routes_and_main.py#L4384) " +
          "and <file>/home/user/legacy_repo/infra/mobile/android/app/src/main/java/com/eiel/chat/ui/ChatScreen.kt</file>.",
      textColor = Color(0xFF101010),
      inlineCodeColor = Color(0xFF202020),
    )

    val annotations = styled.getStringAnnotations(FileReferenceAnnotationTag, 0, styled.length)
    assertEquals(
      listOf(
        "/home/user/causyl/infra/webui/server_routes_and_main.py",
        "/home/user/causyl/infra/mobile/android/app/src/main/java/com/eiel/chat/ui/ChatScreen.kt",
      ),
      annotations.map { it.item },
    )
  }

  @Test
  fun compactParagraphFileReferenceRejectsShortenedDisplayPaths() {
    assertEquals(
      "infra/mobile/android/app/src/main/java/com/eiel/chat/ui/MarkdownMessage.kt",
      compactParagraphFileReference("infra/mobile/android/app/src/main/java/com/eiel/chat/ui/MarkdownMessage.kt"),
    )
    assertEquals(
      "/home/user/causyl/infra/mobile/android/app/src/main/java/com/eiel/chat/ui/MarkdownMessage.kt",
      compactParagraphFileReference("/home/user/legacy_repo/infra/mobile/android/app/src/main/java/com/eiel/chat/ui/MarkdownMessage.kt"),
    )
    assertEquals(
      null,
      compactParagraphFileReference("runtime/live/.../stdout_stderr.log"),
    )
  }

  @Test
  fun collapsesDenseTechnicalSectionsIntoDetailsBlocks() {
    val blocks = parseMessageRenderBlocks(
      """
      :::decision
      This is already implemented in the current worktree.
      :::

      The path is wired end to end:
      - The server emits structured commentary in [infra/webui/server_routes_and_main.py](/home/user/causyl/infra/webui/server_routes_and_main.py#L4384).
      - Android parses the phase in [HttpChatApi.kt](/home/user/causyl/infra/mobile/android/app/src/main/java/com/eiel/chat/data/HttpChatApi.kt#L114).
      - The chat screen shows the footer in [ChatScreen.kt](/home/user/causyl/infra/mobile/android/app/src/main/java/com/eiel/chat/ui/ChatScreen.kt#L1217).

      What I could verify here:
      - `pytest tests/control_plane/webui/test_mobile_chat_agents.py -k direct_host_verification` passed.
      - Local Android Gradle verification is still blocked in this shell.
      """.trimIndent(),
    )

    assertEquals(3, blocks.size)
    assertTrue(blocks[0] is MessageRenderBlock.SemanticCallout)
    val implementation = blocks[1] as MessageRenderBlock.TechnicalDetails
    val verification = blocks[2] as MessageRenderBlock.TechnicalDetails
    assertEquals("Implementation details", implementation.title)
    assertEquals("3 items hidden", implementation.summary)
    assertEquals("Verification details", verification.title)
    assertEquals("2 items hidden", verification.summary)
  }

  @Test
  fun detectsSingleLinePathParagraphForCompactTechnicalRendering() {
    assertTrue(isCompactPathOnlyParagraph("runtime/live/.../stdout_stderr.log"))
    assertTrue(isCompactPathOnlyParagraph("./infra/mobile/android/app/build.gradle.kts"))
    assertFalse(isCompactPathOnlyParagraph("Open runtime/live/.../stdout_stderr.log now."))
    assertFalse(isCompactPathOnlyParagraph("just_text_no_path"))
  }

  @Test
  fun bubblePrimaryLongPressActionUsesSpeechForNonUserMessages() {
    assertEquals(
      BubblePrimaryLongPressAction.COPY,
      bubblePrimaryLongPressAction(
        role = MessageRole.USER,
        messageId = "m1",
        activeSpeechMessageId = null,
      ),
    )
    assertEquals(
      BubblePrimaryLongPressAction.SPEAK,
      bubblePrimaryLongPressAction(
        role = MessageRole.ASSISTANT,
        messageId = "m2",
        activeSpeechMessageId = null,
      ),
    )
    assertEquals(
      BubblePrimaryLongPressAction.STOP_SPEAKING,
      bubblePrimaryLongPressAction(
        role = MessageRole.WORKFLOW,
        messageId = "m3",
        activeSpeechMessageId = "m3",
      ),
    )
    assertEquals(BubbleCopyLongPressMs, bubblePrimaryLongPressTimeoutMs(MessageRole.USER))
    assertEquals(BubbleSpeechLongPressMs, bubblePrimaryLongPressTimeoutMs(MessageRole.ASSISTANT))
    assertEquals(
      12f,
      bubblePrimaryLongPressCancelDistancePx(
        role = MessageRole.USER,
        touchSlopPx = 12f,
      ),
      0.001f,
    )
    assertEquals(
      30f,
      bubblePrimaryLongPressCancelDistancePx(
        role = MessageRole.ASSISTANT,
        touchSlopPx = 12f,
      ),
      0.001f,
    )
    assertEquals(
      30f,
      bubblePrimaryLongPressCancelDistancePx(
        role = MessageRole.WORKFLOW,
        touchSlopPx = 12f,
      ),
      0.001f,
    )
  }

  @Test
  fun composerPrimaryActionKeepsQueueingAvailableWhileBusy() {
    assertEquals(
      ComposerPrimaryAction.QUEUE,
      composerPrimaryAction(hasDraft = true, isBusy = true),
    )
    assertEquals(
      ComposerPrimaryAction.SEND,
      composerPrimaryAction(hasDraft = true, isBusy = false),
    )
    assertEquals(
      ComposerPrimaryAction.STOP,
      composerPrimaryAction(hasDraft = false, isBusy = true),
    )
    assertEquals(
      ComposerPrimaryAction.NONE,
      composerPrimaryAction(hasDraft = false, isBusy = false),
    )
  }

  @Test
  fun busyStatusLabelAndRunnerWaitTextDifferentiateExternalVerification() {
    assertEquals(
      "Waiting on runner",
      busyStatusLabel(
        AgentChatState(
          tab = AgentTab.ALL,
          isStreaming = true,
          streamPhase = StreamPhase.HOST_VERIFICATION_WAIT,
        ),
      ),
    )
    assertEquals(
      "Thinking",
      busyStatusLabel(
        AgentChatState(
          tab = AgentTab.ALL,
          isStreaming = true,
        ),
      ),
    )
    assertEquals("Waiting for runner..  13:55", runnerWaitStatusText(remainingMs = 835_000L, pulseMs = 65_000L))
  }

  @Test
  fun extractsHostVerificationTimeoutFromAssistantMarker() {
    val state = AgentChatState(
      tab = AgentTab.ALL,
      messages = listOf(
        com.eiel.chat.domain.ChatMessage(
          threadId = AgentTab.ALL.threadId,
          role = MessageRole.ASSISTANT,
          content = """
            Requested host verification.
            HOST_VERIFICATION_REQUEST_JSON:
            {"action_id":"android_unit_test","timeout_s":1800}
          """.trimIndent(),
          status = com.eiel.chat.domain.MessageStatus.STREAMING,
          createdAtMs = 1L,
          updatedAtMs = 1L,
        ),
      ),
    )

    assertEquals(1_800_000L, hostVerificationTimeoutMs(state))
  }

  private fun testCodePalette(): CodeHighlightPalette = CodeHighlightPalette(
    keyword = Color(0xFF0000FF),
    string = Color(0xFF008000),
    comment = Color(0xFF808080),
    number = Color(0xFFFF8800),
    syntax = Color(0xFFAA00FF),
  )
}
