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

class GroqProvider(
    apiKey: String,
    model: String = "llama-3.3-70b-versatile"
) : BaseHttpProvider(
    apiKey = apiKey,
    model = model,
    baseUrl = "https://api.groq.com/openai/v1"
), LLMProvider {
    
    override val name = "Groq"
    override val baseUrl = "https://api.groq.com/openai/v1"
    override val supportsStreaming = true
    override val supportsTools = false
    override val defaultModel = "llama-3.3-70b-versatile"
    override val maxContextTokens = 131072
    
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
                        val data = line.substring(6)
                        if (data == "[DONE]") break
                        try {
                            val json = JSONObject(data)
                            val delta = json.getJSONArray("choices")
                                .getJSONObject(0)
                                .getJSONObject("delta")
                            if (delta.has("content")) {
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