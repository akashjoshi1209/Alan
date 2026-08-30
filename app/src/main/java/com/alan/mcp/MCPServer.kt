package com.alan.mcp

import kotlinx.parcelize.Parcelize
import android.os.Parcelable

@Parcelize
data class MCPServer(
    val id: String = "",
    val name: String,
    val url: String,
    val apiKey: String? = null,
    val enabled: Boolean = true
) : Parcelable