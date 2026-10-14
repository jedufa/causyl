import java.util.Properties

plugins {
  id("com.android.application")
  id("org.jetbrains.kotlin.android")
}

fun projectPropOrEnv(name: String): String? {
  val p = (project.findProperty(name) as String?)?.trim()?.ifBlank { null }
  if (p != null) return p
  val e = System.getenv(name)?.trim()?.ifBlank { null }
  if (e != null) return e
  return null
}

fun loadRuntimePolicy(): Properties {
  val props = Properties()
  val policyFile = rootProject.file("../../config/runtime_policy.properties")
  if (policyFile.exists()) {
    policyFile.inputStream().use { props.load(it) }
  }
  return props
}

val runtimePolicy = loadRuntimePolicy()

fun runtimePolicyValue(key: String): String? = runtimePolicy.getProperty(key)?.trim()?.ifBlank { null }

val serverBaseUrl =
  (project.findProperty("EIEL_SERVER_BASE_URL") as String?)
    ?.trim()
    ?.ifBlank { null }
    ?: "http://192.0.2.1:8787"

val chatStreamPath =
  (project.findProperty("EIEL_CHAT_STREAM_PATH") as String?)
    ?.trim()
    ?.ifBlank { null }
    ?: "/api/mobile/chat/stream"

val authToken =
  (project.findProperty("EIEL_AUTH_TOKEN") as String?)
    ?.trim()
    ?: ""

val explicitAppVersionCode =
  (project.findProperty("EIEL_APP_VERSION_CODE") as String?)
    ?.trim()
    ?.toIntOrNull()

val explicitAppVersionName =
  (project.findProperty("EIEL_APP_VERSION_NAME") as String?)
    ?.trim()
    ?.ifBlank { null }

val chatTimeoutS =
  (projectPropOrEnv("EIEL_MOBILE_CHAT_TIMEOUT_S")
    ?.toIntOrNull()
    ?: projectPropOrEnv("EIEL_CHAT_TURN_TIMEOUT_S")
    ?.toIntOrNull()
    ?: projectPropOrEnv("EIEL_CHAT_TIMEOUT_S")
      ?.toIntOrNull()
    ?: runtimePolicyValue("mobile.chat_turn_timeout_s")?.toIntOrNull()
    ?: 3600)
    .coerceIn(30, 6 * 60 * 60)

val chatIdleTimeoutS =
  (projectPropOrEnv("EIEL_CHAT_IDLE_TIMEOUT_S")
    ?.toIntOrNull()
    ?: runtimePolicyValue("mobile.chat_idle_timeout_s")?.toIntOrNull()
    ?: 600)
    .coerceIn(30, 6 * 60 * 60)

val workflowTimeoutS =
  (projectPropOrEnv("EIEL_MOBILE_WORKFLOW_TIMEOUT_S")
    ?.toIntOrNull()
    ?: runtimePolicyValue("mobile.workflow_timeout_s")?.toIntOrNull()
    ?: 7000)
    .coerceIn(30, 6 * 60 * 60)

