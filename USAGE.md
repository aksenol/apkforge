# Usage Guide

Detailed usage instructions for the Minimal Android APK Build System.

## Commands

### `./build.py setup`

Downloads and installs the Android SDK into `.android-sdk/`.

```bash
./build.py setup
```

What happens:
1. Downloads the Android command-line tools zip (~150 MB) from Google
2. Extracts to `.android-sdk/cmdline-tools/latest/`
3. Runs `sdkmanager` to install `build-tools;34.0.0` and `platforms;android-34`
4. Auto-accepts SDK licenses
5. If `kotlin.version` is set in `build.yaml`, downloads the Kotlin compiler from GitHub releases to `.kotlin/`
6. If `compose: true` is set, downloads the Compose compiler plugin JAR to `.kotlin/compose-plugin/`

The SDK and Kotlin compiler are only downloaded once. Re-running `setup` skips already-installed components.

### `./build.py build`

Runs the full 7-step build pipeline and produces the final APK.

```bash
./build.py build
```

Output: `.build/app-debug.apk`

The build validates your config first (checks that manifest, source dirs, and resource dirs exist) and fails early with clear error messages if anything is missing.

On first build, a `debug.keystore` is auto-generated in the project root using `keytool`. This keystore is reused for all subsequent builds.

### `./build.py clean`

Removes the `.build/` directory and all intermediate artifacts.

```bash
./build.py clean
```

This does **not** remove `.android-sdk/`, `.kotlin/`, `.deps/`, or `debug.keystore`. To fully reset:

```bash
./build.py clean
rm -rf .android-sdk/ .kotlin/ .deps/ debug.keystore
```

### Custom Config File

All commands accept `--config` (`-c`) to use a different YAML config:

```bash
./build.py -c another-app.yaml build
```

## Adapting for Your Own App

### 1. Edit `build.yaml`

Update the `app` section with your package name, app name, and version:

```yaml
app:
  package: com.mycompany.myapp
  name: MyApp
  version_code: 1
  version_name: "1.0"
```

### 2. Point to Your Sources

Set the `paths` section to your project layout:

```yaml
paths:
  manifest: app/AndroidManifest.xml
  sources:
    - app/src
  resources:
    - app/res
  libs:
    - libs/some-library.jar
```

Multiple source and resource directories are supported. All paths are relative to the directory containing `build.yaml`.

### 3. Configure Kotlin (Optional)

To use Kotlin, add a `kotlin` section to `build.yaml`:

```yaml
kotlin:
  version: "2.0.21"
```

Then run `./build.py setup` to download the Kotlin compiler. Source directories can contain `.kt` files, `.java` files, or a mix of both. If no `.kt` files are found, the build uses `javac` only (backward compatible).

### 3b. Enabling Jetpack Compose (Optional)

