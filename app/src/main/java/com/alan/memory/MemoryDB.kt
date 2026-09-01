package com.alan.memory

import androidx.room.*
import androidx.room.migration.Migration
import androidx.sqlite.db.SupportSQLiteDatabase
import com.alan.memory.nodes.CodePatternNode
import com.alan.memory.nodes.CodePatternNodeDao
import com.alan.memory.nodes.CodeTaskNode
import com.alan.memory.nodes.CodeTaskNodeDao
import com.alan.memory.nodes.DecisionNode
import com.alan.memory.nodes.DecisionNodeDao
import com.alan.memory.nodes.EdgeDao
import com.alan.memory.nodes.EdgeEntity
import com.alan.memory.nodes.FileNode
import com.alan.memory.nodes.FileNodeDao
import com.alan.memory.nodes.ProjectNode
import com.alan.memory.nodes.ProjectNodeDao
import com.alan.memory.nodes.ProviderStatsDao
import com.alan.memory.nodes.SymbolNode
import com.alan.memory.nodes.SymbolNodeDao

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