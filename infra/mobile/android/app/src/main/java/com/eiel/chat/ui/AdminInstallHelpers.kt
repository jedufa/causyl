package com.eiel.chat.ui

import android.content.ActivityNotFoundException
import android.content.Context
import android.content.Intent
import android.net.Uri
import android.os.Build
import androidx.core.content.FileProvider
import java.io.File

internal enum class ApkInstallerLaunchResult {
  STARTED_INSTALLER,
  AWAITING_INSTALL_PERMISSION,
  MISSING_FILE,
  FAILED,
}

internal data class UnknownAppSourcesSettingsIntentSpec(
  val action: String,
  val packageUriString: String,
  val flags: Int,
)

internal data class ApkInstallerIntentSpec(
  val action: String,
  val flags: Int,
)

private const val ManageUnknownAppSourcesAction = "android.settings.MANAGE_UNKNOWN_APP_SOURCES"
private const val InstallPackageAction = "android.intent.action.INSTALL_PACKAGE"
internal const val FlagGrantReadUriPermission = 0x00000001
internal const val FlagActivityNewTask = 0x10000000

internal fun requiresUnknownAppSourcesPermission(
  canRequestPackageInstalls: Boolean,
  sdkInt: Int = Build.VERSION.SDK_INT,
): Boolean {
  return sdkInt >= Build.VERSION_CODES.O && !canRequestPackageInstalls
}

internal fun canInstallLocalApk(context: Context, sdkInt: Int = Build.VERSION.SDK_INT): Boolean {
  return !requiresUnknownAppSourcesPermission(
    canRequestPackageInstalls = context.packageManager.canRequestPackageInstalls(),
    sdkInt = sdkInt,
  )
}

internal fun buildPackageUriString(packageName: String): String = "package:$packageName"

// Provide framework-free intent metadata so JVM unit tests can verify this logic.
internal fun buildUnknownAppSourcesSettingsIntentSpec(
  packageName: String,
): UnknownAppSourcesSettingsIntentSpec {
  return UnknownAppSourcesSettingsIntentSpec(
    action = ManageUnknownAppSourcesAction,
    packageUriString = buildPackageUriString(packageName),
    flags = FlagActivityNewTask,
  )
}

internal fun buildUnknownAppSourcesSettingsIntent(packageName: String): Intent {
  return buildUnknownAppSourcesSettingsIntent(
    packageUri = Uri.fromParts("package", packageName, null),
    spec = buildUnknownAppSourcesSettingsIntentSpec(packageName),
  )
}

internal fun buildUnknownAppSourcesSettingsIntent(packageName: String, packageUri: Uri): Intent {
  return buildUnknownAppSourcesSettingsIntent(
    packageUri = packageUri,
    spec = buildUnknownAppSourcesSettingsIntentSpec(packageName),
  )
}

// Build the install intent from the same spec the JVM tests assert against.
internal fun buildApkInstallerIntentSpec(): ApkInstallerIntentSpec {
  return ApkInstallerIntentSpec(
    action = InstallPackageAction,
    flags = FlagGrantReadUriPermission or FlagActivityNewTask,
  )
}

internal fun buildApkInstallerIntent(contentUri: Uri): Intent {
  val spec = buildApkInstallerIntentSpec()
  return Intent(spec.action).apply {
    setData(contentUri)
    addFlags(spec.flags)
  }
}

// Keep Android object construction in one place; the rest can stay plain data.
private fun buildUnknownAppSourcesSettingsIntent(
  packageUri: Uri,
  spec: UnknownAppSourcesSettingsIntentSpec,
): Intent {
  return Intent(spec.action, packageUri).apply {
    addFlags(spec.flags)
  }
}

internal fun launchApkInstaller(context: Context, absolutePath: String): ApkInstallerLaunchResult {
  val file = File(absolutePath)
  if (!file.exists()) {
    return ApkInstallerLaunchResult.MISSING_FILE
  }
  if (!canInstallLocalApk(context)) {
    return try {
      context.startActivity(buildUnknownAppSourcesSettingsIntent(context.packageName))
      ApkInstallerLaunchResult.AWAITING_INSTALL_PERMISSION
    } catch (_: ActivityNotFoundException) {
      ApkInstallerLaunchResult.FAILED
    }
  }
  val uri = FileProvider.getUriForFile(
    context,
    "${context.packageName}.fileprovider",
    file,
  )
  return try {
    context.startActivity(buildApkInstallerIntent(uri))
    ApkInstallerLaunchResult.STARTED_INSTALLER
  } catch (_: ActivityNotFoundException) {
    ApkInstallerLaunchResult.FAILED
  }
}
