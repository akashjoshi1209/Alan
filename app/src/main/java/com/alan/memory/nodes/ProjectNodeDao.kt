package com.alan.memory.nodes

import androidx.room.Dao
import androidx.room.Insert
import androidx.room.OnConflictStrategy
import androidx.room.Query
import kotlinx.coroutines.flow.Flow

@Dao
interface ProjectNodeDao {
    @Insert(onConflict = OnConflictStrategy.REPLACE)
    suspend fun insert(project: ProjectNode): Long
    
    @Insert(onConflict = OnConflictStrategy.REPLACE)
    suspend fun insertAll(projects: List<ProjectNode>): List<Long>
    
    @Query("SELECT * FROM projects WHERE id = :id")
    suspend fun getById(id: Long): ProjectNode?
    
    @Query("SELECT * FROM projects WHERE path = :path")
    suspend fun getByPath(path: String): ProjectNode?
    
    @Query("SELECT * FROM projects ORDER BY lastOpened DESC LIMIT :limit")
    suspend fun getRecentProjects(limit: Int): List<ProjectNode>
    
    @Query("SELECT * FROM projects ORDER BY createdAt DESC")
    suspend fun getAllProjects(): List<ProjectNode>
    
    @Query("UPDATE projects SET lastOpened = :lastOpened WHERE id = :id")
    suspend fun updateLastOpened(id: Long, lastOpened: Long): Int
    
    @Query("UPDATE projects SET name = :name, language = :language, framework = :framework, gitRemote = :gitRemote WHERE id = :id")
    suspend fun updateProject(id: Long, name: String, language: String, framework: String?, gitRemote: String?): Int
    
    @Query("DELETE FROM projects WHERE id = :id")
    suspend fun delete(id: Long): Int
    
    @Query("SELECT COUNT(*) FROM projects")
    suspend fun getCount(): Int
}