package com.alan.skills

import android.content.Context
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.withContext
import org.json.JSONArray
import org.json.JSONObject
import java.io.File
import java.util.UUID
import java.util.regex.Pattern

class CodeSkillEngine(private val context: Context) {
    
    private val skillsDir: File
        get() = File(context.filesDir, "skills").also { it.mkdirs() }
    
    private var skillsLoaded = false
    private val skillsCache = mutableListOf<CodeSkill>()
    
    suspend fun loadSkills() = withContext(Dispatchers.IO) {
        if (skillsLoaded) return@withContext
        
        copyBuiltinSkillsIfNeeded()
        
        val jsonFiles = skillsDir.listFiles { _, name -> name.endsWith(".json") }
        
        skillsCache.clear()
        jsonFiles?.forEach { file ->
            try {
                val json = JSONObject(file.readText())
                skillsCache.add(parseSkill(json))
            } catch (e: Exception) {
                e.printStackTrace()
            }
        }
        
        skillsLoaded = true
    }
    
    private fun copyBuiltinSkillsIfNeeded() {
        val assetNames = context.assets.list("skills")
        if (assetNames == null || assetNames.isEmpty()) return
        
        assetNames.forEach { name ->
            val destFile = File(skillsDir, name)
            if (!destFile.exists()) {
                try {
                    context.assets.open("skills/$name").use { input ->
                        destFile.outputStream().use { output ->
                            input.copyTo(output)
                        }
                    }
                } catch (e: Exception) {
                    e.printStackTrace()
                }
            }
        }
    }
    
    private fun parseSkill(json: JSONObject): CodeSkill {
        val triggerPhrases = json.getJSONArray("triggerPhrases").let { arr ->
            (0 until arr.length()).map { arr.getString(it) }
        }
        
        val parameters = json.getJSONArray("parameters").let { arr ->
            (0 until arr.length()).map { i ->
                val obj = arr.getJSONObject(i)
                SkillParameter(
                    key = obj.getString("key"),
                    description = obj.optString("description", ""),
                    type = obj.optString("type", "string"),
                    required = obj.optBoolean("required", true),
                    defaultValue = obj.optString("defaultValue", null)
                )
            }
        }
        
        val steps = json.getJSONArray("steps").let { arr ->
            (0 until arr.length()).map { i ->
                val obj = arr.getJSONObject(i)
                SkillStep(
                    tool = obj.getString("tool"),
                    description = obj.getString("description"),
                    parameters = obj.optJSONObject("parameters") ?: JSONObject()
                )
            }
        }
        
        val tags = json.getJSONArray("tags").let { arr ->
            (0 until arr.length()).map { arr.getString(it) }
        }
        
        return CodeSkill(
            id = json.getString("id"),
            name = json.optString("name", ""),
            description = json.optString("description", ""),
            version = json.optString("version", "1.0"),
            author = json.optString("author", ""),
            triggerPhrases = triggerPhrases,
            parameters = parameters,
            steps = steps,
            llmHint = json.optString("llmHint", ""),
            successVerification = json.optString("successVerification", ""),
            tags = tags,
            usageCount = json.optInt("usageCount", 0),
            successRate = json.optDouble("successRate", 1.0).toFloat()
        )
    }
    
    fun findMatch(command: String): CodeSkill? {
        val lower = command.lowercase()
        
        for (skill in skillsCache) {
            for (phrase in skill.triggerPhrases) {
                val pattern = phrase.replace("*", "(.+)")
                val regex = Pattern.compile(pattern, Pattern.CASE_INSENSITIVE)
                val matcher = regex.matcher(lower)
                
                if (matcher.find()) {
                    return skill
                }
            }
        }
        
        return null
    }
    
    fun extractParameters(command: String, skill: CodeSkill): Map<String, String> {
        val result = mutableMapOf<String, String>()
        val lower = command.lowercase()
        
        for (skillParam in skill.parameters) {
            for (phrase in skill.triggerPhrases) {
                val pattern = phrase.replace("*", "(.+)")
                val regex = Pattern.compile(pattern, Pattern.CASE_INSENSITIVE)
                val matcher = regex.matcher(lower)
                
                if (matcher.find()) {
                    val group = matcher.group(1)?.trim() ?: continue
                    
                    if (skillParam.key in setOf("file", "path", "query", "message", "pattern", "name")) {
                        result[skillParam.key] = group
                    }
                    break
                }
            }
        }
        
        // Apply defaults for missing parameters
        for (param in skill.parameters) {
            if (!result.containsKey(param.key) && param.defaultValue != null) {
                result[param.key] = param.defaultValue!!
            }
        }
        
        return result
    }
    
