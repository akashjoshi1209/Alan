package com.alan.memory.nodes

import androidx.room.Entity
import androidx.room.Index
import androidx.room.PrimaryKey
import kotlinx.parcelize.Parcelize
import android.os.Parcelable

@Parcelize
@Entity(
    tableName = "decisions",
    indices = [
        Index("projectId"),
        Index("timestamp")
    ]
)
data class DecisionNode(
    @PrimaryKey(autoGenerate = true)
    val id: Long = 0,
    val projectId: Long,
    val title: String,
    val context: String,
    val decision: String,
    val alternatives: String = "[]",
    val tags: String = "[]",
    val confidence: Float = 1.0f,
    val timestamp: Long = System.currentTimeMillis()
) : Parcelable