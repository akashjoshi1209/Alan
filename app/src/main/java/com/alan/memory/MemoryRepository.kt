package com.alan.memory

import android.content.Context
import com.alan.memory.nodes.*
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.flow.Flow
import kotlinx.coroutines.flow.flow
import kotlinx.coroutines.withContext
import java.util.Calendar

class MemoryRepository(context: Context) {
    
    private val db = MemoryDB.getInstance(context)
    private val projectNodeDao: ProjectNodeDao = db.projectNodeDao()
    private val codeTaskNodeDao: CodeTaskNodeDao = db.codeTaskNodeDao()
    private val symbolNodeDao: SymbolNodeDao = db.symbolNodeDao()
    private val codePatternNodeDao: CodePatternNodeDao = db.codePatternNodeDao()
    private val decisionNodeDao: DecisionNodeDao = db.decisionNodeDao()
    private val fileNodeDao: FileNodeDao = db.fileNodeDao()
    private val edgeDao: EdgeDao = db.edgeDao()
    private val providerStatsDao: ProviderStatsDao = db.providerStatsDao()
    
    // Project operations
    suspend fun getOrCreateProject(path: String, name: String? = null): ProjectNode = withContext(Dispatchers.IO) {
        val existing = projectNodeDao.getByPath(path)
        if (existing != null) {
            projectNodeDao.updateLastOpened(existing.id, System.currentTimeMillis())
            return@withContext existing
        }
        
        val projectName = name ?: path.substringAfterLast("/").takeIf { it.isNotBlank() } ?: "Unknown Project"
        val project = ProjectNode(
            path = path,
            name = projectName
        )
        val id = projectNodeDao.insert(project)
        project.copy(id = id)
    }
    
    suspend fun getRecentProjects(limit: Int = 10): List<ProjectNode> = withContext(Dispatchers.IO) {
        projectNodeDao.getRecentProjects(limit)
    }
    
    suspend fun getProjectById(id: Long): ProjectNode? = withContext(Dispatchers.IO) {
        projectNodeDao.getById(id)
    }
    
    suspend fun updateProjectLanguage(projectId: Long, language: String, framework: String?): Int = withContext(Dispatchers.IO) {
        val project = projectNodeDao.getById(projectId)
        if (project != null) {
            projectNodeDao.updateProject(projectId, project.name, language, framework, project.gitRemote)
        } else {
            0
        }
    }
    
    // Code Task operations
    suspend fun logTask(
        projectId: Long,
        userQuery: String,
        plan: String = "",
        steps: String = "",
        result: String,
        artifacts: String = "",
        provider: String? = null,
        latencyMs: Long = 0L,
        tokensUsed: Int = 0
    ): Long = withContext(Dispatchers.IO) {
        val task = CodeTaskNode(
            projectId = projectId,
            userQuery = userQuery,
            plan = plan,
            steps = steps,
            result = result,
            artifacts = artifacts,
            providerUsed = provider,
            latencyMs = latencyMs,
            tokensUsed = tokensUsed
        )
        codeTaskNodeDao.insert(task)
    }
    
    suspend fun getRecentTasks(limit: Int = 20): List<CodeTaskNode> = withContext(Dispatchers.IO) {
        codeTaskNodeDao.getRecentTasks(limit)
    }
    
    suspend fun getTasksForProject(projectId: Long, limit: Int = 50): List<CodeTaskNode> = withContext(Dispatchers.IO) {
        codeTaskNodeDao.getTasksForProject(projectId, limit)
    }
    
    suspend fun searchTasks(query: String, limit: Int = 20): List<CodeTaskNode> = withContext(Dispatchers.IO) {
        codeTaskNodeDao.searchTasks(query, limit)
    }
    
    suspend fun getTasksByProvider(provider: String, limit: Int = 20): List<CodeTaskNode> = withContext(Dispatchers.IO) {
        codeTaskNodeDao.getTasksByProvider(provider, limit)
    }
    
    // Symbol operations
    suspend fun upsertSymbol(symbol: SymbolNode): Long = withContext(Dispatchers.IO) {
        symbolNodeDao.insert(symbol)
    }
    
    suspend fun upsertSymbols(symbols: List<SymbolNode>): List<Long> = withContext(Dispatchers.IO) {
        symbolNodeDao.insertAll(symbols)
    }
    
