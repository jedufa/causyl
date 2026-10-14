package com.eiel.chat.ui

import android.os.Build
import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Test

class AdminBatteryOptimizationHelpersTest {
  @Test
  fun batteryOptimizationExemptionIsUnsupportedBeforeM() {
    assertFalse(supportsBatteryOptimizationExemption(Build.VERSION_CODES.LOLLIPOP_MR1))
    assertTrue(supportsBatteryOptimizationExemption(Build.VERSION_CODES.M))
  }

  @Test
  fun requestIgnoreBatteryOptimizationIntentTargetsCurrentPackage() {
    val spec = buildRequestIgnoreBatteryOptimizationsIntentSpec("com.eiel.chat.debug")

    check(
      spec == BatteryOptimizationIntentSpec(
        action = "android.settings.REQUEST_IGNORE_BATTERY_OPTIMIZATIONS",
        packageUriString = "package:com.eiel.chat.debug",
        flags = FlagActivityNewTask,
      )
    )
  }

  @Test
  fun batteryOptimizationSettingsIntentUsesSystemSettingsAction() {
    val spec = buildBatteryOptimizationSettingsIntentSpec()

    check(
      spec == BatteryOptimizationIntentSpec(
        action = "android.settings.IGNORE_BATTERY_OPTIMIZATION_SETTINGS",
        packageUriString = "",
        flags = FlagActivityNewTask,
      )
    )
  }
}