    fun buildExecutionPlan(skill: CodeSkill, params: Map<String, String>): List<SkillStep> {
        return skill.steps.map { step ->
            val resolvedParams = JSONObject(step.parameters.toString())
            for ((key, value) in params) {
                val placeholder = "{$key}"
                if (resolvedParams.has(key)) {
                    resolvedParams.put(key, value)
                } else {
                    // Replace in string values
                    resolvedParams.keys().forEachRemaining { k ->
                        val v = resolvedParams.optString(k, "")
                        if (v.contains(placeholder)) {
                            resolvedParams.put(k, v.replace(placeholder, value))
                        }
                    }
                }
            }
            step.copy(parameters = resolvedParams)
        }
    }
    
    suspend fun recordUsage(skillId: String, success: Boolean) = withContext(Dispatchers.IO) {
        val index = skillsCache.indexOfFirst { it.id == skillId }
        if (index < 0) return@withContext
        
        val skill = skillsCache[index]
        val newCount = skill.usageCount + 1
        val newRate = if (success) {
            ((skill.successRate * skill.usageCount) + 1f) / newCount
        } else {
            (skill.successRate * skill.usageCount) / newCount
        }
        
        val updated = skill.copy(usageCount = newCount, successRate = newRate)
        skillsCache[index] = updated
        
        saveSkill(updated)
    }
    
    private fun saveSkill(skill: CodeSkill) {
        val json = JSONObject().apply {
            put("id", skill.id)
            put("name", skill.name)
            put("description", skill.description)
            put("version", skill.version)
            put("author", skill.author)
            put("triggerPhrases", JSONArray(skill.triggerPhrases))
            put("parameters", JSONArray(skill.parameters.map {
                JSONObject().apply {
                    put("key", it.key)
                    put("description", it.description)
                    put("type", it.type)
                    put("required", it.required)
                    put("defaultValue", it.defaultValue)
                }
            }))
            put("steps", JSONArray(skill.steps.map {
                JSONObject().apply {
                    put("tool", it.tool)
                    put("description", it.description)
                    put("parameters", it.parameters)
                }
            }))
            put("llmHint", skill.llmHint)
            put("successVerification", skill.successVerification)
            put("tags", JSONArray(skill.tags))
            put("usageCount", skill.usageCount)
            put("successRate", skill.successRate.toDouble())
        }
        
        File(skillsDir, "${skill.id}.json").writeText(json.toString(2))
    }
    
    fun generateSkill(command: String, steps: List<SkillStep>): CodeSkill? {
        if (steps.size < 2) return null
        
        val usedTools = steps.map { it.tool }.distinct()
        
        return CodeSkill(
            id = "auto-${UUID.randomUUID().toString().take(8)}",
            name = command.take(40),
            description = "Auto-generated from: $command",
            version = "1.0",
            author = "generated",
            triggerPhrases = listOf(command.lowercase()),
            parameters = listOf(SkillParameter("input", "User input", "string", true, null)),
            steps = steps,
            llmHint = "Custom code workflow",
            successVerification = "Task completed successfully",
            tags = listOf("generated", "custom") + usedTools,
            usageCount = 0,
            successRate = 1.0f
        )
    }
    
    fun getAllSkills(): List<CodeSkill> = skillsCache.toList()
    
    fun getSkillCount(): Int = skillsCache.size
    
    data class SkillParameter(
        val key: String,
        val description: String,
        val type: String,
        val required: Boolean,
        val defaultValue: String?
    )
}

data class CodeSkill(
    val id: String,
    val name: String,
    val description: String,
    val version: String,
    val author: String,
    val triggerPhrases: List<String>,
    val parameters: List<CodeSkillEngine.SkillParameter>,
    val steps: List<SkillStep>,
    val llmHint: String,
    val successVerification: String,
    val tags: List<String>,
    val usageCount: Int,
    val successRate: Float
)

data class SkillStep(
    val tool: String,
    val description: String,
    val parameters: JSONObject
) {
    fun copy(parameters: JSONObject = this.parameters): SkillStep {
        return SkillStep(tool, description, parameters)
    }
}