    suspend fun getSymbolsForProject(projectId: Long, limit: Int = 100): List<SymbolNode> = withContext(Dispatchers.IO) {
        symbolNodeDao.getAllForProject(projectId, limit)
    }
    
    suspend fun searchSymbols(projectId: Long, query: String, limit: Int = 50): List<SymbolNode> = withContext(Dispatchers.IO) {
        symbolNodeDao.searchSymbols(projectId, query, limit)
    }
    
    suspend fun getSymbolsByFile(projectId: Long, filePath: String): List<SymbolNode> = withContext(Dispatchers.IO) {
        symbolNodeDao.getByFilePath(projectId, filePath)
    }
    
    suspend fun deleteSymbolsByFile(projectId: Long, filePath: String): Int = withContext(Dispatchers.IO) {
        symbolNodeDao.deleteByFilePath(projectId, filePath)
    }
    
    // Code Pattern operations
    suspend fun upsertPattern(pattern: CodePatternNode): Long = withContext(Dispatchers.IO) {
        codePatternNodeDao.insert(pattern)
    }
    
    suspend fun getPatternsForProject(projectId: Long, limit: Int = 50): List<CodePatternNode> = withContext(Dispatchers.IO) {
        codePatternNodeDao.getPatternsForProject(projectId, limit)
    }
    
    suspend fun getPatternsByType(projectId: Long, patternType: String, limit: Int = 20): List<CodePatternNode> = withContext(Dispatchers.IO) {
        codePatternNodeDao.getPatternsByType(projectId, patternType, limit)
    }
    
    suspend fun getHighConfidencePatterns(projectId: Long, minConfidence: Float = 0.5f, limit: Int = 20): List<CodePatternNode> = withContext(Dispatchers.IO) {
        codePatternNodeDao.getHighConfidencePatterns(projectId, minConfidence, limit)
    }
    
    suspend fun searchPatterns(projectId: Long, query: String, limit: Int = 20): List<CodePatternNode> = withContext(Dispatchers.IO) {
        codePatternNodeDao.searchPatterns(projectId, query, limit)
    }
    
    suspend fun recordPatternUsage(patternId: Long, success: Boolean, newConfidence: Float? = null): Int = withContext(Dispatchers.IO) {
        val current = codePatternNodeDao.getById(patternId)
        current?.let {
            val newSuccessRate = if (success) {
                ((it.successRate * it.usageCount) + 1f) / (it.usageCount + 1)
            } else {
                (it.successRate * it.usageCount) / (it.usageCount + 1)
            }
            codePatternNodeDao.recordUsage(patternId, success, newSuccessRate)
        } ?: 0
    }
    
    // Decision operations
    suspend fun upsertDecision(decision: DecisionNode): Long = withContext(Dispatchers.IO) {
        decisionNodeDao.insert(decision)
    }
    
    suspend fun getDecisionsForProject(projectId: Long, limit: Int = 50): List<DecisionNode> = withContext(Dispatchers.IO) {
        decisionNodeDao.getDecisionsForProject(projectId, limit)
    }
    
    suspend fun searchDecisions(projectId: Long, query: String, limit: Int = 20): List<DecisionNode> = withContext(Dispatchers.IO) {
        decisionNodeDao.searchDecisions(projectId, query, limit)
    }
    
    // File operations
    suspend fun upsertFile(file: FileNode): Long = withContext(Dispatchers.IO) {
        fileNodeDao.insert(file)
    }
    
    suspend fun upsertFiles(files: List<FileNode>): List<Long> = withContext(Dispatchers.IO) {
        fileNodeDao.insertAll(files)
    }
    
    suspend fun getFilesForProject(projectId: Long, limit: Int = 100): List<FileNode> = withContext(Dispatchers.IO) {
        fileNodeDao.getAllForProject(projectId, limit)
    }
    
    suspend fun searchFiles(projectId: Long, query: String, limit: Int = 50): List<FileNode> = withContext(Dispatchers.IO) {
        fileNodeDao.searchFiles(projectId, query, limit)
    }
    
    suspend fun deleteFile(projectId: Long, path: String): Int = withContext(Dispatchers.IO) {
        fileNodeDao.deleteByPath(projectId, path)
    }
    
