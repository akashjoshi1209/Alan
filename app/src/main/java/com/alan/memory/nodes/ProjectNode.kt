package com.alan.memory.nodes

import androidx.room.Entity
import androidx.room.Index
import androidx.room.PrimaryKey
import kotlinx.parcelize.Parcelize
import android.os.Parcelable

@Parcelize
@Entity(
    tableName = "projects",
    indices = [
        Index("path", unique = true),
        Index("lastOpened")
    ]
)
data class ProjectNode(
    @PrimaryKey(autoGenerate = true)
    val id: Long = 0,
    val path: String,
    val name: String,
    val language: String = "",
    val framework: String? = null,
    val gitRemote: String? = null,
    val lastOpened: Long = System.currentTimeMillis(),
    val createdAt: Long = System.currentTimeMillis()
) : Parcelable