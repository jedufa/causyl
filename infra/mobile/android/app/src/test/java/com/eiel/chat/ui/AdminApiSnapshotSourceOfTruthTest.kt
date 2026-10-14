package com.eiel.chat.ui

import org.junit.Assert.assertTrue
import org.junit.Test
import java.io.File

class AdminApiSnapshotSourceOfTruthTest {
  @Test
  fun adminApiDeclaresCanonicalSnapshotRouteAndProjectionFields() {
    val possibleFiles = listOf(
      "infra/mobile/android/app/src/main/java/com/eiel/chat/data/AdminApi.kt",
      "app/src/main/java/com/eiel/chat/data/AdminApi.kt",
      "src/main/java/com/eiel/chat/data/AdminApi.kt",
      "../app/src/main/java/com/eiel/chat/data/AdminApi.kt",
    )
    val sourceFile = possibleFiles.map(::File).firstOrNull { it.exists() } ?: throw AssertionError(
      "AdminApi source path not found",
    )
    val source = sourceFile.readText()

    assertTrue(source.contains("suspend fun labAssistantSnapshot(adminToken: String): OperatorSnapshot"))
    assertTrue(source.contains("\"/api/admin/lab_assistant/snapshot\""))
    assertTrue(source.contains("parseOperatorSnapshot(obj)"))
    assertTrue(source.contains("parseOperatorSnapshotTasks(obj.optJSONArray(\"active_tasks\"))"))
    assertTrue(source.contains("parseOperatorSnapshotTasks(obj.optJSONArray(\"completed_tasks\"))"))
    assertTrue(source.contains("parseOperatorSnapshotFreshness(obj.optJSONObject(\"freshness\"))"))
    assertTrue(source.contains("parseOperatorSnapshotSummary(obj.optJSONObject(\"operator_summary\"))"))
    assertTrue(source.contains("parseOperatorSnapshotCommandCenter(obj.optJSONObject(\"command_center\"))"))
    assertTrue(source.contains("parseOperatorSnapshotWorkItems(obj.optJSONArray(\"work_items\"))"))
    assertTrue(source.contains("delegated = row.optBoolean(\"delegated\", false)"))
    assertTrue(source.contains("currentRunId = row.optString(\"current_run_id\", \"\")"))
    assertTrue(source.contains("currentRunState = row.optString(\"current_run_state\", \"\")"))
    assertTrue(source.contains("currentRunAttentionState = row.optString(\"current_run_attention_state\", \"\")"))
    assertTrue(source.contains("runCheckpoint = parseOperatorSnapshotRunCheckpoint(row.optJSONObject(\"run_checkpoint\"))"))
    assertTrue(source.contains("private fun parseOperatorSnapshotRunCheckpoint(obj: JSONObject?): OperatorSnapshotRunCheckpoint"))
  }
}
