# Building Alan APK

## Option 1: GitHub Actions (Recommended)

This is the **easiest and most reliable** way to build Alan.

### Steps:

1. **Create a GitHub repository:**
   ```bash
   # Go to https://github.com/new and create a new repo named "alan"
   ```

2. **Push the code:**
   ```bash
   cd /tmp/alan
   git init
   git add .
   git commit -m "feat: Alan - Personal Android AI Coding Agent"
   git branch -M main
   git remote add origin https://github.com/YOUR_USERNAME/alan.git
   git push -u origin main
   ```

3. **GitHub Actions will automatically:**
   - Build the debug APK on every push
   - Run unit tests
   - Upload the APK as an artifact (available for 7 days)
   - Build release APK on tags

4. **Download the APK:**
   - Go to your repo → Actions → Latest workflow run
   - Click "alan-debug-apk" artifact to download
   - Install on device: `adb install alan-debug.apk`

---

## Option 2: Local Build (x86_64 machine)

If you have access to an x86_64 Linux/macOS machine:

```bash
# Clone the repo
git clone https://github.com/YOUR_USERNAME/alan.git
cd alan

# Build
./gradlew assembleDebug

# APK location
ls app/build/outputs/apk/debug/
```

---

## Option 3: Docker (x86_64 host)

```bash
# Build Docker image (on x86_64 host)
docker build --platform=linux/amd64 -f Dockerfile.build -t alan-builder .

# Run and extract APK
docker run --rm -v $(pwd)/output:/app/app/build/outputs alan-builder
# APK will be in output/
```

---

## Required GitHub Secrets (for Release builds)

Go to: Settings → Secrets and variables → Actions → New repository secret

| Secret Name | Description |
|-------------|-------------|
| `SIGNING_KEY_ALIAS` | Key alias from your keystore |
| `SIGNING_KEY_PASSWORD` | Key password |
| `SIGNING_STORE_PASSWORD` | Keystore password |

For debug builds, no secrets needed!

---

## Project Structure

```
/tmp/alan/
├── app/
│   ├── src/main/java/com/alan/
│   │   ├── agent/           # AlanCore, ToolRegistry
│   │   ├── bridge/          # UnixSocketServer (Termux)
│   │   ├── llm/             # 9 LLM providers
│   │   ├── local/           # ModelManager (llama.cpp)
│   │   ├── mcp/             # MCP Client/Manager
│   │   ├── memory/          # Room DB (7 entities)
│   │   ├── tools/           # File, Shell, Git, Test, Lint
│   │   ├── skills/          # CodeSkillEngine
│   │   ├── ui/              # Compose UI (Chat, Settings)
│   │   └── security/        # EncryptedPrefs
│   └── build.gradle         # KAPT for Room
├── .github/workflows/build.yml  # CI/CD pipeline
├── Dockerfile.build         # x86_64 build image
├── build.gradle
├── settings.gradle
└── gradle.properties
```

---

## Features Included

| Feature | Status |
|---------|--------|
| 9 LLM Providers | ✅ |
| Local Models (llama.cpp) | ✅ |
| Code Memory (Room + 7 entities) | ✅ |
| Code Tools (File, Shell, Git, Test, Lint) | ✅ |
| Termux Bridge (Unix socket) | ✅ |
| MCP Support | ✅ |
| Code Skills | ✅ |
| VOID Theme Chat UI | ✅ |
| Encrypted API Keys | ✅ |

---

## Quick Start After Install

1. Open Alan app
2. Go to Settings → AI Provider
3. Select provider (Groq recommended - free & fast)
4. Enter API key
5. Optionally enable local model
6. Grant storage permission
7. Start coding!
