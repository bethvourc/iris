# Packaging: the embedded Python runtime

The shipped app must run the Python daemon on a clean Mac — no system Python, no
Homebrew, no dev tools. To make that true, Release builds embed a fully
relocatable Python interpreter plus the daemon and its locked dependencies
inside the app bundle. Dev builds are unchanged: they run the daemon from the
repo checkout via `uv`.

This document covers the bundle layout, how the runtime is built, and how to
update it. Code signing and notarization of the embedded binaries are covered
separately in Step 7.2.

## Bundle layout

```
Iris.app/
  Contents/
    MacOS/Iris                 # the SwiftUI app
    Resources/
      iris-runtime/
        VERSION                # provenance: pins + build timestamp
        python/
          bin/python3          # python-build-standalone interpreter
          lib/python3.12/
            site-packages/
              iris/            # the daemon package
              ...              # locked third-party dependencies
```

The app launches the daemon as:

```
Contents/Resources/iris-runtime/python/bin/python3 -m iris serve \
    --port <port> --json-logs --log-dir ~/Library/Logs/Iris
```

We invoke the interpreter directly with `-m iris` rather than the generated
`iris` console script: console-script shebangs hard-code the interpreter path at
build time and are not relocatable, whereas `python3 -m iris` resolves the
package from the runtime's own `site-packages`.

## How the daemon path is resolved

`AppModel.daemonConfiguration()` chooses a mode at launch:

1. **Dev** — if the repo is found (`IRIS_REPO_ROOT`, or by walking up from the
   compiled source path to a `pyproject.toml`), run `uv run iris serve` from the
   checkout. Source edits take effect without repackaging.
2. **Bundled** — otherwise, if `Contents/Resources/iris-runtime/python/bin/python3`
   exists, run the embedded runtime (`DaemonConfiguration.bundled`).
3. Neither present → no daemon configuration (the menu bar shows "stopped").

`DaemonConfiguration.bundledRuntimeExists(resourcesURL:)` is the existence check.

## Building the runtime

`apps/macos/scripts/package_python.sh` builds the runtime. It is wired as the
**"Embed Python runtime"** post-build script on the `Iris` target (see
`project.yml`) and **no-ops for Debug** — so it only adds time to Release builds.

What it does, in order:

1. Downloads the pinned [python-build-standalone][pbs] `install_only` archive for
   the target architecture (cached under
   `~/Library/Caches/Iris/python-build-standalone`).
2. Extracts the relocatable interpreter into `iris-runtime/python/`.
3. Exports locked requirements from the committed `uv.lock`
   (`uv export --frozen --no-dev --no-emit-project`) and installs them into the
   runtime with `--require-hashes`.
4. Installs the `iris` package itself (`--no-deps`; deps are already pinned).
5. Prunes GUI/test/build cruft (`tkinter`, `idlelib`, `ensurepip`, `test`, …).
6. Writes `VERSION` and runs a sanity import of `iris.cli` / `iris.gateway`.

Run it standalone (outside Xcode) against any Resources directory:

```sh
CONFIGURATION=Release apps/macos/scripts/package_python.sh /path/to/Resources
```

### Architecture (universal)

The script builds one runtime per architecture in `$ARCHS` (default
`arm64 x86_64`), then merges them into a single universal tree:

1. For each arch, extract the matching python-build-standalone archive and
   install the locked deps with uv's `--python-platform` / `--python-version`
   (so x86_64 wheels install on an Apple Silicon host with no Rosetta).
2. The first arch becomes the primary tree; for every other arch, each Mach-O
   file (`python3`, `*.dylib`, `*.so`) is combined with `lipo` into a fat binary
   (slices deduped, so wheels that already ship universal are handled). Identical
   pure-Python/text files are kept from the primary.

The result is verified with `lipo -archs` on the interpreter (recorded in
`VERSION` as `interpreter_archs`). A Release build with `ONLY_ACTIVE_ARCH=NO`
(the default for `xcodebuild ... -configuration Release`) yields a fully
universal app — both `Contents/MacOS/Iris` and the embedded runtime.

To build a single-arch runtime (faster, dev convenience): `ARCHS=arm64 …`.

### Wake word (optional, large)

The on-device wake-word stack (`onnxruntime`, `openwakeword`, `numpy`, …) is
**not** bundled by default to keep the app lean; the feature is opt-in and imports
lazily. To include it, build with:

```sh
IRIS_BUNDLE_WAKEWORD=1   # env var read by package_python.sh
```

## Reproducibility

Two pins make rebuilds deterministic:

- **Interpreter**: `PYTHON_VERSION` + `PBS_RELEASE` at the top of
  `package_python.sh`.
- **Dependencies**: the committed `uv.lock` (hashes enforced at install).

The same pins + lock produce the same tree on any machine.

## Updating

- **Bump Python / python-build-standalone**: edit `PYTHON_VERSION` and
  `PBS_RELEASE` in `package_python.sh` (pick a [release][pbs-releases] that ships
  the chosen version), then do a clean Release build. Update the `python3.12`
  paths in this doc and in `DaemonConfiguration.bundled` if the minor version
  changes.
- **Bump dependencies**: edit `pyproject.toml`, run `uv lock`, commit the updated
  `uv.lock`. The next Release build picks them up.

## Verifying on a clean Mac

The real test is a Release build run on a machine (or a fresh user account) with
no Python and no dev tools: the app launches, the menu bar reaches **healthy**,
and a voice session works. Locally you can spot-check the embedded runtime
directly:

```sh
APP=".../Build/Products/Release/Iris.app"
"$APP/Contents/Resources/iris-runtime/python/bin/python3" -m iris serve --help
```

[pbs]: https://github.com/astral-sh/python-build-standalone
[pbs-releases]: https://github.com/astral-sh/python-build-standalone/releases
