package com.alan.memory.nodes

import androidx.room.Dao
import androidx.room.Insert
import androidx.room.OnConflictStrategy
import androidx.room.Query

@Dao
interface FileNodeDao {
    @Insert(onConflict = OnConflictStrategy.REPLACE)
    suspend fun insert(file: FileNode): Long
    
    @Insert(onConflict = OnConflictStrategy.REPLACE)
    suspend fun insertAll(files: List<FileNode>): List<Long>
    
    @Query("SELECT * FROM files WHERE id = :id")
    suspend fun getById(id: Long): FileNode?
    
    @Query("SELECT * FROM files WHERE projectId = :projectId AND path = :path")
    suspend fun getByPath(projectId: Long, path: String): FileNode?
    
    @Query("SELECT * FROM files WHERE projectId = :projectId ORDER BY path LIMIT :limit")
    suspend fun getAllForProject(projectId: Long, limit: Int): List<FileNode>
    
    @Query("SELECT * FROM files WHERE projectId = :projectId AND language = :language LIMIT :limit")
    suspend fun getByLanguage(projectId: Long, language: String, limit: Int): List<FileNode>
    
    @Query("SELECT * FROM files WHERE projectId = :projectId AND hash = :hash")
    suspend fun getByHash(projectId: Long, hash: String): List<FileNode>
    
    @Query("SELECT * FROM files WHERE projectId = :projectId AND path LIKE '%' || :query || '%' LIMIT :limit")
    suspend fun searchFiles(projectId: Long, query: String, limit: Int): List<FileNode>
    
    @Query("DELETE FROM files WHERE projectId = :projectId")
    suspend fun deleteByProject(projectId: Long): Int
    
    @Query("DELETE FROM files WHERE projectId = :projectId AND path = :path")
    suspend fun deleteByPath(projectId: Long, path: String): Int
    
    @Query("SELECT COUNT(*) FROM files WHERE projectId = :projectId")
    suspend fun getCountForProject(projectId: Long): Int
}