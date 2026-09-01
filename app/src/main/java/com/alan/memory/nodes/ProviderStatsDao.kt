package com.alan.memory.nodes

import androidx.room.Dao
import androidx.room.Insert
import androidx.room.OnConflictStrategy
import androidx.room.Query
import androidx.room.Update
import com.alan.memory.ProviderStatsEntity

@Dao
interface ProviderStatsDao {
    @Insert(onConflict = OnConflictStrategy.REPLACE)
    suspend fun insert(stats: ProviderStatsEntity): Long
    
    @Insert(onConflict = OnConflictStrategy.REPLACE)
    suspend fun insertAll(stats: List<ProviderStatsEntity>): List<Long>
    
    @Query("SELECT * FROM provider_stats WHERE name = :name")
    suspend fun getByName(name: String): ProviderStatsEntity?
    
    @Query("SELECT * FROM provider_stats ORDER BY useCount DESC LIMIT :limit")
    suspend fun getTopProviders(limit: Int): List<ProviderStatsEntity>
    
    @Query("SELECT * FROM provider_stats")
    suspend fun getAllProviders(): List<ProviderStatsEntity>
    
    @Query("UPDATE provider_stats SET useCount = useCount + 1, lastUsed = :timestamp WHERE name = :name")
    suspend fun incrementUse(name: String, timestamp: Long = System.currentTimeMillis()): Int
    
    @Query("UPDATE provider_stats SET successCount = successCount + 1, totalLatencyMs = totalLatencyMs + :latency WHERE name = :name")
    suspend fun recordSuccess(name: String, latency: Long): Int
    
    @Query("UPDATE provider_stats SET useCount = useCount + 1, lastUsed = :timestamp WHERE name = :name")
    suspend fun recordFailure(name: String, timestamp: Long = System.currentTimeMillis()): Int
    
    @Query("DELETE FROM provider_stats WHERE name = :name")
    suspend fun delete(name: String): Int
}