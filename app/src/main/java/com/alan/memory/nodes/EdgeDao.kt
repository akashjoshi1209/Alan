package com.alan.memory.nodes

import androidx.room.Dao
import androidx.room.Insert
import androidx.room.OnConflictStrategy
import androidx.room.Query
import kotlinx.coroutines.flow.Flow

@Dao
interface EdgeDao {
    @Insert(onConflict = OnConflictStrategy.IGNORE)
    suspend fun insert(edge: EdgeEntity): Long
    
    @Insert(onConflict = OnConflictStrategy.IGNORE)
    suspend fun insertAll(edges: List<EdgeEntity>): List<Long>
    
    @Query("SELECT * FROM edges WHERE sourceType = :sourceType AND sourceId = :sourceId")
    suspend fun getOutgoingEdges(sourceType: String, sourceId: Long): List<EdgeEntity>
    
    @Query("SELECT * FROM edges WHERE targetType = :targetType AND targetId = :targetId")
    suspend fun getIncomingEdges(targetType: String, targetId: Long): List<EdgeEntity>
    
    @Query("SELECT * FROM edges WHERE sourceType = :sourceType AND sourceId = :sourceId AND type = :type")
    suspend fun getEdgesByType(sourceType: String, sourceId: Long, type: String): List<EdgeEntity>
    
    @Query("SELECT * FROM edges WHERE targetType = :targetType AND targetId = :targetId AND type = :type")
    suspend fun getIncomingEdgesByType(targetType: String, targetId: Long, type: String): List<EdgeEntity>
    
    @Query("DELETE FROM edges WHERE sourceType = :sourceType AND sourceId = :sourceId")
    suspend fun deleteOutgoingEdges(sourceType: String, sourceId: Long): Int
    
    @Query("DELETE FROM edges WHERE targetType = :targetType AND targetId = :targetId")
    suspend fun deleteIncomingEdges(targetType: String, targetId: Long): Int
    
    @Query("DELETE FROM edges WHERE id = :id")
    suspend fun delete(id: Long): Int
    
    @Query("DELETE FROM edges WHERE sourceType = :sourceType AND sourceId = :sourceId AND targetType = :targetType AND targetId = :targetId")
    suspend fun deleteSpecificEdge(sourceType: String, sourceId: Long, targetType: String, targetId: Long): Int
}