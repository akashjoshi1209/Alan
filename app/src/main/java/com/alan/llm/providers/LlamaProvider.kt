package com.alan.llm.providers

import com.alan.llm.LLMProvider
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.withContext
import okhttp3.MediaType.Companion.toMediaType
import okhttp3.OkHttpClient
import okhttp3.Request
import okhttp3.RequestBody.Companion.toRequestBody
import org.json.JSONObject
import java.util.concurrent.TimeUnit

class LlamaProvider(
    private val modelPath: String,
    private val modelName: String = "phi3-mini"
) : LLMProvider {
    
    override val name = "Local Llama"
    override val baseUrl = "http://localhost:8080"
    override val supportsStreaming = false
    override val defaultModel = "phi3-mini"
    override val maxContextTokens = 32768
    
    private val client = OkHttpClient.Builder()
        .connectTimeout(30, TimeUnit.SECONDS)
        .readTimeout(120, TimeUnit.SECONDS)
        .build()
    
    private val mediaType = "application/json".toMediaType()
    
    override suspend fun complete(
        systemPrompt: String,
        userMessage: String,
        temperature: Float = 0.1f,
        maxTokens: Int = 2048
    ): Result<String> = withContext(Dispatchers.IO) {
        runCatching {
            val requestBody = JSONObject().apply {
                put("model", modelName)
                put("messages", org.json.JSONArray().apply {
                    put(JSONObject().apply {
                        put("role", "system")
                        put("content", systemPrompt)
                    })
                    put(JSONObject().apply {
                        put("role", "user")
                        put("content", userMessage)
                    })
                })
                put("stream", false)
                put("max_tokens", maxTokens)
                put("temperature", temperature)
            }.toString()
            
            val request = Request.Builder()
                .url("$baseUrl/v1/chat/completions")
                .addHeader("Content-Type", "application/json")
                .post(requestBody.toRequestBody(mediaType))
                .build()
            
            client.newCall(request).execute().use { response ->
                val body = response.body?.string() ?: throw Exception("Empty response")
                val json = JSONObject(body)
                
                if (json.has("choices")) {
                    json.getJSONArray("choices")
                        .getJSONObject(0)
                        .getJSONObject("message")
                        .getString("content")
                } else if (json.has("error")) {
                    throw Exception(json.getJSONObject("error").getString("message"))
                } else {
                    throw Exception("Unknown response: $body")
                }
            }
        }
    }
    
    override suspend fun testConnection(): Result<Long> = withContext(Dispatchers.IO) {
        runCatching {
            val startTime = System.currentTimeMillis()
            
            val requestBody = JSONObject().apply {
                put("model", modelName)
                put("messages", org.json.JSONArray().put(
                    JSONObject().put("role", "user").put("content", "ping")
                ))
                put("max_tokens", 1)
            }.toString()
            
            val request = Request.Builder()
                .url("$baseUrl/v1/chat/completions")
                .post(requestBody.toRequestBody(mediaType))
                .build()
            
            client.newCall(request).execute().use { response ->
                if (!response.isSuccessful && response.code != 200) {
                    throw Exception("HTTP ${response.code}")
                }
            }
            
            System.currentTimeMillis() - startTime
        }
    }
    
    companion object {
        const val DEFAULT_HOST = "localhost"
        const val DEFAULT_PORT = 8080
    }
}