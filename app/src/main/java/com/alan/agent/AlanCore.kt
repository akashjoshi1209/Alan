package com.alan.agent

import android.content.Context
import com.alan.bridge.UnixSocketServer
import com.alan.llm.UniversalAdapter
import com.alan.memory.MemoryRepository
import com.alan.tools.FileTool
import com.alan.tools.GitTool
import com.alan.tools.LintTool
import com.alan.tools.ShellTool
import com.alan.tools.TestTool
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.launch
import kotlinx.coroutines.sync.Mutex
import kotlinx.coroutines.sync.withLock

class AlanCore(private val context: Context) {
    
    private val universalAdapter = UniversalAdapter(context)
    private val memoryRepo = MemoryRepository(context)
    private val toolRegistry = ToolRegistry()
    private val unixSocketServer = UnixSocketServer(context, this, memoryRepo)
    
    private var currentProjectId: Long = -1
    private val scope = CoroutineScope(Dispatchers.IO)
    private val taskMutex = Mutex()
    
    private val _state = MutableStateFlow<AlanState>(AlanState.Idle)
    val state: StateFlow<AlanState> = _state
    
    private val systemPrompt = """
You are Alan — a coding AI agent that helps users write, understand, and maintain code.
You have access to tools for file operations, shell commands, git, testing, and linting.

AVAILABLE TOOLS:
{TOOLS}

RULES:
- Always start by understanding the task and the project structure
- Use tools to explore the codebase before making changes
- Write clean, idiomatic code following project conventions
- Run tests after making changes to verify correctness
- Use git to track changes
- Keep responses concise and actionable
- If you need clarification, ask the user

TOOL USAGE FORMAT:
When you need to use a tool, respond with a JSON object:
{
  "tool": "tool_name",
  "args": { "key": "value" }
}

You can chain multiple tool calls. The system will execute them sequentially and return results.
After receiving tool results, continue your reasoning or provide the final answer.
""".trimIndent()
    
    init {
        // Register built-in tools
        toolRegistry.register(FileTool())
        toolRegistry.register(ShellTool())
        toolRegistry.register(GitTool())
        toolRegistry.register(TestTool())
        toolRegistry.register(LintTool())
        
        // Start Unix socket server for Termux bridge
        unixSocketServer.start()
    }
    
    fun setProject(projectId: Long) {
        currentProjectId = projectId
    }
    
    fun getCurrentProjectId(): Long = currentProjectId
    
    fun getAvailableTools(): List<Tool> = toolRegistry.getAllTools()
    
    fun executeTask(userQuery: String) {
        scope.launch {
            taskMutex.withLock {
                try {
                    _state.value = AlanState.Running("analyzing task...")
                    
                    val projectId = if (currentProjectId > 0) currentProjectId else null
                    val memoryContext = memoryRepo.buildMemoryContext(projectId, userQuery)
                    
                    val toolsDesc = toolRegistry.getToolDefinitions().joinToString("\n") { tool ->
                        "- ${tool.name}: ${tool.description}"
                    }
                    
                    val fullSystem = systemPrompt.replace("{TOOLS}", toolsDesc) + 
                        "\n\nPROJECT CONTEXT:\n$memoryContext"
                    
                    _state.value = AlanState.Running("thinking...")
                    
                    val startTime = System.currentTimeMillis()
                    
                    val result = universalAdapter.complete(fullSystem, userQuery)
                    result.fold(
                        onSuccess = { rawResponse ->
                            val latency = System.currentTimeMillis() - startTime
                            processResponse(rawResponse, userQuery, projectId, latency)
                        },
                        onFailure = { error ->
                            val msg = when {
                                error.message?.contains("401") == true -> "Invalid API key"
                                error.message?.contains("429") == true -> "Rate limited — wait a moment"
                                error.message?.contains("timeout") == true -> "Request timed out"
                                else -> error.message ?: "Unknown error"
                            }
                            _state.value = AlanState.Error(msg)
                            logTask(userQuery, "failed: $msg", "", 0)
                        }
                    )
                } catch (e: Exception) {
                    _state.value = AlanState.Error(e.message ?: "Unknown error")
                    logTask(userQuery, "failed: ${e.message}", "", 0)
                }
            }
        }
    }
    
    private suspend fun processResponse(rawResponse: String, userQuery: String, projectId: Long?, latency: Long) {
        // Parse response - could be tool calls or final answer
        val lines = rawResponse.lines().toList()
        var finalAnswer = ""
        var toolCalls = mutableListOf<Pair<String, org.json.JSONObject>>()
        
        for (line in lines) {
            val trimmed = line.trim()
            if (trimmed.startsWith("{") && trimmed.endsWith("}")) {
                try {
                    val json = org.json.JSONObject(trimmed)
                    if (json.has("tool") && json.has("args")) {
                        toolCalls.add(json.getString("tool") to json.getJSONObject("args"))
                    } else {
                        finalAnswer += trimmed + "\n"
                    }
                } catch (e: Exception) {
                    finalAnswer += trimmed + "\n"
                }
            } else {
                finalAnswer += trimmed + "\n"
            }
        }
        
        // Execute tool calls
        var toolResults = ""
        for ((toolName, args) in toolCalls) {
            _state.value = AlanState.Running("executing $toolName...")
            val tool = toolRegistry.getTool(toolName)
            if (tool != null) {
                val result = tool.execute(args)
                toolResults += "\n[${toolName} result]: ${if (result.success) result.output else "ERROR: ${result.error}"}"
                
                if (!result.success) {
                    toolResults += "\nTool failed, stopping execution."
                    break
                }
            } else {
                toolResults += "\n[Error]: Unknown tool: $toolName"
            }
        }
        
        // If we had tool calls, send results back to LLM for final answer
        if (toolCalls.isNotEmpty()) {
            val followUpPrompt = """
Tool execution results:
$toolResults

Based on these results, provide a final answer to the user's original query: "$userQuery"
"""
            val followUpResult = universalAdapter.complete(systemPrompt.replace("{TOOLS}", toolRegistry.getToolDefinitions().joinToString("\n") { "- ${it.name}: ${it.description}" }), followUpPrompt)
            followUpResult.fold(
                onSuccess = { response ->
                    finalAnswer = response
                    _state.value = AlanState.Done(finalAnswer.trim())
                    logTask(userQuery, finalAnswer.trim(), universalAdapter.getProviderName(), latency)
                },
                onFailure = { error ->
                    _state.value = AlanState.Done(finalAnswer.trim() + "\n\n(Note: follow-up failed: ${error.message})")
                    logTask(userQuery, finalAnswer.trim(), universalAdapter.getProviderName(), latency)
                }
            )
        } else {
            _state.value = AlanState.Done(finalAnswer.trim())
            logTask(userQuery, finalAnswer.trim(), universalAdapter.getProviderName(), latency)
        }
    }
    
    private suspend fun logTask(query: String, result: String, provider: String, latency: Long) {
        if (currentProjectId > 0) {
            memoryRepo.logTask(
                projectId = currentProjectId,
                userQuery = query,
                result = result,
                provider = provider,
                latencyMs = latency
            )
        }
    }
    
    suspend fun testConnection(): Result<Long> = universalAdapter.testConnection()
    
    fun getCurrentProviderName(): String = universalAdapter.getProviderName()
    
    fun getStateFlow(): StateFlow<AlanState> = state

    fun dismissError() {
        _state.value = AlanState.Idle
    }
    
    fun shutdown() {
        unixSocketServer.stop()
        scope.coroutineContext[kotlinx.coroutines.Job]?.cancel()
    }
}