android {
  namespace = "com.eiel.chat"
  compileSdk = 35

  defaultConfig {
    applicationId = "com.eiel.chat"
    minSdk = 26
    targetSdk = 35
    versionCode = explicitAppVersionCode ?: 1
    versionName = explicitAppVersionName ?: "0.1.0"

    testInstrumentationRunner = "androidx.test.runner.AndroidJUnitRunner"
    buildConfigField("String", "SERVER_BASE_URL", "\"${serverBaseUrl.replace("\"", "\\\"")}\"")
    buildConfigField("String", "CHAT_STREAM_PATH", "\"${chatStreamPath.replace("\"", "\\\"")}\"")
    buildConfigField("String", "AUTH_TOKEN", "\"${authToken.replace("\"", "\\\"")}\"")
    buildConfigField("int", "CHAT_TIMEOUT_S", chatTimeoutS.toString())
    buildConfigField("int", "CHAT_IDLE_TIMEOUT_S", chatIdleTimeoutS.toString())
    buildConfigField("int", "WORKFLOW_TIMEOUT_S", workflowTimeoutS.toString())
  }

  val releaseStoreFile = projectPropOrEnv("EIEL_RELEASE_STORE_FILE")
  val releaseStorePassword = projectPropOrEnv("EIEL_RELEASE_STORE_PASSWORD")
  val releaseKeyAlias = projectPropOrEnv("EIEL_RELEASE_KEY_ALIAS")
  val releaseKeyPassword = projectPropOrEnv("EIEL_RELEASE_KEY_PASSWORD")
  val hasReleaseSigning =
    !releaseStoreFile.isNullOrBlank() &&
      !releaseStorePassword.isNullOrBlank() &&
      !releaseKeyAlias.isNullOrBlank() &&
      !releaseKeyPassword.isNullOrBlank()

  signingConfigs {
    if (hasReleaseSigning) {
      create("release") {
        storeFile = file(releaseStoreFile!!)
        storePassword = releaseStorePassword
        keyAlias = releaseKeyAlias
        keyPassword = releaseKeyPassword
      }
    }
  }

  buildTypes {
    release {
      isMinifyEnabled = false
      if (hasReleaseSigning) {
        signingConfig = signingConfigs.getByName("release")
      }
      proguardFiles(
        getDefaultProguardFile("proguard-android-optimize.txt"),
        "proguard-rules.pro",
      )
    }
    debug {
      applicationIdSuffix = ".debug"
      if (explicitAppVersionName == null) {
        versionNameSuffix = "-debug"
      }
    }
  }

  compileOptions {
    sourceCompatibility = JavaVersion.VERSION_17
    targetCompatibility = JavaVersion.VERSION_17
  }
  kotlinOptions {
    jvmTarget = "17"
  }
  buildFeatures {
    compose = true
    buildConfig = true
  }
  composeOptions {
    kotlinCompilerExtensionVersion = "1.5.14"
  }
  packaging {
    resources {
      excludes += "/META-INF/{AL2.0,LGPL2.1}"
    }
  }

  testOptions {
    unitTests {
      isIncludeAndroidResources = true
    }
  }
}

dependencies {
  val composeBom = platform("androidx.compose:compose-bom:2024.09.01")

  implementation(composeBom)
  androidTestImplementation(composeBom)

  implementation("androidx.core:core-ktx:1.13.1")
  implementation("androidx.activity:activity-compose:1.9.2")
  implementation("androidx.lifecycle:lifecycle-runtime-ktx:2.8.5")
  implementation("androidx.lifecycle:lifecycle-runtime-compose:2.8.5")
  implementation("androidx.lifecycle:lifecycle-viewmodel-compose:2.8.5")
  implementation("androidx.compose.ui:ui")
  implementation("androidx.compose.ui:ui-tooling-preview")
  implementation("androidx.compose.material3:material3")
  implementation("androidx.compose.material:material-icons-extended")
  implementation("com.google.android.material:material:1.12.0")
  implementation("androidx.compose.foundation:foundation")
  implementation("org.jetbrains.kotlinx:kotlinx-coroutines-android:1.8.1")
  implementation("com.squareup.okhttp3:okhttp:4.12.0")

  debugImplementation("androidx.compose.ui:ui-tooling")
  debugImplementation("androidx.compose.ui:ui-test-manifest")

  testImplementation("junit:junit:4.13.2")
  testImplementation("org.jetbrains.kotlinx:kotlinx-coroutines-test:1.8.1")
  testImplementation("com.squareup.okhttp3:mockwebserver:4.12.0")
  testImplementation("org.json:json:20240303")
  testImplementation("org.robolectric:robolectric:4.12.1")
  testImplementation("androidx.test:core-ktx:1.6.1")
  testImplementation(composeBom)
  testImplementation("androidx.compose.ui:ui-test-junit4")
  testImplementation("androidx.compose.ui:ui-test-manifest")

  androidTestImplementation("androidx.test.ext:junit:1.2.1")
  androidTestImplementation("androidx.test.espresso:espresso-core:3.6.1")
  androidTestImplementation("androidx.compose.ui:ui-test-junit4")
}
