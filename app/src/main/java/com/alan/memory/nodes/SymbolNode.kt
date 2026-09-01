package com.alan.memory.nodes

import androidx.room.Entity
import androidx.room.Index
import androidx.room.PrimaryKey

@Entity(
    tableName = "symbols",
    indices = [
        Index("projectId"),
        Index("name"),
        Index("filePath"),
        Index("type")
    ]
)
data class SymbolNode(
    @PrimaryKey(autoGenerate = true)
    val id: Long = 0,
    val projectId: Long,
    val name: String,
    val type: String,
    val filePath: String,
    val lineStart: Int,
    val lineEnd: Int,
    val signature: String,
    val docstring: String? = null,
    val calls: String = "[]",
    val calledBy: String = "[]",
    val lastIndexed: Long = System.currentTimeMillis()
)