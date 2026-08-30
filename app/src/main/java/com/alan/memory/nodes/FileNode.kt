package com.alan.memory.nodes

import androidx.room.Entity
import androidx.room.Index
import androidx.room.PrimaryKey
import kotlinx.parcelize.Parcelize
import android.os.Parcelable

@Parcelize
@Entity(
    tableName = "files",
    indices = [
        Index("projectId"),
        Index("path", unique = true),
        Index("hash"),
        Index("lastIndexed")
    ]
)
data class FileNode(
    @PrimaryKey(autoGenerate = true)
    val id: Long = 0,
    val projectId: Long,
    val path: String,
    val language: String,
    val hash: String,
    val symbols: String = "[]",
    val imports: String = "[]",
    val exports: String = "[]",
    val lastModified: Long = 0L,
    val lastIndexed: Long = System.currentTimeMillis()
) : Parcelable