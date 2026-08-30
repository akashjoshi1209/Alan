package com.alan.tools

import com.alan.agent.Tool
import com.alan.agent.ToolResult
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.withContext
import org.json.JSONObject
import java.io.File

class GitTool : Tool {
    override val name = "git"
    override val description = "Git operations: status, diff, commit, branch, log, etc."
    
    override val parametersSchema = JSONObject().apply {
        put("type", "object")
        put("properties", JSONObject().apply {
            put("action", JSONObject().apply {
                put("type", "string")
                put("enum", JSONArray().put("status").put("diff").put("log").put("branch").put("add").put("commit").put("push").put("pull").put("checkout").put("merge").put("stash"))
                put("description", "Git action to perform")
            })
            put("args", JSONObject().apply {
                put("type", "array")
                put("items", JSONObject().put("type", "string"))
                put("description", "Additional arguments")
            })
            put("cwd", JSONObject().apply {
                put("type", "string")
                put("description", "Working directory")
            })
        })
        put("required", JSONArray().put("action"))
    }
    
    override suspend fun execute(args: JSONObject): ToolResult = withContext(Dispatchers.IO) {
        val action = args.optString("action", "status")
        val cwd = args.optString("cwd", "/data/data/com.alan/files/projects")
        val extraArgs = args.optJSONArray("args")?.let { 
            (0 until it.length()).map { it.getString(it) } 
        } ?: emptyList()
        
        return@withContext try {
            val commands = when (action) {
                "status" -> listOf("git", "status", "--short")
                "diff" -> listOf("git", "diff") + extraArgs
                "log" -> listOf("git", "log", "--oneline", "-20") + extraArgs
                "branch" -> listOf("git", "branch", "-a")
                "add" -> listOf("git", "add") + extraArgs
                "commit" -> listOf("git", "commit", "-m", extraArgs.firstOrNull() ?: "Update") + extraArgs.drop(1)
                "push" -> listOf("git", "push") + extraArgs
                "pull" -> listOf("git", "pull") + extraArgs
                "checkout" -> listOf("git", "checkout") + extraArgs
                "merge" -> listOf("git", "merge") + extraArgs
                "stash" -> listOf("git", "stash") + extraArgs
                else -> listOf("git", action) + extraArgs
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
            ToolResult(false, "", "Git error: ${e.message}", exitCode = -1)
        }
    }
}