    // Edge operations
    suspend fun addEdge(edge: EdgeEntity): Long = withContext(Dispatchers.IO) {
        edgeDao.insert(edge)
    }
    
    suspend fun addEdges(edges: List<EdgeEntity>): List<Long> = withContext(Dispatchers.IO) {
        edgeDao.insertAll(edges)
    }
    
    suspend fun getOutgoingEdges(sourceType: String, sourceId: Long): List<EdgeEntity> = withContext(Dispatchers.IO) {
        edgeDao.getOutgoingEdges(sourceType, sourceId)
    }
    
    suspend fun getIncomingEdges(targetType: String, targetId: Long): List<EdgeEntity> = withContext(Dispatchers.IO) {
        edgeDao.getIncomingEdges(targetType, targetId)
    }
    
    suspend fun deleteOutgoingEdges(sourceType: String, sourceId: Long): Int = withContext(Dispatchers.IO) {
        edgeDao.deleteOutgoingEdges(sourceType, sourceId)
    }
    
    // Provider stats
    suspend fun incrementProviderUse(provider: String): Int = withContext(Dispatchers.IO) {
        providerStatsDao.incrementUse(provider)
    }
    
    suspend fun recordProviderSuccess(provider: String, latency: Long): Int = withContext(Dispatchers.IO) {
        providerStatsDao.recordSuccess(provider, latency)
    }
    
    suspend fun getProviderStats(): List<ProviderStats> = withContext(Dispatchers.IO) {
        providerStatsDao.getAllProviders().map { p ->
            ProviderStats(
                name = p.name,
                useCount = p.useCount,
                successRate = if (p.useCount > 0) p.successCount.toFloat() / p.useCount else 0f,
                avgLatencyMs = if (p.useCount > 0) p.totalLatencyMs / p.useCount else 0L
            )
        }
    }
    
    // Memory context building for LLM
    fun buildMemoryContext(projectId: Long? = null, query: String = ""): String = kotlinx.coroutines.runBlocking {
        withContext(Dispatchers.IO) {
            val sb = StringBuilder()
            
            // Recent tasks
            val tasks = if (projectId != null) {
                codeTaskNodeDao.getTasksForProject(projectId, 10)
            } else {
                codeTaskNodeDao.getRecentTasks(10)
            }
            
            if (tasks.isNotEmpty()) {
                sb.appendLine("## Recent Tasks")
                for (task in tasks.take(5)) {
                    sb.appendLine("- ${task.userQuery} → ${task.result.take(100)}")
                }
            }
            
            // Patterns
            if (projectId != null) {
                val patterns = codePatternNodeDao.getHighConfidencePatterns(projectId, 0.5f, 5)
                if (patterns.isNotEmpty()) {
                    sb.appendLine("\n## Learned Code Patterns")
                    for (pattern in patterns.take(3)) {
                        sb.appendLine("- [${pattern.patternType}] ${pattern.trigger} (${(pattern.confidence * 100).toInt()}% confidence, ${pattern.usageCount} uses)")
                    }
                }
            }
            
            // Decisions
            if (projectId != null) {
                val decisions = decisionNodeDao.getDecisionsForProject(projectId, 5)
                if (decisions.isNotEmpty()) {
                    sb.appendLine("\n## Architectural Decisions")
                    for (decision in decisions.take(3)) {
                        sb.appendLine("- ${decision.title}: ${decision.decision.take(80)}")
                    }
                }
            }
            
            sb.toString().take(2000)
        }
    }
    
    // Stats
    suspend fun getTaskStats(): TaskStats = withContext(Dispatchers.IO) {
        val all = codeTaskNodeDao.getRecentTasks(1000)
        val total = all.size
        val today = all.count {
            it.timestamp > System.currentTimeMillis() - 24 * 60 * 60 * 1000
        }
        val failed = all.count {
            it.result.contains("Error") || it.result.contains("Failed") || it.result.contains("Exception")
        }
        val failRate = if (total > 0) failed.toFloat() / total else 0f
        TaskStats(total, today, failRate)
    }
    
    data class TaskStats(
        val totalTasks: Int,
        val tasksToday: Int,
        val failRate: Float
    )
    
    data class ProviderStats(
        val name: String,
        val useCount: Int,
        val successRate: Float,
        val avgLatencyMs: Long
    )
}