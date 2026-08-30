package com.alan.memory.nodes

import androidx.room.Dao
import androidx.room.Insert
import androidx.room.OnConflictStrategy
import androidx.room.Query

@Dao
interface DecisionNodeDao {
    @Insert(onConflict = OnConflictStrategy.REPLACE)
    suspend fun insert(decision: DecisionNode): Long
    
    @Insert(onConflict = OnConflictStrategy.REPLACE)
    suspend fun insertAll(decisions: List<DecisionNode>): List<Long>
    
    @Query("SELECT * FROM decisions WHERE id = :id")
    suspend fun getById(id: Long): DecisionNode?
    
    @Query("SELECT * FROM decisions WHERE projectId = :projectId ORDER BY timestamp DESC LIMIT :limit")
    suspend fun getDecisionsForProject(projectId: Long, limit: Int): List<DecisionNode>
    
    @Query("SELECT * FROM decisions WHERE projectId = :projectId AND tags LIKE '%' || :tag || '%' LIMIT :limit")
    suspend fun getDecisionsByTag(projectId: Long, tag: String, limit: Int): List<DecisionNode>
    
    @Query("SELECT * FROM decisions WHERE projectId = :projectId AND (title LIKE '%' || :query || '%' OR context LIKE '%' || :query || '%') LIMIT :limit")
    suspend fun searchDecisions(projectId: Long, query: String, limit: Int): List<DecisionNode>
    
    @Query("DELETE FROM decisions WHERE projectId = :projectId")
    suspend fun deleteByProject(projectId: Long): Int
    
    @Query("DELETE FROM decisions WHERE id = :id")
    suspend fun delete(id: Long): Int
    
    @Query("SELECT COUNT(*) FROM decisions WHERE projectId = :projectId")
    suspend fun getCountForProject(projectId: Long): Int
}