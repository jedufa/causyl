package com.eiel.chat.ui

import android.content.ActivityNotFoundException
import android.content.Context
import android.content.Intent
import android.net.Uri
import android.os.Build
import android.os.PowerManager

internal enum class BatteryOptimizationLaunchResult {
  ALREADY_IGNORING,
  OPENED_REQUEST,
  OPENED_SETTINGS,
  NOT_SUPPORTED,
  FAILED,
}

internal data class BatteryOptimizationIntentSpec(
  val action: String,
  val packageUriString: String = "",
  val flags: Int,
)

private const val RequestIgnoreBatteryOptimizationsAction =
  "android.settings.REQUEST_IGNORE_BATTERY_OPTIMIZATIONS"
private const val IgnoreBatteryOptimizationSettingsAction =
  "android.settings.IGNORE_BATTERY_OPTIMIZATION_SETTINGS"

internal fun supportsBatteryOptimizationExemption(
  sdkInt: Int = Build.VERSION.SDK_INT,
): Boolean = sdkInt >= Build.VERSION_CODES.M

internal fun isIgnoringBatteryOptimizations(
  context: Context,
  sdkInt: Int = Build.VERSION.SDK_INT,
): Boolean {
  if (!supportsBatteryOptimizationExemption(sdkInt)) return true
  val powerManager = context.getSystemService(PowerManager::class.java) ?: return false
  return powerManager.isIgnoringBatteryOptimizations(context.packageName)
}

internal fun buildRequestIgnoreBatteryOptimizationsIntentSpec(
  packageName: String,
): BatteryOptimizationIntentSpec {
  return BatteryOptimizationIntentSpec(
    action = RequestIgnoreBatteryOptimizationsAction,
    packageUriString = buildPackageUriString(packageName),
    flags = FlagActivityNewTask,
  )
}

internal fun buildBatteryOptimizationSettingsIntentSpec(): BatteryOptimizationIntentSpec {
  return BatteryOptimizationIntentSpec(
    action = IgnoreBatteryOptimizationSettingsAction,
    flags = FlagActivityNewTask,
  )
}

internal fun buildRequestIgnoreBatteryOptimizationsIntent(packageName: String): Intent {
  val spec = buildRequestIgnoreBatteryOptimizationsIntentSpec(packageName)
  return Intent(spec.action, Uri.parse(spec.packageUriString)).apply {
    addFlags(spec.flags)
  }
}

internal fun buildBatteryOptimizationSettingsIntent(): Intent {
  val spec = buildBatteryOptimizationSettingsIntentSpec()
  return Intent(spec.action).apply {
    addFlags(spec.flags)
  }
}

internal fun launchBatteryOptimizationSettings(context: Context): BatteryOptimizationLaunchResult {
  if (!supportsBatteryOptimizationExemption()) {
    return BatteryOptimizationLaunchResult.NOT_SUPPORTED
  }
  if (isIgnoringBatteryOptimizations(context)) {
    return BatteryOptimizationLaunchResult.ALREADY_IGNORING
  }
  return try {
    context.startActivity(buildRequestIgnoreBatteryOptimizationsIntent(context.packageName))
    BatteryOptimizationLaunchResult.OPENED_REQUEST
  } catch (_: ActivityNotFoundException) {
    try {
      context.startActivity(buildBatteryOptimizationSettingsIntent())
      BatteryOptimizationLaunchResult.OPENED_SETTINGS
    } catch (_: ActivityNotFoundException) {
      BatteryOptimizationLaunchResult.FAILED
    } catch (_: SecurityException) {
      BatteryOptimizationLaunchResult.FAILED
    }
  } catch (_: SecurityException) {
    try {
      context.startActivity(buildBatteryOptimizationSettingsIntent())
      BatteryOptimizationLaunchResult.OPENED_SETTINGS
    } catch (_: ActivityNotFoundException) {
      BatteryOptimizationLaunchResult.FAILED
    } catch (_: SecurityException) {
      BatteryOptimizationLaunchResult.FAILED
    }
  }
}