[Jetpack Compose](https://developer.android.com/compose) is Android's modern declarative UI toolkit. It works as a Kotlin compiler plugin — the `@Composable` annotation triggers code transformation at compile time.

To enable Compose, set `compose: true` under the `kotlin` section and add Compose dependencies:

```yaml
kotlin:
  version: "2.1.20"
  compose: true

dependencies:
  - androidx.activity:activity-compose:1.10.1
  - androidx.compose.ui:ui:1.7.8
  - androidx.compose.foundation:foundation:1.7.8
  - androidx.compose.material3:material3:1.3.2
```

Then run setup and build:

```bash
./build.py setup   # Downloads Kotlin compiler + Compose plugin JAR
./build.py build   # Builds APK with Compose support
```

**How it works:**
- `./build.py setup` downloads `kotlin-compose-compiler-plugin-<version>.jar` from Maven Central to `.kotlin/compose-plugin/`
- During compilation, the plugin JAR is passed to `kotlinc` via `-Xplugin`, enabling `@Composable` code transformation
- Since Kotlin 2.0+, the Compose compiler plugin version matches the Kotlin version exactly
- Compose UI libraries (ui, foundation, material3) are standard Maven artifacts handled by the existing dependency resolver

**Requirements:**
- `kotlin.version` must be set (Compose is a Kotlin compiler plugin)
- Kotlin 2.0+ recommended (plugin version matches Kotlin version)
- Use `ComponentActivity` instead of `AppCompatActivity` as your base class

### 4. Update AndroidManifest.xml

Ensure the `package` attribute in your manifest matches `app.package` in `build.yaml`:

```xml
<manifest xmlns:android="http://schemas.android.com/apk/res/android"
    package="com.mycompany.myapp">
```

### 5. Build

```bash
./build.py build
```

## Adding Maven Dependencies

Declare Maven coordinates in `build.yaml` under the `dependencies` section:

```yaml
repositories:
  - https://dl.google.com/dl/android/maven2
  - https://repo1.maven.org/maven2

dependencies:
  - androidx.appcompat:appcompat:1.7.0
  - com.google.android.material:material:1.12.0
```

The build system will:
1. Download POM files and resolve the full transitive dependency tree
2. Download JAR and AAR artifacts from the configured repositories
3. Extract AAR files (classes.jar, resources, manifest)
4. Include dependency JARs in the compile classpath and DEX step
5. Compile and link AAR resources with resource overlay merging
6. Generate R classes for library packages

Dependencies are cached in `.deps/` (versioned and immutable), so subsequent builds skip all downloads.

### Cache Layout

```
.deps/
├── poms/        # Downloaded POM files
├── artifacts/   # Downloaded JAR/AAR files
└── extracted/   # Extracted AAR contents (classes.jar, res/, AndroidManifest.xml)
```

### Supported Dependency Features

- Transitive dependency resolution
- Parent POM inheritance
- BOM imports (`<type>pom</type>` + `<scope>import</scope>`)
- `<dependencyManagement>` version pinning
- Version range parsing (e.g., `[1.7.0]` → `1.7.0`)
- Automatic skip of `test`, `provided`, `system` scoped and optional dependencies

### Limitations

- No manifest merging: the app manifest must declare all required components and themes
- No ProGuard/R8 consumer rules from AARs
- No version conflict resolution (first-seen version wins)

## Adding Manual JAR Dependencies

Place JAR files in a `libs/` directory (or anywhere you like) and list them in `build.yaml`:

```yaml
paths:
  libs:
    - libs/gson-2.10.jar
    - libs/okhttp-4.12.jar
```

These JARs are added to the compile classpath and DEXed into the APK automatically. For simple projects without external libraries, leave `libs` as `[]`.

## Changing SDK Versions

To target a different API level or build-tools version:

```yaml
sdk:
  min_sdk: 24
  target_sdk: 35
  build_tools: "35.0.0"
```

Then re-run setup to install the new components:

```bash
./build.py setup
./build.py build
```

## Verifying the APK

After building, inspect the APK metadata:

```bash
.android-sdk/build-tools/34.0.0/aapt2 dump badging .build/app-debug.apk
```

This prints the package name, version code/name, SDK versions, permissions, and declared activities.

To verify the signature:

```bash
.android-sdk/build-tools/34.0.0/apksigner verify --print-certs .build/app-debug.apk
```

## Installing on a Device

With `adb` on your PATH (included in Android SDK platform-tools, or install separately):

```bash
adb install .build/app-debug.apk
```

To also install platform-tools via this build system, you can manually run:

```bash
.android-sdk/cmdline-tools/latest/bin/sdkmanager --sdk_root=.android-sdk "platform-tools"
.android-sdk/platform-tools/adb install .build/app-debug.apk
```

## Testing with the Android Emulator

### Prerequisites

Install the emulator, a system image, and platform-tools (~1.5 GB total):

```bash
.android-sdk/cmdline-tools/latest/bin/sdkmanager \
  --sdk_root=.android-sdk \
  "platform-tools" "emulator" "system-images;android-34;google_apis;x86_64"
```

On Ubuntu/Debian, the emulator also requires `libpulse0` (even with `-no-audio`):

```bash
sudo apt install -y libpulse0
```

### KVM Setup (Required for Usable Performance)

Without KVM hardware acceleration, the emulator boots in 10-30 minutes. With KVM, boot takes ~48 seconds.

**Linux (native):**
```bash
sudo modprobe kvm-intel   # Intel CPUs
sudo modprobe kvm-amd     # AMD CPUs
sudo chmod 666 /dev/kvm
```

**WSL2:**
1. Create/edit `C:\Users\<username>\.wslconfig`:
   ```ini
   [wsl2]
   nestedVirtualization=true
   ```
2. Restart WSL from Windows PowerShell: `wsl --shutdown`
3. Reopen WSL and load the module:
   ```bash
   sudo modprobe kvm-intel
   sudo chmod 666 /dev/kvm
   ```

Verify KVM is working:
```bash
ls -la /dev/kvm   # Should show crw-rw-rw-
```

### Create an AVD

```bash
echo "no" | .android-sdk/cmdline-tools/latest/bin/avdmanager create avd \
  --name "test_device" \
  --package "system-images;android-34;google_apis;x86_64" \
  --device "pixel" --force
```

### Launch, Install, and Verify

```bash
# Launch emulator (with KVM)
.android-sdk/emulator/emulator -avd test_device \
  -gpu swiftshader_indirect -no-snapshot -no-audio &

# Wait for full boot
ADB=.android-sdk/platform-tools/adb
$ADB wait-for-device
while [ "$($ADB shell getprop sys.boot_completed 2>/dev/null)" != "1" ]; do sleep 5; done

# Install and launch app
$ADB install .build/app-debug.apk
$ADB shell am start -n com.example.hello/.MainActivity

# Verify app is running
$ADB shell dumpsys activity activities | grep "topResumedActivity"

# Take a screenshot
$ADB shell screencap /sdcard/screen.png && \
  $ADB pull /sdcard/screen.png .build/emulator-screenshot.png

# Check for crashes
$ADB logcat -d | grep -i "FATAL\|AndroidRuntime" | tail -n 10

# Kill emulator when done
$ADB emu kill
```

### Without KVM (Fallback)

If KVM cannot be enabled, the emulator still works but is extremely slow:

```bash
.android-sdk/emulator/emulator -avd test_device \
  -no-accel -no-window -gpu swiftshader_indirect \
  -no-snapshot -no-audio -memory 2048 &
```

### Emulator Troubleshooting

| Problem | Fix |
|---------|-----|
| `libpulse.so.0` not found | `sudo apt install -y libpulse0` |
| `/dev/kvm` not found | Load module: `sudo modprobe kvm-intel` (Intel) or `kvm-amd` (AMD) |
| `/dev/kvm` permission denied | `sudo chmod 666 /dev/kvm` |
| WSL2: KVM still missing after `.wslconfig` | Run `wsl --shutdown` from Windows, then reopen WSL |
| Emulator exits immediately | Check `libpulse0` is installed; verify system image matches AVD config |
| Boot takes forever | Enable KVM — without it, expect 10-30 min boot times |

## Build Pipeline Details

Each step produces intermediate files in `.build/`:

```
.build/
├── compiled_res/          # Step 1: .flat files from app resources
├── aar_compiled_res/      # Step 1: .flat files from AAR library resources
├── gen/                   # Step 2: R.java generated by aapt2 link
│   └── com/example/hello/
│       └── R.java
├── base.apk               # Step 2: resources-only APK
├── classes/                # Step 3: .class files from javac/kotlinc
│   └── com/example/hello/
│       ├── MainActivity.class
│       └── R.class
├── dex/                    # Step 4: DEX files from d8
│   ├── classes.dex
│   └── classes2.dex       # (when multi-DEX is needed)
├── dex.apk                 # Step 5: base.apk + all DEX files
├── aligned.apk             # Step 6: zipaligned copy
└── app-debug.apk           # Step 7: signed final APK
```

Intermediate files are useful for debugging build failures. For example, if Step 3 (`javac`) fails, check that `R.java` was generated in `.build/gen/` by Step 2.

## Environment Variables

The build tool does not require any environment variables. It manages its own SDK path internally. During SDK installation, it sets `ANDROID_SDK_ROOT` and `ANDROID_HOME` for the `sdkmanager` subprocess.

## Platform Support

| Platform | Status |
|----------|--------|
| Linux x86_64 | Supported |
| macOS (Intel/Apple Silicon) | Supported |
| Windows | Supported (uses `.bat` wrappers for SDK tools) |
| WSL | Supported (uses Linux SDK) |
