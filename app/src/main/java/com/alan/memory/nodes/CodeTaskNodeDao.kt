package com.alan.memory.nodes

import androidx.room.Dao
import androidx.room.Insert
import androidx.room.OnConflictStrategy
import androidx.room.Query
import kotlinx.coroutines.flow.Flow

@Dao
interface CodeTaskNodeDao {
    @Insert(onConflict = OnConflictStrategy.REPLACE)
    suspend fun insert(task: CodeTaskNode): Long
    
    @Insert(onConflict = OnConflictStrategy.REPLACE)
    suspend fun insertAll(tasks: List<CodeTaskNode>): List<Long>
    
    @Query("SELECT * FROM code_tasks WHERE id = :id")
    suspend fun getById(id: Long): CodeTaskNode?
    
    @Query("SELECT * FROM code_tasks WHERE projectId = :projectId ORDER BY timestamp DESC LIMIT :limit")
    suspend fun getTasksForProject(projectId: Long, limit: Int): List<CodeTaskNode>
    
    @Query("SELECT * FROM code_tasks ORDER BY timestamp DESC LIMIT :limit")
    suspend fun getRecentTasks(limit: Int): List<CodeTaskNode>
    
    @Query("SELECT * FROM code_tasks WHERE userQuery LIKE '%' || :query || '%' ORDER BY timestamp DESC LIMIT :limit")
    suspend fun searchTasks(query: String, limit: Int): List<CodeTaskNode>
    
    @Query("SELECT * FROM code_tasks WHERE providerUsed = :provider ORDER BY timestamp DESC LIMIT :limit")
    suspend fun getTasksByProvider(provider: String, limit: Int): List<CodeTaskNode>
    
    @Query("SELECT * FROM code_tasks WHERE timestamp > :since ORDER BY timestamp DESC")
    suspend fun getTasksSince(since: Long): List<CodeTaskNode>
    
    @Query("SELECT COUNT(*) FROM code_tasks")
    suspend fun getTotalCount(): Int
    
    @Query("SELECT COUNT(*) FROM code_tasks WHERE projectId = :projectId")
    suspend fun getCountForProject(projectId: Long): Int
    
    @Query("DELETE FROM code_tasks WHERE projectId = :projectId")
    suspend fun deleteByProject(projectId: Long): Int
    
    @Query("DELETE FROM code_tasks WHERE id = :id")
    suspend fun delete(id: Long): Int
}