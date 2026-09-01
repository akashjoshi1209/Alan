package com.alan.tools

import com.alan.agent.Tool
import com.alan.agent.ToolResult
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.withContext
import okhttp3.MediaType.Companion.toMediaType
import okhttp3.OkHttpClient
import okhttp3.Request
import okhttp3.RequestBody.Companion.toRequestBody
import org.json.JSONArray
import org.json.JSONObject
import java.io.File
import java.util.concurrent.TimeUnit

class FileTool : Tool {
    override val name = "file"
    override val description = "Read, write, edit, list, and search files in the project"
    
    override val parametersSchema = JSONObject().apply {
        put("type", "object")
        put("properties", JSONObject().apply {
            put("action", JSONObject().apply {
                put("type", "string")
                put("enum", JSONObject().apply {
                    put("read", JSONObject())
                    put("write", JSONObject())
                    put("edit", JSONObject())
                    put("list", JSONObject())
                    put("glob", JSONObject())
                    put("grep", JSONObject())
                    put("delete", JSONObject())
                })
                put("description", "Action to perform")
            })
            put("path", JSONObject().apply {
                put("type", "string")
                put("description", "File or directory path (relative to project root)")
            })
            put("content", JSONObject().apply {
                put("type", "string")
                put("description", "Content to write (for write action)")
            })
            put("oldText", JSONObject().apply {
                put("type", "string")
                put("description", "Text to replace (for edit action)")
            })
            put("newText", JSONObject().apply {
                put("type", "string")
                put("description", "New text (for edit action)")
            })
            put("pattern", JSONObject().apply {
                put("type", "string")
                put("description", "Glob pattern (for glob action) or regex (for grep action)")
            })
            put("recursive", JSONObject().apply {
                put("type", "boolean")
                put("default", true)
                put("description", "Whether to search recursively")
            })
        })
        put("required", JSONArray().put("action").put("path"))
    }
    
    private val client = OkHttpClient.Builder()
        .connectTimeout(10, TimeUnit.SECONDS)
        .readTimeout(30, TimeUnit.SECONDS)
        .build()
    
    private val mediaType = "application/json".toMediaType()
    
    override suspend fun execute(args: JSONObject): ToolResult = withContext(Dispatchers.IO) {
        val action = args.optString("action", "read")
        val path = args.optString("path", "")
        val projectRoot = args.optString("projectRoot", "/data/data/com.alan/files/projects")
        
        val fullPath = if (path.startsWith("/")) path else File(projectRoot, path).absolutePath
        val file = File(fullPath)
        
        return@withContext when (action) {
            "read" -> readFile(file)
            "write" -> writeFile(file, args.optString("content", ""))
            "edit" -> editFile(file, args.optString("oldText", ""), args.optString("newText", ""))
            "list" -> listDirectory(file)
            "glob" -> globFiles(projectRoot, args.optString("pattern", "**/*"), args.optBoolean("recursive", true))
            "grep" -> grepFiles(projectRoot, args.optString("pattern", ""), args.optBoolean("recursive", true))
            "delete" -> deleteFile(file)
            else -> ToolResult(false, "", "Unknown action: $action")
        }
    }
    
    private fun readFile(file: File): ToolResult {
        return try {
            if (!file.exists()) {
                ToolResult(false, "", "File not found: ${file.absolutePath}")
            } else if (!file.isFile) {
                ToolResult(false, "", "Not a file: ${file.absolutePath}")
            } else {
                val content = file.readText()
                ToolResult(true, content)
            }
        } catch (e: Exception) {
            ToolResult(false, "", "Read error: ${e.message}")
        }
    }
    
    private fun writeFile(file: File, content: String): ToolResult {
        return try {
            file.parentFile?.mkdirs()
            file.writeText(content)
            ToolResult(true, "Written ${content.length} bytes to ${file.absolutePath}")
        } catch (e: Exception) {
            ToolResult(false, "", "Write error: ${e.message}")
        }
    }
    
