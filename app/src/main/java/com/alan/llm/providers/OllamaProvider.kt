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

class OllamaProvider(
    baseUrl: String = "http://localhost:11434",
    model: String = "llama3.3"
) : BaseHttpProvider(
    apiKey = "",
    model = model,
    baseUrl = baseUrl
), LLMProvider {
    
    override val name = "Ollama (Local)"
    override val supportsStreaming = true
    override val supportsTools = true
    override val defaultModel = "llama3.3"
    override val maxContextTokens = 32768
    
    override fun buildHeaders(): Map<String, String> = mapOf(
        "Content-Type" to "application/json"
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
                put("num_predict", maxTokens)
                put("stream", false)
            }
            
            val request = Request.Builder()
                .url("$baseUrl/api/chat")
                .headers(buildHeaders().toHeaders())
                .post(buildRequestBody(requestBody))
                .build()
            
            executeRequest(request).getOrThrow().let { response ->
                val json = JSONObject(response)
                json.getJSONObject("message").getString("content")
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
                    put("name", tool.name)
                    put("description", tool.description)
                    put("parameters", tool.parameters)
                }
            }.toList()
            
            val requestBody = JSONObject().apply {
                put("model", model)
                put("messages", messages)
                put("temperature", temperature)
                put("num_predict", maxTokens)
                put("tools", JSONArray(toolSchemas))
                put("stream", false)
            }
            
            val request = Request.Builder()
                .url("$baseUrl/api/chat")
                .headers(buildHeaders().toHeaders())
                .post(buildRequestBody(requestBody))
                .build()
            
            executeRequest(request).getOrThrow().let { response ->
                val json = JSONObject(response)
                val message = json.getJSONObject("message")
                
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
                put("num_predict", maxTokens)
                put("stream", true)
            }
            
            val request = Request.Builder()
                .url("$baseUrl/api/chat")
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
                    if (line.trim().isEmpty()) continue
                    try {
                        val json = JSONObject(line.trim())
                        if (json.has("message")) {
                            val message = json.getJSONObject("message")
                            if (message.has("content") && !message.isNull("content")) {
                                val content = message.getString("content")
                                fullContent += content
                                onChunk(content)
                            }
                        }
                        if (json.getBoolean("done")) break
                    } catch (e: Exception) {
                        // Skip parse errors
                    }
                }
            }
            fullContent
        }
    }
    
    override suspend fun testConnection(): Result<Long> = withContext(Dispatchers.IO) {
        runCatching {
            val startTime = System.currentTimeMillis()
            val request = Request.Builder()
                .url("$baseUrl/api/tags")
                .headers(buildHeaders().toHeaders())
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