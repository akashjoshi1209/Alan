package com.alan.bridge

import android.content.Context
import android.os.Process
import com.alan.agent.AlanCore
import com.alan.agent.AlanState
import com.alan.memory.MemoryRepository
import kotlinx.coroutines.*
import kotlinx.coroutines.flow.collectLatest
import java.io.BufferedReader
import java.io.InputStreamReader
import java.io.OutputStreamWriter
import java.io.PrintWriter
import java.net.ServerSocket
import java.net.Socket
import java.util.UUID

class UnixSocketServer(
    private val context: Context,
    private val alanCore: AlanCore,
    private val memoryRepo: MemoryRepository
) {
    private val scope = CoroutineScope(Dispatchers.IO + SupervisorJob())
    private var serverSocket: ServerSocket? = null
    private var isRunning = false
    private var runningPort = 0
    
    private val socketFile: java.io.File
        get() = java.io.File(context.filesDir, "alan.port")
    
    private val allowedUids = setOf(
        context.applicationInfo.uid,
        2000, // shell user
        "com.termux".hashCode()
    )
    
    private fun isAuthorized(): Boolean {
        return try {
            val callingUid = Process.myUid()
            callingUid in allowedUids
        } catch (e: Exception) {
            false
        }
    }
    
    companion object {
        private const val SOCKET_NAME = "alan.port"
        private const val MAX_COMMAND_LENGTH = 5000
        private const val MAX_REQUESTS_PER_SECOND = 20
    }
    
    fun start() {
        if (isRunning) return
        
        isRunning = true
        scope.launch {
            acceptConnections()
        }
    }
    
    fun stop() {
        isRunning = false
        serverSocket?.close()
        serverSocket = null
        
        val sockFile = socketFile
        if (sockFile.exists()) {
            sockFile.delete()
        }
        
        scope.coroutineContext[Job]?.cancelChildren()
    }
    
    fun getPort(): Int = runningPort
    
    private suspend fun acceptConnections() = withContext(Dispatchers.IO) {
        try {
            serverSocket = ServerSocket(0)
            runningPort = serverSocket?.localPort ?: 0
            
            val sockFile = socketFile
            sockFile.writeText(runningPort.toString())
            
            while (isRunning) {
                try {
                    val client = serverSocket?.accept() ?: break
                    scope.launch { handleClient(client) }
                } catch (e: Exception) {
                    if (isRunning) {
                        delay(100)
                    }
                }
            }
        } catch (e: Exception) {
            e.printStackTrace()
        }
    }
    
    private suspend fun handleClient(clientSocket: Socket) = withContext(Dispatchers.IO) {
        try {
            val reader = BufferedReader(InputStreamReader(clientSocket.getInputStream()))
            val writer = PrintWriter(OutputStreamWriter(clientSocket.getOutputStream()), true)
            
            val line = reader.readLine() ?: run {
                writer.println("""{"requestId":"","status":"error","result":"empty request"}""")
                return@withContext
            }
            
            if (!isAuthorized()) {
                writer.println(createErrorResponse("", "unauthorized"))
                return@withContext
            }
            
            val request = parseRequest(line)
            if (request == null) {
                writer.println(createErrorResponse("", "invalid JSON"))
                return@withContext
            }
            
            val (requestId, cmd) = request
            
            when (cmd.lowercase().trim()) {
                "status" -> handleStatus(writer, requestId)
                "history" -> handleHistory(writer, requestId)
                "providers" -> handleProviders(writer, requestId)
                "memory" -> handleMemory(writer, requestId)
                "tools" -> handleTools(writer, requestId)
                else -> handleCommand(writer, requestId, cmd)
            }
            
            writer.flush()
        } catch (e: Exception) {
            e.printStackTrace()
        } finally {
            try {
                clientSocket.close()
            } catch (e: Exception) { }
        }
    }
    
    private fun handleStatus(writer: PrintWriter, requestId: String) {
        val provider = try {
            alanCore.getCurrentProviderName()
        } catch (e: Exception) { "unknown" }
        
        val statusJson = buildString {
            append("{")
            append("\"requestId\": \"$requestId\",")
            append("\"status\": \"done\",")
            append("\"result\": {")
            append("\"service\": \"running\",")
            append("\"provider\": \"$provider\",")
            append("\"port\": $runningPort")
            append("}}")
        }
        
        writer.println(statusJson)
    }
    
    private suspend fun handleHistory(writer: PrintWriter, requestId: String) {
        val tasks = memoryRepo.getRecentTasks(20)
        val tasksJson = tasks.joinToString(",", "[", "]") { task ->
            """{"id":${task.id},"query":"${escapeJson(task.userQuery)}","result":"${escapeJson(task.result)}","timestamp":${task.timestamp},"provider":"${task.providerUsed ?: "unknown"}","latencyMs":${task.latencyMs}}"""
        }
        
        writer.println("""{"requestId":"$requestId","status":"done","result":$tasksJson}""")
    }
    
    private suspend fun handleProviders(writer: PrintWriter, requestId: String) {
        val providers = memoryRepo.getProviderStats()
        val providersJson = providers.joinToString(",", "[", "]") { p ->
            """{"name":"${p.name}","useCount":${p.useCount},"successRate":${p.successRate}}"""
        }
        
        writer.println("""{"requestId":"$requestId","status":"done","result":$providersJson}""")
    }
    
    private suspend fun handleMemory(writer: PrintWriter, requestId: String) {
        val context = memoryRepo.buildMemoryContext()
        val contextJson = """{"requestId":"$requestId","status":"done","result":"${escapeJson(context)}"}"""
        writer.println(contextJson)
    }
    
    private suspend fun handleTools(writer: PrintWriter, requestId: String) {
        val tools = alanCore.getAvailableTools()
        val toolsJson = tools.joinToString(",", "[", "]") { tool ->
            """{"name":"${tool.name}","description":"${escapeJson(tool.description)}","parameters":${tool.parametersSchema}}"""
        }
        
        writer.println("""{"requestId":"$requestId","status":"done","result":$toolsJson}""")
    }
    
    private suspend fun handleCommand(writer: PrintWriter, requestId: String, cmd: String) {
        val job = scope.launch {
            alanCore.getStateFlow().collectLatest { state ->
                when (state) {
                    is AlanState.Running -> {
                        writer.println("""{"requestId":"$requestId","status":"progress","result":"${escapeJson(state.step)}"}""")
                        writer.flush()
                    }
                    is AlanState.Done -> {
                        writer.println("""{"requestId":"$requestId","status":"done","result":"${escapeJson(state.result)}"}""")
                        writer.flush()
                        cancel()
                    }
                    is AlanState.Error -> {
                        writer.println("""{"requestId":"$requestId","status":"error","result":"${escapeJson(state.message)}"}""")
                        writer.flush()
                        cancel()
                    }
                    is AlanState.Idle -> { }
                }
            }
        }
        
        alanCore.executeTask(cmd)
        
        job.join()
    }
    
    private fun parseRequest(line: String): Pair<String, String>? {
        return try {
            val json = org.json.JSONObject(line)
            val cmd = json.optString("cmd", "")
            val requestId = json.optString("requestId", UUID.randomUUID().toString())
            val tool = json.optString("tool", "")
            val args = json.optJSONObject("args")
            
            if (tool.isNotBlank()) {
                // Tool invocation format
                val argsJson = args?.toString() ?: "{}"
                val fullCmd = "tool:$tool $argsJson"
                Pair(requestId, fullCmd)
            } else if (cmd.isNotBlank()) {
                Pair(requestId, cmd)
            } else {
                null
            }
        } catch (e: Exception) {
            null
        }
    }
    
    private fun createErrorResponse(requestId: String, error: String): String {
        return """{"requestId":"$requestId","status":"error","result":"$error"}"""
    }
    
    private fun escapeJson(s: String): String {
        return s
            .replace("\\", "\\\\")
            .replace("\"", "\\\"")
            .replace("\n", "\\n")
            .replace("\r", "\\r")
            .replace("\t", "\\t")
    }
}