package com.alan.tools

import com.alan.agent.Tool
import com.alan.agent.ToolResult
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.withContext
import org.json.JSONObject

class ShellTool : Tool {
    override val name = "shell"
    override val description = "Execute shell commands in the project environment"
    
    override val parametersSchema = JSONObject().apply {
        put("type", "object")
        put("properties", JSONObject().apply {
            put("command", JSONObject().apply {
                put("type", "string")
                put("description", "Command to execute")
            })
            put("cwd", JSONObject().apply {
                put("type", "string")
                put("description", "Working directory (optional)")
            })
            put("timeout", JSONObject().apply {
                put("type", "integer")
                put("default", 60000)
                put("description", "Timeout in milliseconds")
            })
            put("env", JSONObject().apply {
                put("type", "object")
                put("description", "Environment variables")
            })
        })
        put("required", JSONArray().put("command"))
    }
    
    override suspend fun execute(args: JSONObject): ToolResult = withContext(Dispatchers.IO) {
        val command = args.optString("command", "")
        val cwd = args.optString("cwd", "/data/data/com.alan/files/projects")
        val timeout = args.optLong("timeout", 60000)
        
        if (command.isBlank()) {
            return@withContext ToolResult(false, "", "Command is required")
        }
        
        val env = mutableMapOf<String, String>()
        args.optJSONObject("env")?.let { envJson ->
            envJson.keys().forEachRemaining { key ->
                env[key] = envJson.optString(key, "")
            }
        }
        
        return@withContext try {
            val processBuilder = ProcessBuilder("sh", "-c", command).apply {
                directory(File(cwd))
                environment().putAll(env)
                redirectErrorStream(true)
            }
            
            val process = processBuilder.start()
            
            val output = StringBuilder()
            val reader = java.io.BufferedReader(java.io.InputStreamReader(process.inputStream))
            val readJob = kotlinx.coroutines.CoroutineScope(Dispatchers.IO).launch {
                reader.use {
                    var line: String?
                    while (true) {
                        line = reader.readLine()
                        if (line == null) break
                        output.append(line).append('\n')
                    }
                }
            }
            
            val finished = process.waitFor(timeout, java.util.concurrent.TimeUnit.MILLISECONDS)
            
            if (!finished) {
                process.destroyForcibly()
                readJob.cancel()
                return@withContext ToolResult(false, output.toString(), "Command timed out after ${timeout}ms", exitCode = -1)
            }
            
            val exitCode = process.exitValue()
            readJob.join()
            
            ToolResult(
                success = exitCode == 0,
                output = output.toString(),
                error = if (exitCode != 0) "Exit code: $exitCode" else null,
                exitCode = exitCode
            )
        } catch (e: Exception) {
            ToolResult(false, "", "Shell error: ${e.message}", exitCode = -1)
        }
    }
}