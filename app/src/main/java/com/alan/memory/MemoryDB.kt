package com.alan.memory

import androidx.room.*
import androidx.room.migration.Migration
import androidx.sqlite.db.SupportSQLiteDatabase

@Database(
    entities = [
        ProjectNode::class,
        CodeTaskNode::class,
        SymbolNode::class,
        CodePatternNode::class,
        DecisionNode::class,
        FileNode::class,
        EdgeEntity::class,
        ProviderStatsEntity::class
    ],
    version = 1,
    exportSchema = false
)
abstract class MemoryDB : RoomDatabase() {
    abstract fun projectNodeDao(): ProjectNodeDao
    abstract fun codeTaskNodeDao(): CodeTaskNodeDao
    abstract fun symbolNodeDao(): SymbolNodeDao
    abstract fun codePatternNodeDao(): CodePatternNodeDao
    abstract fun decisionNodeDao(): DecisionNodeDao
    abstract fun fileNodeDao(): FileNodeDao
    abstract fun edgeDao(): EdgeDao
    abstract fun providerStatsDao(): ProviderStatsDao
    
    companion object {
        @Volatile
        private var INSTANCE: MemoryDB? = null
        
        fun getInstance(context: android.content.Context): MemoryDB {
            return INSTANCE ?: synchronized(this) {
                val instance = Room.databaseBuilder(
                    context.applicationContext,
                    MemoryDB::class.java,
                    "alan_memory.db"
                )
                    .fallbackToDestructiveMigration()
                    .build()
                INSTANCE = instance
                instance
            }
        }
        
        fun resetForTesting() {
            INSTANCE = null
        }
    }
}

@Entity(tableName = "provider_stats")
data class ProviderStatsEntity(
    @PrimaryKey val name: String,
    val useCount: Int = 0,
    val successCount: Int = 0,
    val totalLatencyMs: Long = 0L,
    val lastUsed: Long = 0L
)