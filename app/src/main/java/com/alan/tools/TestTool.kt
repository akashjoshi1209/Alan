package com.alan.tools

import com.alan.agent.Tool
import com.alan.agent.ToolResult
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.withContext
import org.json.JSONArray
import org.json.JSONObject
import java.io.File

class TestTool : Tool {
    override val name = "test"
    override val description = "Run tests: detect test framework and execute tests"
    
    override val parametersSchema = JSONObject().apply {
        put("type", "object")
        put("properties", JSONObject().apply {
            put("action", JSONObject().apply {
                put("type", "string")
                put("enum", JSONArray().put("run").put("list").put("coverage"))
                put("description", "Test action")
            })
            put("framework", JSONObject().apply {
                put("type", "string")
                put("enum", JSONArray().put("junit").put("pytest").put("jest").put("cargo").put("gradle").put("maven").put("auto"))
                put("default", "auto")
                put("description", "Test framework")
            })
            put("path", JSONObject().apply {
                put("type", "string")
                put("description", "Specific test path or pattern")
            })
            put("cwd", JSONObject().apply {
                put("type", "string")
                put("description", "Working directory")
            })
        })
    }
    
    override suspend fun execute(args: JSONObject): ToolResult = withContext(Dispatchers.IO) {
        val action = args.optString("action", "run")
        val framework = args.optString("framework", "auto")
        val path = args.optString("path", "")
        val cwd = args.optString("cwd", "/data/data/com.alan/files/projects")
        
        return@withContext try {
            val (detectedFramework, commands) = detectFrameworkAndCommands(framework, cwd, path, action)
            
            if (commands.isEmpty()) {
                return@withContext ToolResult(false, "", "No test framework detected")
            }
            
            val processBuilder = ProcessBuilder(*commands.toTypedArray()).apply {
                directory(File(cwd))
                redirectErrorStream(true)
            }
            
            val process = processBuilder.start()
            val output = StringBuilder()
            val reader = java.io.BufferedReader(java.io.InputStreamReader(process.inputStream))
            
            reader.use {
                var line: String?
                while (true) {
                    line = reader.readLine()
                    if (line == null) break
                    output.append(line).append('\n')
                }
            }
            
            val exitCode = process.waitFor()
            
            ToolResult(
                success = exitCode == 0,
                output = output.toString(),
                error = if (exitCode != 0) "Exit code: $exitCode" else null,
                exitCode = exitCode
            )
        } catch (e: Exception) {
            ToolResult(false, "", "Test error: ${e.message}", exitCode = -1)
        }
    }
    
    private fun detectFrameworkAndCommands(
        framework: String,
        cwd: String,
        path: String,
        action: String
    ): Pair<String, List<String>> {
        val projectDir = File(cwd)
        
        // Auto-detect
        val detected = if (framework != "auto") framework else {
            when {
                File(projectDir, "package.json").exists() -> "jest"
                File(projectDir, "pytest.ini").exists() || File(projectDir, "pyproject.toml").exists() -> "pytest"
                File(projectDir, "build.gradle.kts").exists() || File(projectDir, "build.gradle").exists() -> "gradle"
                File(projectDir, "pom.xml").exists() -> "maven"
                File(projectDir, "Cargo.toml").exists() -> "cargo"
                File(projectDir, "go.mod").exists() -> "go"
                else -> "unknown"
            }
        }
        
        val commands = when (detected) {
            "pytest" -> when (action) {
                "run" -> listOf("python", "-m", "pytest", path.ifBlank { "" }, "-v", "--tb=short").filter { it.isNotBlank() }
                "list" -> listOf("python", "-m", "pytest", "--collect-only", "-q")
                "coverage" -> listOf("python", "-m", "pytest", "--cov", "--cov-report=term-missing", path.ifBlank { "" }).filter { it.isNotBlank() }
                else -> listOf("python", "-m", "pytest", path.ifBlank { "" }).filter { it.isNotBlank() }
            }
            "jest" -> when (action) {
                "run" -> listOf("npx", "jest", path.ifBlank { "" }, "--verbose").filter { it.isNotBlank() }
                "list" -> listOf("npx", "jest", "--listTests")
                "coverage" -> listOf("npx", "jest", "--coverage", path.ifBlank { "" }).filter { it.isNotBlank() }
                else -> listOf("npx", "jest", path.ifBlank { "" }).filter { it.isNotBlank() }
            }
            "gradle" -> when (action) {
                "run" -> listOf("./gradlew", "test", path.ifBlank { "--tests" }.let { if (it.isNotBlank()) "$it $path" else "" }).filter { it.isNotBlank() }
                "list" -> listOf("./gradlew", "test", "--dry-run")
                "coverage" -> listOf("./gradlew", "jacocoTestReport")
                else -> listOf("./gradlew", "test").filter { it.isNotBlank() }
            }
            "maven" -> when (action) {
                "run" -> listOf("mvn", "test", path.ifBlank { "" }.let { if (it.isNotBlank()) "-Dtest=$it" else "" }).filter { it.isNotBlank() }
                "list" -> listOf("mvn", "test", "-Dtest=*")
                "coverage" -> listOf("mvn", "jacoco:report")
                else -> listOf("mvn", "test").filter { it.isNotBlank() }
            }
            "cargo" -> when (action) {
                "run" -> listOf("cargo", "test", path.ifBlank { "" }).filter { it.isNotBlank() }
                "list" -> listOf("cargo", "test", "--no-run", "--list")
                "coverage" -> listOf("cargo", "llvm-cov", "--html")
                else -> listOf("cargo", "test").filter { it.isNotBlank() }
            }
            "go" -> when (action) {
                "run" -> listOf("go", "test", "-v", path.ifBlank { "./..." })
                "list" -> listOf("go", "test", "-list", ".*", path.ifBlank { "./..." })
                "coverage" -> listOf("go", "test", "-coverprofile=coverage.out", path.ifBlank { "./..." })
                else -> listOf("go", "test", path.ifBlank { "./..." })
            }
            else -> emptyList()
        }
        
        return detected to commands
    }
}