package com.alan.memory.nodes

import androidx.room.Entity
import androidx.room.Index
import androidx.room.PrimaryKey

@Entity(
    tableName = "code_tasks",
    indices = [
        Index("timestamp"),
        Index("projectId"),
        Index("providerUsed")
    ]
)
data class CodeTaskNode(
    @PrimaryKey(autoGenerate = true)
    val id: Long = 0,
    val projectId: Long,
    val userQuery: String,
    val plan: String = "",
    val steps: String = "",
    val result: String,
    val artifacts: String = "",
    val providerUsed: String? = null,
    val latencyMs: Long = 0L,
    val tokensUsed: Int = 0,
    val timestamp: Long = System.currentTimeMillis()
)