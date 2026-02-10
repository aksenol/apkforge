# Minimal Android APK Build System

A single-file, zero-dependency Python build tool that produces debug Android APKs from a declarative YAML config. Supports both Java and Kotlin sources.

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
├── build.py                              # Build tool (single file, ~700 lines)
├── build.yaml                            # Declarative project config
├── sample/                               # Sample HelloWorld app (Kotlin)
│   ├── AndroidManifest.xml
│   ├── src/com/example/hello/
│   │   └── MainActivity.kt
│   └── res/
│       ├── layout/activity_main.xml
│       └── values/strings.xml
├── .build/                               # Build artifacts (gitignored)
├── .android-sdk/                         # Downloaded SDK (gitignored)
└── .kotlin/                              # Downloaded Kotlin compiler (gitignored)
```

## Architecture

### Components

| Component | Role |
|-----------|------|
| `mini_yaml_load()` | Built-in YAML parser fallback (uses PyYAML when available) |
| `Config` | Loads `build.yaml`, validates paths, resolves relative paths, applies defaults |
| `SDKManager` | Downloads cmdline-tools, installs build-tools and platform via `sdkmanager` |
| `KotlinManager` | Downloads and installs the Kotlin compiler from GitHub releases |
| `DebugKeystore` | Auto-generates `debug.keystore` via `keytool` on first build |
| `Builder` | Executes the 7-step APK build pipeline |

### Build Pipeline

| Step | Tool | Description |
|------|------|-------------|
| 1 | `aapt2 compile` | Compile each resource file to `.flat` binary format |
| 2 | `aapt2 link` | Link flat resources, generate `R.java`, produce base APK |
| 3 | `javac` / `kotlinc` | Compile Java/Kotlin sources + `R.java` against `android.jar` |
| 4 | `d8` | Convert `.class` files (+ `kotlin-stdlib.jar`) to Dalvik `classes.dex` |
| 5 | Python `zipfile` | Inject `classes.dex` into the APK |
| 6 | `zipalign` | 4-byte align uncompressed entries for memory-mapped access |
| 7 | `apksigner` | Sign APK with the auto-generated debug keystore |

### Build Artifacts (`.build/`)

| File | Pipeline stage |
|------|---------------|
| `compiled_res/*.flat` | Step 1 output |
| `gen/**/R.java` | Step 2 output |
| `base.apk` | Step 2 output (resources only) |
| `classes/*.class` | Step 3 output |
| `dex/classes.dex` | Step 4 output |
| `dex.apk` | Step 5 output (resources + DEX) |
| `aligned.apk` | Step 6 output |
| `app-debug.apk` | Step 7 output (final, signed) |

## Design Decisions

- **Single Python file** -- No package structure, no `setup.py`. Copy `build.py` into any project and go.
- **Zero mandatory dependencies** -- The built-in `mini_yaml_load()` handles the config subset needed. PyYAML is used automatically when installed but never required.
- **`Activity` not `AppCompatActivity`** -- The sample app extends `android.app.Activity` directly. This avoids pulling in AndroidX/appcompat and keeps the build free of external AAR/JAR dependencies.
- **`d8` not `dx`** -- `dx` is deprecated. `d8` is the modern DEX compiler included in build-tools 28+.
- **Python `zipfile` for DEX injection** -- Avoids the deprecated `aapt` v1 `add` command. Standard library, no extra tools.
- **Kotlin support** -- Optional. When `kotlin.version` is set in `build.yaml`, the Kotlin compiler is auto-downloaded during setup. Mixed Java/Kotlin projects are supported. Java-only projects work unchanged when the `kotlin:` section is omitted.
- **Java 1.8 source/target** -- Maximum compatibility across Android API levels.
- **SDK in project directory** -- Installed to `.android-sdk/` and `.kotlin/` inside the project root. Self-contained, no system-wide side effects, easy to delete.
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

## Troubleshooting

| Problem | Fix |
|---------|-----|
| `javac` not found | Install JDK 8+ and ensure `JAVA_HOME/bin` is on `PATH` |
| `keytool` not found | Same as above -- `keytool` ships with every JDK |
| Kotlin compiler not found | Run `./build.py setup` with `kotlin.version` set in `build.yaml` |
| SDK download fails | Check internet connection; retry `./build.py setup` |
| `aapt2 link` fails | Ensure `AndroidManifest.xml` `package` matches `build.yaml` `app.package` |
| `d8` fails with unsupported class version | Compile with JDK 11 or lower, or use `--release 8` if on JDK 17+ |

## License

This project is provided as-is for educational and prototyping purposes.
