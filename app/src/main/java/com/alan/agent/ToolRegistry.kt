package com.alan.agent

import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow

sealed class AlanState {
    object Idle : AlanState()
    data class Running(val step: String) : AlanState()
    data class Done(val result: String) : AlanState()
    data class Error(val message: String) : AlanState()
}

data class ToolDefinition(
    val name: String,
    val description: String,
    val parameters: org.json.JSONObject
)

data class ToolResult(
    val success: Boolean,
    val output: String,
    val error: String? = null,
    val artifacts: List<String> = emptyList()
)

interface Tool {
    val name: String
    val description: String
    val parametersSchema: org.json.JSONObject
    
    suspend fun execute(args: org.json.JSONObject): ToolResult
}

class ToolRegistry {
    private val tools = mutableMapOf<String, Tool>()
    
    fun register(tool: Tool) {
        tools[tool.name] = tool
    }
    
    fun unregister(name: String) {
        tools.remove(name)
    }
    
    fun getTool(name: String): Tool? = tools[name]
    
    fun getAllTools(): List<Tool> = tools.values.toList()
    
    fun getToolDefinitions(): List<ToolDefinition> = tools.values.map { tool ->
        ToolDefinition(tool.name, tool.description, tool.parametersSchema)
    }
}