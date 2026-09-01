package com.alan.llm.providers

import com.alan.llm.BaseHttpProvider
import com.alan.llm.LLMProvider
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.withContext
import org.json.JSONArray
import org.json.JSONObject
import java.util.concurrent.TimeUnit
import okhttp3.Request

class GeminiProvider(
    apiKey: String,
    model: String = "gemini-2.0-flash-exp"
) : BaseHttpProvider(
    apiKey = apiKey,
    model = model,
    baseUrl = "https://generativelanguage.googleapis.com/v1beta"
), LLMProvider {
    
    override val name = "Google Gemini"
    override val baseUrl = "https://generativelanguage.googleapis.com/v1beta"
    override val supportsStreaming = true
    override val supportsTools = false
    override val defaultModel = "gemini-2.0-flash-exp"
    override val maxContextTokens = 1048576
    
    override suspend fun complete(
        systemPrompt: String,
        userMessage: String,
        temperature: Float,
        maxTokens: Int
    ): Result<String> = withContext(Dispatchers.IO) {
        runCatching {
            val parts = mutableListOf<JSONObject>()
            if (systemPrompt.isNotBlank()) {
                parts.add(JSONObject().apply {
                    put("text", "System: $systemPrompt")
                })
            }
            parts.add(JSONObject().apply {
                put("text", userMessage)
            })
            
            val requestBody = JSONObject().apply {
                put("contents", JSONArray().apply {
                    put(JSONObject().apply {
                        put("parts", JSONArray(parts))
                    })
                })
                put("generationConfig", JSONObject().apply {
                    put("temperature", temperature)
                    put("maxOutputTokens", maxTokens)
                })
            }
            
            val url = "$baseUrl/models/$model:generateContent?key=$apiKey"
            val request = Request.Builder()
                .url(url)
                .addHeader("Content-Type", "application/json")
                .post(buildRequestBody(requestBody))
                .build()
            
            executeRequest(request).getOrThrow().let { response ->
                val json = JSONObject(response)
                json.getJSONArray("candidates")
                    .getJSONObject(0)
                    .getJSONObject("content")
                    .getJSONArray("parts")
                    .getJSONObject(0)
                    .getString("text")
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
            val parts = mutableListOf<JSONObject>()
            if (systemPrompt.isNotBlank()) {
                parts.add(JSONObject().apply {
                    put("text", "System: $systemPrompt")
                })
            }
            parts.add(JSONObject().apply {
                put("text", userMessage)
            })
            
            val requestBody = JSONObject().apply {
                put("contents", JSONArray().apply {
                    put(JSONObject().apply {
                        put("parts", JSONArray(parts))
                    })
                })
                put("generationConfig", JSONObject().apply {
                    put("temperature", temperature)
                    put("maxOutputTokens", maxTokens)
                })
            }
            
            val url = "$baseUrl/models/$model:streamGenerateContent?key=$apiKey"
            val request = Request.Builder()
                .url(url)
                .addHeader("Content-Type", "application/json")
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
                        val candidates = json.getJSONArray("candidates")
                        if (candidates.length() > 0) {
                            val content = candidates.getJSONObject(0).getJSONObject("content")
                            val parts = content.getJSONArray("parts")
                            for (i in 0 until parts.length()) {
                                val part = parts.getJSONObject(i)
                                if (part.has("text")) {
                                    val text = part.getString("text")
                                    fullContent += text
                                    onChunk(text)
                                }
                            }
                        }
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
            val requestBody = JSONObject().apply {
                put("contents", JSONArray().apply {
                    put(JSONObject().apply {
                        put("parts", JSONArray().put(JSONObject().put("text", "ping")))
                    })
                })
                put("generationConfig", JSONObject().put("maxOutputTokens", 1))
            }
            val url = "$baseUrl/models/$model:generateContent?key=$apiKey"
            val request = Request.Builder()
                .url(url)
                .addHeader("Content-Type", "application/json")
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