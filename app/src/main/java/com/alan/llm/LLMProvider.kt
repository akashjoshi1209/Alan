package com.alan.llm

import kotlinx.coroutines.CoroutineDispatcher
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.withContext
import org.json.JSONObject
import java.util.concurrent.TimeUnit
import okhttp3.OkHttpClient
import okhttp3.Request
import okhttp3.RequestBody.Companion.toRequestBody
import okhttp3.MediaType.Companion.toMediaType

interface LLMProvider {
    val name: String
    val baseUrl: String
    val supportsStreaming: Boolean = false
    val supportsTools: Boolean = false
    val maxContextTokens: Int = 32768
    val defaultModel: String
    
    suspend fun complete(
        systemPrompt: String,
        userMessage: String,
        temperature: Float = 0.1f,
        maxTokens: Int = 2048
    ): Result<String>
    
    suspend fun completeWithTools(
        systemPrompt: String,
        userMessage: String,
        tools: List<ToolDefinition>,
        temperature: Float = 0.1f,
        maxTokens: Int = 2048
    ): Result<ToolCallResult> = withContext(Dispatchers.IO) {
        Result.failure(UnsupportedOperationException("Tools not supported by $name"))
    }
    
    suspend fun streamComplete(
        systemPrompt: String,
        userMessage: String,
        onChunk: (String) -> Unit,
        temperature: Float = 0.1f,
        maxTokens: Int = 2048
    ): Result<String> = withContext(Dispatchers.IO) {
        Result.failure(UnsupportedOperationException("Streaming not supported by $name"))
    }
    
    suspend fun testConnection(): Result<Long>
    
    fun getModelList(): List<String> = listOf(defaultModel)
}

data class ToolDefinition(
    val name: String,
    val description: String,
    val parameters: JSONObject
)

sealed class ToolCallResult {
    data class Text(val content: String) : ToolCallResult()
    data class ToolCalls(val calls: List<ToolCall>) : ToolCallResult()
    data class Mixed(val content: String, val calls: List<ToolCall>) : ToolCallResult()
}

data class ToolCall(
    val id: String,
    val name: String,
    val arguments: JSONObject
)

// Base HTTP client for providers
abstract class BaseHttpProvider(
    protected val apiKey: String,
    protected val model: String,
    protected val baseUrl: String = "https://api.openai.com/v1",
    protected val connectTimeoutSeconds: Int = 30,
    protected val readTimeoutSeconds: Int = 120
) : LLMProvider {
    
    protected val client = OkHttpClient.Builder()
        .connectTimeout(connectTimeoutSeconds, TimeUnit.SECONDS)
        .readTimeout(readTimeoutSeconds, TimeUnit.SECONDS)
        .writeTimeout(30, TimeUnit.SECONDS)
        .build()
    
    protected val mediaType = "application/json".toMediaType()
    
    protected fun buildHeaders(): Map<String, String> = mapOf(
        "Content-Type" to "application/json",
        "Authorization" to "Bearer $apiKey"
    )
    
    protected fun buildRequestBody(json: JSONObject): okhttp3.RequestBody {
        return json.toString().toRequestBody(mediaType)
    }
    
    protected suspend fun executeRequest(request: Request): Result<String> = withContext(Dispatchers.IO) {
        runCatching {
            client.newCall(request).execute().use { response ->
                val body = response.body?.string() ?: throw Exception("Empty response")
                if (!response.isSuccessful) {
                    throw Exception("HTTP ${response.code}: $body")
                }
                body
            }
        }
    }
}