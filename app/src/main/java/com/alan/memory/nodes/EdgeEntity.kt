package com.alan.memory.nodes

import androidx.room.Entity
import androidx.room.Index
import androidx.room.PrimaryKey
import kotlinx.parcelize.Parcelize
import android.os.Parcelable

@Parcelize
@Entity(
    tableName = "edges",
    indices = [
        Index("sourceType"),
        Index("sourceId"),
        Index("targetType"),
        Index("targetId"),
        Index("type")
    ]
)
data class EdgeEntity(
    @PrimaryKey(autoGenerate = true)
    val id: Long = 0,
    val sourceType: String,
    val sourceId: Long,
    val targetType: String,
    val targetId: Long,
    val type: String,
    val weight: Float = 1.0f,
    val createdAt: Long = System.currentTimeMillis()
) : Parcelable