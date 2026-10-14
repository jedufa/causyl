# EIEL Native Android Chat

Native Android app for a single-pane Codex chat interface.

Product boundary:
- This app is the primary end-user mobile surface.
- WebUI remains the control-plane/admin surface.
- See [../../docs/manuals/NATIVE_APP_AND_WEBUI_BOUNDARY.md](../../docs/manuals/NATIVE_APP_AND_WEBUI_BOUNDARY.md).

## Defaults
- Platform: Android only
- Stack: Kotlin + Jetpack Compose
- Min SDK: 26
- Server base URL default: `http://192.0.2.1:8787`
- Stream path default: `/api/mobile/chat/stream`
- Chat turn timeout default: `7000s`

You can override at build time:

```bash
./gradlew :app:assembleDebug \
  -PEIEL_SERVER_BASE_URL=http://192.0.2.1:8787 \
  -PEIEL_CHAT_STREAM_PATH=/api/mobile/chat/stream \
  -PEIEL_AUTH_TOKEN= \
  -PEIEL_CHAT_TIMEOUT_S=7000
```

## Build
Use Android Studio or Gradle wrapper in this directory.

```bash
./gradlew assembleDebug
```

## CLI setup without Android Studio

From repo root:

```bash
infra/mobile/install_android_cli_stack.sh
source infra/mobile/android_env.sh
infra/mobile/bootstrap_android_gradle_wrapper.sh
cd infra/mobile/android && ./gradlew :app:testDebugUnitTest
```

## Mobile Agent Startup

If you want the mobile web server and Codex sessions to inherit the same Android/Java/Gradle env, use the repo launchers from the repo root:

```bash
infra/bin/start_mobile_web_server.sh --host 0.0.0.0 --port 8787
infra/bin/start_mobile_codex.sh
```

Both wrappers load `infra/mobile/android_env.sh` and export repo-local `GRADLE_USER_HOME` before launch.
They also default future wrapper-launched agent processes to `codex` backend, `oneshot` protocol, and `EIEL_CODEX_UNRESTRICTED=1`.
On WSL, the shared mobile agent env also auto-detects a reachable `adb` server and exports `ADB_SERVER_SOCKET`
for downstream agents and deploy scripts. For a Windows-hosted emulator, expose the Windows `adb` listener to WSL once:

```powershell
netsh interface portproxy add v4tov4 listenaddress=0.0.0.0 listenport=5037 connectaddress=127.0.0.1 connectport=5037
netsh advfirewall firewall add rule name="ADB 5037 from WSL" dir=in action=allow protocol=TCP localport=5037
```

After that, `infra/bin/mobile_agent_env.sh`, `infra/bin/start_mobile_codex.sh`, and the deploy/build helpers will
reuse the Windows emulator automatically as long as `adb.exe devices -l` works on Windows.

If you want plain `codex` in your current shell to do the same thing, activate the shell hook once:

```bash
source infra/bin/activate_mobile_agent_shell.sh
```

After that, calling `codex` from that shell refreshes the same mobile agent env before launch.

To confirm the whole mobile toolchain is visible to the launched stack, run:

```bash
infra/bin/check_mobile_agent_env.sh
```

That probes the same wrapper environment for `java`, `javac`, `sdkmanager`, `adb`, `emulator`, and `./gradlew -version`.

To smoke-test the live mobile loop against a running emulator/device and server, run:

```bash
infra/mobile/smoke_test_mobile_stack.sh
```

That verifies the shared mobile agent env, `adb` visibility, device boot completion, WebUI health at `/api/health`,
and a best-effort socket probe from the device to the configured server host/port.

## Install
From repo root:

```bash
infra/mobile/build_and_install_android.sh
```

One-command setup + build + deploy:

```bash
infra/mobile/deploy_debug_phone.sh
```

## Stability / Failure Behavior
- No network: send is blocked early with a clear error and retry option.
- Server down/404: stream fails with error; failed prompt is retained for retry.
- App killed mid-stream: in-flight prompt is persisted and restored as retryable.
- Draft text is persisted locally and restored on next launch.

## Signed Release Build
Use env vars for signing; do not commit keystore/secrets.

```bash
export EIEL_RELEASE_STORE_FILE=/absolute/path/to/keystore.jks
export EIEL_RELEASE_STORE_PASSWORD='***'
export EIEL_RELEASE_KEY_ALIAS='***'
export EIEL_RELEASE_KEY_PASSWORD='***'

infra/mobile/build_release_signed.sh
```

Optional AAB:

```bash
EIEL_BUILD_BUNDLE=true infra/mobile/build_release_signed.sh
```

## Field Logs
Pull app-side rotating logs for troubleshooting:

```bash
infra/mobile/pull_mobile_app_logs.sh debug
```
