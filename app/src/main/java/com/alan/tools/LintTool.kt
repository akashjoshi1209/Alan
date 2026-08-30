package com.alan.tools

import com.alan.agent.Tool
import com.alan.agent.ToolResult
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.withContext
import org.json.JSONObject
import java.io.File

class LintTool : Tool {
    override val name = "lint"
    override val description = "Run linters: detect language and execute appropriate linter"
    
    override val parametersSchema = JSONObject().apply {
        put("type", "object")
        put("properties", JSONObject().apply {
            put("action", JSONObject().apply {
                put("type", "string")
                put("enum", JSONArray().put("check").put("fix"))
                put("default", "check")
                put("description", "Lint action: check or fix")
            })
            put("path", JSONObject().apply {
                put("type", "string")
                put("description", "Specific file or directory to lint")
            })
            put("cwd", JSONObject().apply {
                put("type", "string")
                put("description", "Working directory")
            })
        })
    }
    
    override suspend fun execute(args: JSONObject): ToolResult = withContext(Dispatchers.IO) {
        val action = args.optString("action", "check")
        val path = args.optString("path", "")
        val cwd = args.optString("cwd", "/data/data/com.alan/files/projects")
        
        return@withContext try {
            val (detectedLinter, commands) = detectLinterAndCommands(cwd, path, action)
            
            if (commands.isEmpty()) {
                return@withContext ToolResult(false, "", "No linter detected for this project")
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
                success = exitCode == 0 || action == "check", // check can return non-zero for warnings
                output = output.toString(),
                error = if (exitCode != 0 && action == "fix") "Exit code: $exitCode" else null,
                exitCode = exitCode
            )
        } catch (e: Exception) {
            ToolResult(false, "", "Lint error: ${e.message}", exitCode = -1)
        }
    }
    
    private fun detectLinterAndCommands(
        cwd: String,
        path: String,
        action: String
    ): Pair<String, List<String>> {
        val projectDir = File(cwd)
        
        val linters = listOf(
            // JavaScript/TypeScript
            LinterDetector("eslint", File(projectDir, "package.json"), 
                { listOf("npx", "eslint", if (action == "fix") "--fix" else "", path.ifBlank { "." }).filter { it.isNotBlank() } },
                { listOf("npx", "eslint", "--version") }
            ),
            LinterDetector("prettier", File(projectDir, "package.json"),
                { listOf("npx", "prettier", if (action == "fix") "--write" else "--check", path.ifBlank { "." }).filter { it.isNotBlank() } },
                { listOf("npx", "prettier", "--version") }
            ),
            // Python
            LinterDetector("ruff", File(projectDir, "pyproject.toml"),
                { listOf("ruff", "check", if (action == "fix") "--fix" else "", path.ifBlank { "." }).filter { it.isNotBlank() } },
                { listOf("ruff", "--version") }
            ),
            LinterDetector("black", File(projectDir, "pyproject.toml"),
                { listOf("black", if (action == "fix") "" else "--check", path.ifBlank { "." }).filter { it.isNotBlank() } },
                { listOf("black", "--version") }
            ),
            // Rust
            LinterDetector("clippy", File(projectDir, "Cargo.toml"),
                { listOf("cargo", "clippy", "--", "-D", "warnings") },
                { listOf("cargo", "clippy", "--version") }
            ),
            // Go
            LinterDetector("golangci-lint", File(projectDir, "go.mod"),
                { listOf("golangci-lint", "run", if (action == "fix") "--fix" else "") },
                { listOf("golangci-lint", "--version") }
            ),
            // Java/Kotlin
            LinterDetector("ktlint", File(projectDir, "build.gradle.kts"),
                { listOf("./gradlew", "ktlintCheck", if (action == "fix") "ktlintFormat" else "") },
                { listOf("./gradlew", "ktlintCheck", "--version") }
            ),
            // Shell
            LinterDetector("shellcheck", File(projectDir, ".shellcheckrc"),
                { listOf("shellcheck", path.ifBlank { "**/*.sh" }) },
                { listOf("shellcheck", "--version") }
            )
        )
        
        for (linter in linters) {
            if (linter.configFile.exists()) {
                // Verify linter is available
                try {
                    ProcessBuilder(*linter.versionCheck().toTypedArray()).directory(File(cwd)).start().waitFor()
                    return linter.name to linter.commands(action, path)
                } catch (e: Exception) {
                    // Linter not installed, try next
                }
            }
        
        return "unknown" to emptyList()
    }
    
    data class LinterDetector(
        val name: String,
        val configFile: File,
        val commands: (String, String) -> List<String>,
        val versionCheck: () -> List<String>
    )
}