    private fun editFile(file: File, oldText: String, newText: String): ToolResult {
        return try {
            if (!file.exists()) {
                ToolResult(false, "", "File not found: ${file.absolutePath}")
            } else {
                val content = file.readText()
                if (!content.contains(oldText)) {
                    ToolResult(false, "", "Text not found in file")
                } else {
                    val newContent = content.replace(oldText, newText)
                    file.writeText(newContent)
                    ToolResult(true, "Edited ${file.absolutePath}")
                }
            }
        } catch (e: Exception) {
            ToolResult(false, "", "Edit error: ${e.message}")
        }
    }
    
    private fun listDirectory(file: File): ToolResult {
        return try {
            if (!file.exists()) {
                ToolResult(false, "", "Directory not found: ${file.absolutePath}")
            } else if (!file.isDirectory) {
                ToolResult(false, "", "Not a directory: ${file.absolutePath}")
            } else {
                val files = file.listFiles()?.map { f ->
                    JSONObject().apply {
                        put("name", f.name)
                        put("path", f.absolutePath)
                        put("isDirectory", f.isDirectory)
                        put("size", f.length())
                        put("lastModified", f.lastModified())
                    }
                } ?: emptyList()
                ToolResult(true, JSONArray(files).toString(2))
            }
        } catch (e: Exception) {
            ToolResult(false, "", "List error: ${e.message}")
        }
    }
    
    private fun globFiles(root: String, pattern: String, recursive: Boolean): ToolResult {
        return try {
            val rootFile = File(root)
            val results = mutableListOf<JSONObject>()
            
            fun walk(dir: File, relPath: String) {
                dir.listFiles()?.forEach { f ->
                    val childRelPath = if (relPath.isEmpty()) f.name else "$relPath/${f.name}"
                    val matches = matchGlob(childRelPath, pattern)
                    if (matches) {
                        results.add(JSONObject().apply {
                            put("path", childRelPath)
                            put("isDirectory", f.isDirectory)
                            put("size", f.length())
                        })
                    }
                    if (recursive && f.isDirectory) {
                        walk(f, childRelPath)
                    }
                }
            }
            
            walk(rootFile, "")
            ToolResult(true, JSONArray(results).toString(2))
        } catch (e: Exception) {
            ToolResult(false, "", "Glob error: ${e.message}")
        }
    }
    
    private fun matchGlob(path: String, pattern: String): Boolean {
        val regex = pattern
            .replace(".", "\\.")
            .replace("*", ".*")
            .replace("?", ".")
        return path.matches(regex.toRegex())
    }
    
    private fun grepFiles(root: String, pattern: String, recursive: Boolean): ToolResult {
        return try {
            val rootFile = File(root)
            val results = mutableListOf<JSONObject>()
            val regex = pattern.toRegex()
            
            fun walk(dir: File, relPath: String) {
                dir.listFiles()?.forEach { f ->
                    val childRelPath = if (relPath.isEmpty()) f.name else "$relPath/${f.name}"
                    if (f.isFile && f.length() < 1_000_000) { // Skip large files
                        try {
                            val content = f.readText()
                            val lines = content.lines()
                            lines.forEachIndexed { index, line ->
                                if (regex.containsMatchIn(line)) {
                                    results.add(JSONObject().apply {
                                        put("file", childRelPath)
                                        put("line", index + 1)
                                        put("content", line.trim())
                                    })
                                }
                            }
                        } catch (e: Exception) {
                            // Skip binary/unreadable files
                        }
                    }
                    if (recursive && f.isDirectory) {
                        walk(f, childRelPath)
                    }
                }
            }
            
            walk(rootFile, "")
            ToolResult(true, JSONArray(results.take(100)).toString(2)) // Limit results
        } catch (e: Exception) {
            ToolResult(false, "", "Grep error: ${e.message}")
        }
    }
    
    private fun deleteFile(file: File): ToolResult {
        return try {
            if (!file.exists()) {
                ToolResult(false, "", "File not found: ${file.absolutePath}")
            } else {
                file.deleteRecursively()
                ToolResult(true, "Deleted ${file.absolutePath}")
            }
        } catch (e: Exception) {
            ToolResult(false, "", "Delete error: ${e.message}")
        }
    }
}