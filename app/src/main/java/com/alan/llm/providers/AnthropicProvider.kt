package com.alan.llm.providers

import com.alan.llm.BaseHttpProvider
import com.alan.llm.LLMProvider
import com.alan.llm.ToolCall
import com.alan.llm.ToolCallResult
import com.alan.llm.ToolDefinition
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.withContext
import org.json.JSONArray
import org.json.JSONObject
import java.util.concurrent.TimeUnit

class AnthropicProvider(
    apiKey: String,
    model: String = "claude-3-5-sonnet-20241022"
) : BaseHttpProvider(
    apiKey = apiKey,
    model = model,
    baseUrl = "https://api.anthropic.com/v1"
), LLMProvider {
    
    override val name = "Anthropic Claude"
    override val baseUrl = "https://api.anthropic.com/v1"
    override val supportsStreaming = true
    override val supportsTools = true
    override val defaultModel = "claude-3-5-sonnet-20241022"
    override val maxContextTokens = 200000
    
    override fun buildHeaders(): Map<String, String> = mapOf(
        "Content-Type" to "application/json",
        "x-api-key" to apiKey,
        "anthropic-version" to "2023-06-01"
    )
    
    override suspend fun complete(
        systemPrompt: String,
        userMessage: String,
        temperature: Float = 0.1f,
        maxTokens: Int = 2048
    ): Result<String> = withContext(Dispatchers.IO) {
        runCatching {
            val messages = JSONArray().apply {
                put(JSONObject().apply {
                    put("role", "user")
                    put("content", userMessage)
                })
            }
            
            val requestBody = JSONObject().apply {
                put("model", model)
                put("messages", messages)
                put("temperature", temperature)
                put("max_tokens", maxTokens)
                if (systemPrompt.isNotBlank()) {
                    put("system", systemPrompt)
                }
                put("stream", false)
            }
            
            val request = Request.Builder()
                .url("$baseUrl/messages")
                .headers(buildHeaders().toHeaders())
                .post(buildRequestBody(requestBody))
                .build()
            
            executeRequest(request).getOrThrow().let { response ->
                val json = JSONObject(response)
                json.getJSONArray("content")
                    .getJSONObject(0)
                    .getString("text")
            }
        }
    }
    
    override suspend fun completeWithTools(
        systemPrompt: String,
        userMessage: String,
        tools: List<ToolDefinition>,
        temperature: Float = 0.1f,
        maxTokens: Int = 2048
    ): Result<ToolCallResult> = withContext(Dispatchers.IO) {
        runCatching {
            val messages = JSONArray().apply {
                put(JSONObject().apply {
                    put("role", "user")
                    put("content", userMessage)
                })
            }
            
            val toolSchemas = tools.map { tool ->
                JSONObject().apply {
                    put("name", tool.name)
                    put("description", tool.description)
                    put("input_schema", tool.parameters)
                }
            }.toList()
            
            val requestBody = JSONObject().apply {
                put("model", model)
                put("messages", messages)
                put("temperature", temperature)
                put("max_tokens", maxTokens)
                if (systemPrompt.isNotBlank()) {
                    put("system", systemPrompt)
                }
                put("tools", JSONArray(toolSchemas))
                put("stream", false)
            }
            
            val request = Request.Builder()
                .url("$baseUrl/messages")
                .headers(buildHeaders().toHeaders())
                .post(buildRequestBody(requestBody))
                .build()
            
            executeRequest(request).getOrThrow().let { response ->
                val json = JSONObject(response)
                val contentArray = json.getJSONArray("content")
                
                var textContent = ""
                val toolCalls = mutableListOf<ToolCall>()
                
                for (i in 0 until contentArray.length()) {
                    val block = contentArray.getJSONObject(i)
                    when (block.getString("type")) {
                        "text" -> textContent += block.getString("text")
                        "tool_use" -> {
                            toolCalls.add(ToolCall(
                                id = block.getString("id"),
                                name = block.getString("name"),
                                arguments = block.getJSONObject("input")
                            ))
                        }
                    }
                }
                
                when {
                    toolCalls.isNotEmpty() && textContent.isNotBlank() -> ToolCallResult.Mixed(textContent, toolCalls)
                    toolCalls.isNotEmpty() -> ToolCallResult.ToolCalls(toolCalls)
                    else -> ToolCallResult.Text(textContent)
                }
            }
        }
    }
    
    override suspend fun streamComplete(
        systemPrompt: String,
        userMessage: String,
        onChunk: (String) -> Unit,
        temperature: Float = 0.1f,
        maxTokens: Int = 2048
    ): Result<String> = withContext(Dispatchers.IO) {
        runCatching {
            val messages = JSONArray().apply {
                put(JSONObject().apply {
                    put("role", "user")
                    put("content", userMessage)
                })
            }
            
            val requestBody = JSONObject().apply {
                put("model", model)
                put("messages", messages)
                put("temperature", temperature)
                put("max_tokens", maxTokens)
                if (systemPrompt.isNotBlank()) {
                    put("system", systemPrompt)
                }
                put("stream", true)
            }
            
            val request = Request.Builder()
                .url("$baseUrl/messages")
                .headers(buildHeaders().toHeaders())
                .post(buildRequestBody(requestBody))
                .build()
            
            val response = client.newCall(request).execute()
            if (!response.isSuccessful) {
                throw Exception("HTTP ${response.code}: ${response.body?.string()}")
            }
            
            val body = response.body?.byteStream() ?: throw Exception("Empty response stream")
            val reader = java.io.BufferedReader(java.io.InputStreamReader(body))
            var fullContent = ""
            
            reader.use {
                var line: String?
                while (true) {
                    line = reader.readLine()
                    if (line == null) break
                    if (line.startsWith("data: ")) {
                        val data = line.substring(6).trim()
                        if (data.isEmpty() || data == "[DONE]") continue
                        try {
                            val json = JSONObject(data)
                            if (json.getString("type") == "content_block_delta") {
                                val delta = json.getJSONObject("delta")
                                if (delta.getString("type") == "text_delta") {
                                    val content = delta.getString("text")
                                    fullContent += content
                                    onChunk(content)
                                }
                            }
                        } catch (e: Exception) {
                            // Skip parse errors
                        }
                    }
                }
            }
            fullContent
        }
    }
    
    override suspend fun testConnection(): Result<Long> = withContext(Dispatchers.IO) {
        runCatching {
            val startTime = System.currentTimeMillis()
            val requestBody = JSONObject().apply {
                put("model", model)
                put("messages", JSONArray().put(JSONObject().apply {
                    put("role", "user")
                    put("content", "ping")
                }))
                put("max_tokens", 1)
            }
            val request = Request.Builder()
                .url("$baseUrl/messages")
                .headers(buildHeaders().toHeaders())
                .post(buildRequestBody(requestBody))
                .build()
            client.newCall(request).execute().use {
                if (!it.isSuccessful && it.code != 200) {
                    throw Exception("HTTP ${it.code}")
                }
            }
            System.currentTimeMillis() - startTime
        }
    }
}