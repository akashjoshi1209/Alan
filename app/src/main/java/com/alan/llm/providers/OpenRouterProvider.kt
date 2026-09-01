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
import okhttp3.Headers.Companion.toHeaders
import okhttp3.Request

class OpenRouterProvider(
    apiKey: String,
    model: String = "anthropic/claude-3.5-sonnet"
) : BaseHttpProvider(
    apiKey = apiKey,
    model = model,
    baseUrl = "https://openrouter.ai/api/v1"
), LLMProvider {
    
    override val name = "OpenRouter"
    override val baseUrl = "https://openrouter.ai/api/v1"
    override val supportsStreaming = true
    override val supportsTools = true
    override val defaultModel = "anthropic/claude-3.5-sonnet"
    override val maxContextTokens = 128000
    
    override fun buildHeaders(): Map<String, String> = mapOf(
        "Content-Type" to "application/json",
        "Authorization" to "Bearer $apiKey",
        "HTTP-Referer" to "https://alan.app",
        "X-Title" to "Alan"
    )
    
    override suspend fun complete(
        systemPrompt: String,
        userMessage: String,
        temperature: Float,
        maxTokens: Int
    ): Result<String> = withContext(Dispatchers.IO) {
        runCatching {
            val messages = JSONArray().apply {
                if (systemPrompt.isNotBlank()) {
                    put(JSONObject().apply {
                        put("role", "system")
                        put("content", systemPrompt)
                    })
                }
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
                put("stream", false)
            }
            
            val request = Request.Builder()
                .url("$baseUrl/chat/completions")
                .headers(buildHeaders().toHeaders())
                .post(buildRequestBody(requestBody))
                .build()
            
            executeRequest(request).getOrThrow().let { response ->
                val json = JSONObject(response)
                json.getJSONArray("choices")
                    .getJSONObject(0)
                    .getJSONObject("message")
                    .getString("content")
            }
        }
    }
    
    override suspend fun completeWithTools(
        systemPrompt: String,
        userMessage: String,
        tools: List<ToolDefinition>,
        temperature: Float,
        maxTokens: Int
    ): Result<ToolCallResult> = withContext(Dispatchers.IO) {
        runCatching {
            val messages = JSONArray().apply {
                if (systemPrompt.isNotBlank()) {
                    put(JSONObject().apply {
                        put("role", "system")
                        put("content", systemPrompt)
                    })
                }
                put(JSONObject().apply {
                    put("role", "user")
                    put("content", userMessage)
                })
            }
            
            val toolSchemas = tools.map { tool ->
                JSONObject().apply {
                    put("type", "function")
                    put("function", JSONObject().apply {
                        put("name", tool.name)
                        put("description", tool.description)
                        put("parameters", tool.parameters)
                    })
                }
            }.toList()
            
            val requestBody = JSONObject().apply {
                put("model", model)
                put("messages", messages)
                put("temperature", temperature)
                put("max_tokens", maxTokens)
                put("tools", JSONArray(toolSchemas))
                put("tool_choice", "auto")
                put("stream", false)
            }
            
            val request = Request.Builder()
                .url("$baseUrl/chat/completions")
                .headers(buildHeaders().toHeaders())
                .post(buildRequestBody(requestBody))
                .build()
            
            executeRequest(request).getOrThrow().let { response ->
                val json = JSONObject(response)
                val message = json.getJSONArray("choices").getJSONObject(0).getJSONObject("message")
                
                if (message.has("tool_calls")) {
                    val toolCalls = mutableListOf<ToolCall>()
                    val toolCallsArray = message.getJSONArray("tool_calls")
                    for (i in 0 until toolCallsArray.length()) {
                        val tc = toolCallsArray.getJSONObject(i)
                        toolCalls.add(ToolCall(
                            id = tc.getString("id"),
                            name = tc.getJSONObject("function").getString("name"),
                            arguments = tc.getJSONObject("function").getJSONObject("arguments")
                        ))
                    }
                    ToolCallResult.ToolCalls(toolCalls)
                } else {
                    ToolCallResult.Text(message.getString("content"))
                }
            }
        }
    }
    
    override suspend fun streamComplete(
        systemPrompt: String,
        userMessage: String,
        onChunk: (String) -> Unit,
        temperature: Float,
        maxTokens: Int
    ): Result<String> = withContext(Dispatchers.IO) {
        runCatching {
            val messages = JSONArray().apply {
                if (systemPrompt.isNotBlank()) {
                    put(JSONObject().apply {
                        put("role", "system")
                        put("content", systemPrompt)
                    })
                }
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
                put("stream", true)
            }
            
            val request = Request.Builder()
                .url("$baseUrl/chat/completions")
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
                        if (data == "[DONE]") break
                        if (data.isEmpty()) continue
                        try {
                            val json = JSONObject(data)
                            val delta = json.getJSONArray("choices")
                                .getJSONObject(0)
                                .getJSONObject("delta")
                            if (delta.has("content") && !delta.isNull("content")) {
                                val content = delta.getString("content")
                                fullContent += content
                                onChunk(content)
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
                .url("$baseUrl/chat/completions")
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