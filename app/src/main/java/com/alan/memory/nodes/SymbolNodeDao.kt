package com.alan.memory.nodes

import androidx.room.Dao
import androidx.room.Insert
import androidx.room.OnConflictStrategy
import androidx.room.Query
import kotlinx.coroutines.flow.Flow

@Dao
interface SymbolNodeDao {
    @Insert(onConflict = OnConflictStrategy.REPLACE)
    suspend fun insert(symbol: SymbolNode): Long
    
    @Insert(onConflict = OnConflictStrategy.REPLACE)
    suspend fun insertAll(symbols: List<SymbolNode>): List<Long>
    
    @Query("SELECT * FROM symbols WHERE id = :id")
    suspend fun getById(id: Long): SymbolNode?
    
    @Query("SELECT * FROM symbols WHERE projectId = :projectId AND name = :name")
    suspend fun getByName(projectId: Long, name: String): List<SymbolNode>
    
    @Query("SELECT * FROM symbols WHERE projectId = :projectId AND filePath = :filePath")
    suspend fun getByFilePath(projectId: Long, filePath: String): List<SymbolNode>
    
    @Query("SELECT * FROM symbols WHERE projectId = :projectId AND type = :type")
    suspend fun getByType(projectId: Long, type: String): List<SymbolNode>
    
    @Query("SELECT * FROM symbols WHERE projectId = :projectId ORDER BY name LIMIT :limit")
    suspend fun getAllForProject(projectId: Long, limit: Int): List<SymbolNode>
    
    @Query("SELECT * FROM symbols WHERE projectId = :projectId AND name LIKE '%' || :query || '%' LIMIT :limit")
    suspend fun searchSymbols(projectId: Long, query: String, limit: Int): List<SymbolNode>
    
    @Query("DELETE FROM symbols WHERE projectId = :projectId")
    suspend fun deleteByProject(projectId: Long): Int
    
    @Query("DELETE FROM symbols WHERE projectId = :projectId AND filePath = :filePath")
    suspend fun deleteByFilePath(projectId: Long, filePath: String): Int
    
    @Query("SELECT COUNT(*) FROM symbols WHERE projectId = :projectId")
    suspend fun getCountForProject(projectId: Long): Int
}