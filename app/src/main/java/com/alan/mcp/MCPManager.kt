package com.alan.mcp

import android.content.Context
import androidx.security.crypto.EncryptedSharedPreferences
import androidx.security.crypto.MasterKey
import com.alan.security.EncryptedPrefs
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.withContext
import org.json.JSONArray
import org.json.JSONObject
import java.util.UUID

class MCPManager(private val context: Context) {
    
    private val clients = mutableMapOf<String, MCPClient>()
    
    private val prefs = EncryptedPrefs.getInstance(context)
    
    val serverTemplates = listOf(
        MCPServerTemplate("File System", "http://localhost:3001/mcp", true),
        MCPServerTemplate("GitHub", "https://api.github.com/mcp", false),
        MCPServerTemplate("Database", "http://localhost:3002/mcp", true),
        MCPServerTemplate("Custom", "", true)
    )
    
    suspend fun addServer(server: MCPServer) = withContext(Dispatchers.IO) {
        val id = server.id.ifBlank { UUID.randomUUID().toString() }
        val newServer = server.copy(id = id)
        
        val servers = getServers().toMutableList()
        servers.removeAll { it.id == id }
        servers.add(newServer)
        
        saveServers(servers)
        
        val client = MCPClient(newServer)
        clients[id] = client
        
        newServer
    }
    
    suspend fun removeServer(id: String) = withContext(Dispatchers.IO) {
        clients[id]?.disconnect()
        clients.remove(id)
        
        val servers = getServers().toMutableList()
        servers.removeAll { it.id == id }
        saveServers(servers)
    }
    
    suspend fun toggleServer(id: String, enabled: Boolean) = withContext(Dispatchers.IO) {
        val servers = getServers().toMutableList()
        val index = servers.indexOfFirst { it.id == id }
        if (index >= 0) {
            servers[index] = servers[index].copy(enabled = enabled)
            saveServers(servers)
            
            if (!enabled) {
                clients[id]?.disconnect()
            }
        }
    }
    
    fun getServers(): List<MCPServer> {
        val json = prefs.getString("mcp_servers", "[]") ?: return emptyList()
        return try {
            val array = JSONArray(json)
            (0 until array.length()).map { i ->
                val obj = array.getJSONObject(i)
                MCPServer(
                    id = obj.getString("id"),
                    name = obj.getString("name"),
                    url = obj.getString("url"),
                    apiKey = obj.optString("apiKey", null),
                    enabled = obj.optBoolean("enabled", true)
                )
            }
        } catch (e: Exception) {
            emptyList()
        }
    }
    
    private fun saveServers(servers: List<MCPServer>) {
        val array = JSONArray()
        servers.forEach { server ->
            array.put(JSONObject().apply {
                put("id", server.id)
                put("name", server.name)
                put("url", server.url)
                put("apiKey", server.apiKey)
                put("enabled", server.enabled)
            })
        }
        prefs.putString("mcp_servers", array.toString())
    }
    
    suspend fun getAllTools(): Map<String, List<MCPTool>> = withContext(Dispatchers.IO) {
        val result = mutableMapOf<String, List<MCPTool>>()
        
        for (server in getServers()) {
            if (!server.enabled) continue
            
            val client = clients.getOrPut(server.id) { MCPClient(server) }
            
            if (client.connect()) {
                val tools = client.listTools()
                result[server.id] = tools
            }
        }
        
        result
    }
    
    suspend fun callTool(serverId: String, toolName: String, args: JSONObject): String {
        val client = clients[serverId] ?: return "Server not found"
        return client.callTool(toolName, args)
    }
    
    suspend fun testConnection(server: MCPServer): Result<Int> = runCatching {
        val client = MCPClient(server)
        if (!client.connect()) {
            throw Exception("Connection failed")
        }
        val tools = client.listTools()
        client.disconnect()
        tools.size
    }
    
    fun buildMCPToolsPrompt(toolsMap: Map<String, List<MCPTool>>): String {
        if (toolsMap.isEmpty()) return ""
        
        val sb = StringBuilder()
        sb.appendLine("MCP TOOLS AVAILABLE:")
        
        for ((serverId, tools) in toolsMap) {
            sb.appendLine("  [$serverId]:")
            for (tool in tools) {
                sb.appendLine("    - ${tool.name}: ${tool.description}")
            }
        }
        
        return sb.toString()
    }
    
    data class MCPServerTemplate(
        val name: String,
        val defaultUrl: String,
        val requiresApiKey: Boolean
    )
}