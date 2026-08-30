package com.alan.memory.nodes

import androidx.room.Entity
import androidx.room.Index
import androidx.room.PrimaryKey
import kotlinx.parcelize.Parcelize
import android.os.Parcelable

@Parcelize
@Entity(
    tableName = "code_patterns",
    indices = [
        Index("projectId"),
        Index("patternType"),
        Index("confidence"),
        Index("lastUsed")
    ]
)
data class CodePatternNode(
    @PrimaryKey(autoGenerate = true)
    val id: Long = 0,
    val projectId: Long,
    val patternType: String,
    val trigger: String,
    val template: String,
    val confidence: Float = 0.0f,
    val usageCount: Int = 0,
    val successRate: Float = 0.0f,
    val tags: String = "[]",
    val createdAt: Long = System.currentTimeMillis(),
    val lastUsed: Long = System.currentTimeMillis()
) : Parcelable