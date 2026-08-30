#!/bin/bash
set -e

source "$HOME/.sdkman/bin/sdkman-init.sh"

SDK="/opt/android-sdk"
BUILD_TOOLS="$SDK/build-tools/35.0.0"
PLATFORM="$SDK/platforms/android-34/android.jar"
PROJECT="/tmp/alan"
OUT="$PROJECT/build/manual"

echo "=== Building Alan manually ==="

mkdir -p "$OUT/classes" "$OUT/res" "$OUT/src-gen" "$OUT/dex"

# 1. Collect dependencies
echo "=== Collecting dependencies ==="
DEPS=$(find ~/.gradle/caches -name '*.jar' 2>/dev/null | grep -E '(kotlin-stdlib|kotlinx-coroutines|kotlinx-serialization|androidx|com.google.guava|org.jetbrains)' | head -100 | tr '\n' ':')

# 2. Compile Kotlin
echo "=== Compiling Kotlin ==="
kotlinc -cp "$PLATFORM:$DEPS" \
  -d "$OUT/classes" \
  -jvm-target 17 \
  -language-version 1.9 \
  -Xuse-experimental=kotlinx.coroutines.ExperimentalCoroutinesApi \
  $(find "$PROJECT/app/src/main/java" -name "*.kt") \
  2>&1 | grep -v "warning:" | tail -20

# 3. Compile resources
echo "=== Compiling Resources ==="
$BUILD_TOOLS/aapt package -f -m \
  -S "$PROJECT/app/src/main/res" \
  -M "$PROJECT/app/src/main/AndroidManifest.xml" \
  -I "$PLATFORM" \
  -J "$OUT/src-gen" \
  --auto-add-overlay \
  --non-constant-id 2>&1 | tail -10

# 4. Compile generated R.java
echo "=== Compiling R.java ==="
find "$OUT/src-gen" -name "*.java" -exec javac -cp "$PLATFORM:$OUT/classes" -d "$OUT/classes" {} + 2>&1 | grep -v "warning:" | tail -10

# 5. Create DEX
echo "=== Creating DEX ==="
$BUILD_TOOLS/d8 --lib "$PLATFORM" --output "$OUT/dex" "$OUT/classes" 2>&1 | tail -10

# 6. Package APK
echo "=== Packaging APK ==="
$BUILD_TOOLS/aapt package -f \
  -M "$PROJECT/app/src/main/AndroidManifest.xml" \
  -S "$PROJECT/app/src/main/res" \
  -I "$PLATFORM" \
  -F "$OUT/alan-unsigned.apk" \
  "$OUT/dex/classes.dex" 2>&1 | tail -10

# 7. Sign APK
if [ ! -f "$PROJECT/debug.keystore" ]; then
  keytool -genkey -v -keystore "$PROJECT/debug.keystore" \
    -alias androiddebugkey -keyalg RSA -keysize 2048 \
    -validity 10000 -storepass android -keypass android \
    -dname "CN=Android Debug,O=Android,C=US" 2>/dev/null
fi

$BUILD_TOOLS/apksigner sign \
  --ks "$PROJECT/debug.keystore" \
  --ks-pass pass:android \
  --key-pass pass:android \
  --out "$OUT/alan-debug.apk" \
  "$OUT/alan-unsigned.apk" 2>&1

# Verify
echo "=== Verifying APK ==="
$BUILD_TOOLS/apksigner verify "$OUT/alan-debug.apk" && echo "APK verified successfully"
ls -la "$OUT/alan-debug.apk"

echo "=== Build complete! APK at $OUT/alan-debug.apk ==="
