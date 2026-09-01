package com.alan.memory.nodes

import androidx.room.Dao
import androidx.room.Insert
import androidx.room.OnConflictStrategy
import androidx.room.Query
import androidx.room.Update
import kotlinx.coroutines.flow.Flow

@Dao
interface CodePatternNodeDao {
    @Insert(onConflict = OnConflictStrategy.REPLACE)
    suspend fun insert(pattern: CodePatternNode): Long
    
    @Insert(onConflict = OnConflictStrategy.REPLACE)
    suspend fun insertAll(patterns: List<CodePatternNode>): List<Long>
    
    @Query("SELECT * FROM code_patterns WHERE id = :id")
    suspend fun getById(id: Long): CodePatternNode?
    
    @Query("SELECT * FROM code_patterns WHERE projectId = :projectId ORDER BY confidence DESC, usageCount DESC LIMIT :limit")
    suspend fun getPatternsForProject(projectId: Long, limit: Int): List<CodePatternNode>
    
    @Query("SELECT * FROM code_patterns WHERE projectId = :projectId AND patternType = :type ORDER BY confidence DESC LIMIT :limit")
    suspend fun getPatternsByType(projectId: Long, type: String, limit: Int): List<CodePatternNode>
    
    @Query("SELECT * FROM code_patterns WHERE projectId = :projectId AND confidence >= :minConfidence ORDER BY confidence DESC LIMIT :limit")
    suspend fun getHighConfidencePatterns(projectId: Long, minConfidence: Float, limit: Int): List<CodePatternNode>
    
    @Query("SELECT * FROM code_patterns WHERE projectId = :projectId AND trigger LIKE '%' || :query || '%' LIMIT :limit")
    suspend fun searchPatterns(projectId: Long, query: String, limit: Int): List<CodePatternNode>
    
    @Query("UPDATE code_patterns SET usageCount = usageCount + 1, lastUsed = :timestamp, successRate = CASE WHEN :success THEN :successRate ELSE :successRate END WHERE id = :id")
    suspend fun recordUsage(id: Long, success: Boolean, successRate: Float, timestamp: Long = System.currentTimeMillis()): Int
    
    @Query("UPDATE code_patterns SET confidence = :confidence WHERE id = :id")
    suspend fun updateConfidence(id: Long, confidence: Float): Int
    
    @Query("DELETE FROM code_patterns WHERE projectId = :projectId")
    suspend fun deleteByProject(projectId: Long): Int
    
    @Query("DELETE FROM code_patterns WHERE id = :id")
    suspend fun delete(id: Long): Int
    
    @Query("SELECT COUNT(*) FROM code_patterns WHERE projectId = :projectId")
    suspend fun getCountForProject(projectId: Long): Int
}