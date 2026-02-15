# Minimal Android APK Build System

A single-file, zero-dependency Python build tool that produces debug Android APKs from a declarative YAML config. Supports both Java and Kotlin sources, with automatic Maven dependency resolution for AndroidX and other libraries.

## Prerequisites

- **Python 3.6+**
- **JDK 8+** (`javac` and `keytool` on PATH)
- Internet connection (first run only, to download the Android SDK)

## Quick Start

```bash
./build.py setup   # Download Android SDK (~500 MB)
./build.py build   # Produce .build/app-debug.apk
./build.py clean   # Remove build artifacts
```

## Project Structure

```
├── build.py                              # Build tool (single file, ~1100 lines)
├── build.yaml                            # Declarative project config
├── sample/                               # Sample app (Kotlin + AppCompatActivity)
│   ├── AndroidManifest.xml
│   ├── src/com/example/hello/
│   │   └── MainActivity.kt
│   └── res/
│       ├── layout/activity_main.xml
│       └── values/strings.xml
├── .build/                               # Build artifacts (gitignored)
├── .android-sdk/                         # Downloaded SDK (gitignored)
├── .kotlin/                              # Downloaded Kotlin compiler (gitignored)
└── .deps/                                # Maven dependency cache (gitignored)
```

## Architecture

### Components

| Component | Role |
|-----------|------|
| `mini_yaml_load()` | Built-in YAML parser fallback (uses PyYAML when available) |
| `Config` | Loads `build.yaml`, validates paths, resolves relative paths, applies defaults |
| `SDKManager` | Downloads cmdline-tools, installs build-tools and platform via `sdkmanager` |
| `KotlinManager` | Downloads and installs the Kotlin compiler from GitHub releases |
| `MavenResolver` | Resolves Maven dependencies (transitive), downloads JARs/AARs, extracts AARs |
| `DebugKeystore` | Auto-generates `debug.keystore` via `keytool` on first build |
| `Builder` | Executes the 7-step APK build pipeline (with dependency integration) |

### Build Pipeline

| Step | Tool | Description |
|------|------|-------------|
| 0 | `MavenResolver` | Resolve Maven dependencies, download POMs/JARs/AARs, extract AARs |
| 1 | `aapt2 compile` | Compile app + AAR resource files to `.flat` binary format |
| 2 | `aapt2 link` | Link flat resources (with overlay merging), generate `R.java`, produce base APK |
| 3 | `javac` / `kotlinc` | Compile Java/Kotlin sources + `R.java` against `android.jar` + dependency JARs |
| 4 | `d8` | Convert `.class` files + dependency JARs + `kotlin-stdlib.jar` to DEX (multi-DEX supported) |
| 5 | Python `zipfile` | Inject `classes.dex` (and `classes2.dex`, etc.) into the APK |
| 6 | `zipalign` | 4-byte align uncompressed entries for memory-mapped access |
| 7 | `apksigner` | Sign APK with the auto-generated debug keystore |

### Build Artifacts (`.build/`)

| File | Pipeline stage |
|------|---------------|
| `compiled_res/*.flat` | Step 1 output (app resources) |
| `aar_compiled_res/*.flat` | Step 1 output (AAR library resources) |
| `gen/**/R.java` | Step 2 output |
| `base.apk` | Step 2 output (resources only) |
| `classes/*.class` | Step 3 output |
| `dex/classes.dex` | Step 4 output |
| `dex/classes2.dex` | Step 4 output (multi-DEX, when needed) |
| `dex.apk` | Step 5 output (resources + DEX) |
| `aligned.apk` | Step 6 output |
| `app-debug.apk` | Step 7 output (final, signed) |

## Design Decisions

- **Single Python file** -- No package structure, no `setup.py`. Copy `build.py` into any project and go.
- **Zero mandatory dependencies** -- The built-in `mini_yaml_load()` handles the config subset needed. PyYAML is used automatically when installed but never required. Maven resolution uses only `urllib.request` and `xml.etree.ElementTree` from stdlib.
- **Maven dependency resolution** -- Declare `group:artifact:version` coordinates in `build.yaml`. The resolver downloads POMs, walks transitive dependencies, fetches JARs/AARs, and extracts AAR contents. Cached in `.deps/` so subsequent builds skip downloads.
- **AndroidX / AppCompat support** -- The sample app uses `AppCompatActivity` with a Material theme. AAR resources are compiled and merged via `--auto-add-overlay`, and R classes are generated for library packages with `--extra-packages`.
- **No manifest merging** -- Library manifests from AARs are not merged. The app manifest must declare everything it needs. This is a deliberate simplification.
- **Multi-DEX** -- With many dependencies, `d8` may produce multiple DEX files. All `classes*.dex` files are injected into the APK. Works natively on min SDK 21+.
- **`d8` not `dx`** -- `dx` is deprecated. `d8` is the modern DEX compiler included in build-tools 28+.
- **Python `zipfile` for DEX injection** -- Avoids the deprecated `aapt` v1 `add` command. Standard library, no extra tools.
- **Kotlin support** -- Optional. When `kotlin.version` is set in `build.yaml`, the Kotlin compiler is auto-downloaded during setup. Mixed Java/Kotlin projects are supported. Java-only projects work unchanged when the `kotlin:` section is omitted.
- **Java 1.8 source/target** -- Maximum compatibility across Android API levels.
- **SDK in project directory** -- Installed to `.android-sdk/`, `.kotlin/`, and `.deps/` inside the project root. Self-contained, no system-wide side effects, easy to delete.
- **Debug builds only** -- No ProGuard/R8 shrinking, no release signing. Keeps the tool focused and simple.

