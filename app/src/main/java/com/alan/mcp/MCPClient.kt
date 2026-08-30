package com.alan.mcp

import android.util.Log
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.withContext
import okhttp3.MediaType.Companion.toMediaType
import okhttp3.OkHttpClient
import okhttp3.Request
import okhttp3.RequestBody.Companion.toRequestBody
import org.json.JSONArray
import org.json.JSONObject
import java.util.concurrent.TimeUnit
import java.util.concurrent.atomic.AtomicBoolean

class MCPClient(private val server: MCPServer) {
    
    private val client = OkHttpClient.Builder()
        .connectTimeout(10, TimeUnit.SECONDS)
        .readTimeout(30, TimeUnit.SECONDS)
        .build()
    
    private val mediaType = "application/json".toMediaType()
    private val connected = AtomicBoolean(false)
    private var sessionId: String? = null
    
    suspend fun connect(): Boolean = withContext(Dispatchers.IO) {
        runCatching {
            val requestBody = JSONObject().apply {
                put("jsonrpc", "2.0")
                put("id", 1)
                put("method", "initialize")
                put("params", JSONObject().apply {
                    put("protocolVersion", "2024-11-05")
                    put("capabilities", JSONObject())
                    put("clientInfo", JSONObject().apply {
                        put("name", "Alan")
                        put("version", "1.0.0")
                    })
                })
            }.toString()
            
            val request = Request.Builder()
                .url(server.url)
                .addHeader("Content-Type", "application/json")
                .apply {
                    server.apiKey?.let { addHeader("Authorization", "Bearer $it") }
                }
                .post(requestBody.toRequestBody(mediaType))
                .build()
            
            val response = client.newCall(request).execute()
            if (!response.isSuccessful) {
                throw Exception("Connection failed: HTTP ${response.code}")
            }
            
            val body = response.body?.string() ?: throw Exception("Empty response")
            val json = JSONObject(body)
            
            if (json.has("result")) {
                sessionId = json.getJSONObject("result").optString("sessionId")
                connected.set(true)
                Log.d("MCPClient", "Connected to ${server.name}")
                true
            } else {
                throw Exception("Initialize failed: ${json.optString("error", "unknown")}")
            }
        }.getOrElse { false }
    }
    
    fun disconnect() {
        if (connected.getAndSet(false)) {
            Log.d("MCPClient", "Disconnected from ${server.name}")
        }
    }
    
    suspend fun listTools(): List<MCPTool> = withContext(Dispatchers.IO) {
        runCatching {
            if (!connected.get()) {
                if (!connect()) throw Exception("Not connected")
            }
            
            val requestBody = JSONObject().apply {
                put("jsonrpc", "2.0")
                put("id", System.currentTimeMillis())
                put("method", "tools/list")
            }.toString()
            
            val request = Request.Builder()
                .url(server.url)
                .addHeader("Content-Type", "application/json")
                .apply {
                    server.apiKey?.let { addHeader("Authorization", "Bearer $it") }
                }
                .post(requestBody.toRequestBody(mediaType))
                .build()
            
            val response = client.newCall(request).execute()
            if (!response.isSuccessful) {
                throw Exception("HTTP ${response.code}")
            }
            
            val body = response.body?.string() ?: throw Exception("Empty response")
            val json = JSONObject(body)
            
            val tools = mutableListOf<MCPTool>()
            if (json.has("result")) {
                val result = json.getJSONObject("result")
                val toolsArray = result.optJSONArray("tools") ?: return@runCatching tools
                
                for (i in 0 until toolsArray.length()) {
                    val tool = toolsArray.getJSONObject(i)
                    tools.add(MCPTool(
                        name = tool.getString("name"),
                        description = tool.getString("description"),
                        inputSchema = tool.getJSONObject("inputSchema"),
                        serverId = server.id
                    ))
                }
            }
            tools
        }.getOrElse { emptyList() }
    }
    
    suspend fun callTool(toolName: String, args: JSONObject): String = withContext(Dispatchers.IO) {
        runCatching {
            if (!connected.get()) {
                if (!connect()) throw Exception("Not connected")
            }
            
            val requestBody = JSONObject().apply {
                put("jsonrpc", "2.0")
                put("id", System.currentTimeMillis())
                put("method", "tools/call")
                put("params", JSONObject().apply {
                    put("name", toolName)
                    put("arguments", args)
                })
            }.toString()
            
            val request = Request.Builder()
                .url(server.url)
                .addHeader("Content-Type", "application/json")
                .apply {
                    server.apiKey?.let { addHeader("Authorization", "Bearer $it") }
                }
                .post(requestBody.toRequestBody(mediaType))
                .build()
            
            val response = client.newCall(request).execute()
            if (!response.isSuccessful) {
                throw Exception("HTTP ${response.code}")
            }
            
            val body = response.body?.string() ?: throw Exception("Empty response")
            val json = JSONObject(body)
            
            if (json.has("result")) {
                val result = json.getJSONObject("result")
                val contentArray = result.optJSONArray("content") ?: return@runCatching body
                
                val textContent = StringBuilder()
                for (i in 0 until contentArray.length()) {
                    val item = contentArray.getJSONObject(i)
                    if (item.getString("type") == "text") {
                        textContent.append(item.getString("text"))
                    }
                }
                textContent.toString()
            } else {
                throw Exception(json.optString("error", "Unknown error"))
            }
        }.getOrElse { "Error: ${it.message}" }
    }
    
    fun isConnected(): Boolean = connected.get()
}

data class MCPTool(
    val name: String,
    val description: String,
    val inputSchema: JSONObject,
    val serverId: String
)