## Configuration Reference

All settings live in `build.yaml`:

```yaml
app:
  package: com.example.hello    # Application package name
  name: HelloWorld              # Human-readable app name
  version_code: 1               # Integer version code
  version_name: "1.0"           # Display version string

sdk:
  min_sdk: 21                   # Minimum API level
  target_sdk: 34                # Target API level
  build_tools: "34.0.0"        # Build tools version to install/use

kotlin:
  version: "2.0.21"            # Optional: omit section for Java-only projects

repositories:                             # Maven repositories (defaults shown)
  - https://dl.google.com/dl/android/maven2   # Google Maven (AndroidX)
  - https://repo1.maven.org/maven2             # Maven Central

dependencies:                             # Maven coordinates (group:artifact:version)
  - androidx.appcompat:appcompat:1.7.0
  - com.google.android.material:material:1.12.0

paths:
  manifest: sample/AndroidManifest.xml
  sources:                      # Java/Kotlin source roots
    - sample/src
  resources:                    # Android resource directories
    - sample/res
  libs: []                      # Extra JAR files for classpath

output:
  dir: .build                   # Build output directory
  apk_name: app-debug.apk      # Final APK filename
```

Use `--config`/`-c` to point at a different config file:

```bash
./build.py -c myapp.yaml build
```

## Testing on Emulator

The build system can be paired with the Android emulator to verify APKs run correctly. The emulator, system images, and platform-tools are not installed by `./build.py setup` — they must be installed separately via `sdkmanager`.

### Quick Setup

```bash
# Install emulator components (~1.5 GB download)
.android-sdk/cmdline-tools/latest/bin/sdkmanager \
  --sdk_root=.android-sdk \
  "platform-tools" "emulator" "system-images;android-34;google_apis;x86_64"

# Create AVD
echo "no" | .android-sdk/cmdline-tools/latest/bin/avdmanager create avd \
  --name "test_device" \
  --package "system-images;android-34;google_apis;x86_64" \
  --device "pixel" --force

# Launch emulator (KVM required for reasonable performance)
.android-sdk/emulator/emulator -avd test_device -gpu swiftshader_indirect -no-snapshot -no-audio &

# Wait for boot, install, and launch
.android-sdk/platform-tools/adb wait-for-device
while [ "$(.android-sdk/platform-tools/adb shell getprop sys.boot_completed 2>/dev/null)" != "1" ]; do sleep 5; done
.android-sdk/platform-tools/adb install .build/app-debug.apk
.android-sdk/platform-tools/adb shell am start -n com.example.hello/.MainActivity

# Verify
.android-sdk/platform-tools/adb shell dumpsys activity activities | grep "topResumedActivity"
.android-sdk/platform-tools/adb shell screencap /sdcard/screen.png && \
  .android-sdk/platform-tools/adb pull /sdcard/screen.png .build/emulator-screenshot.png

# Cleanup
.android-sdk/platform-tools/adb emu kill
```

### KVM on WSL2

The emulator requires KVM for usable performance (~48s boot with KVM vs 10-30 min without). On WSL2:

1. Create `C:\Users\<username>\.wslconfig`:
   ```ini
   [wsl2]
   nestedVirtualization=true
   ```
2. Restart WSL: `wsl --shutdown` from Windows PowerShell
3. Inside WSL: `sudo modprobe kvm-intel && sudo chmod 666 /dev/kvm`

### Dependencies

The emulator requires `libpulse0` on Ubuntu/Debian even when audio is disabled:

```bash
sudo apt install -y libpulse0
```

## Troubleshooting

| Problem | Fix |
|---------|-----|
| `javac` not found | Install JDK 8+ and ensure `JAVA_HOME/bin` is on `PATH` |
| `keytool` not found | Same as above -- `keytool` ships with every JDK |
| Kotlin compiler not found | Run `./build.py setup` with `kotlin.version` set in `build.yaml` |
| SDK download fails | Check internet connection; retry `./build.py setup` |
| `aapt2 link` fails | Ensure `AndroidManifest.xml` `package` matches `build.yaml` `app.package` |
| `d8` fails with unsupported class version | Compile with JDK 11 or lower, or use `--release 8` if on JDK 17+ |
| Emulator: `libpulse.so.0` not found | `sudo apt install -y libpulse0` |
| Emulator: very slow / no KVM | Enable nested virtualization in `.wslconfig` (WSL2), or `sudo modprobe kvm-intel` |

## License

This project is provided as-is for educational and prototyping purposes.
