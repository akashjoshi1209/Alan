package com.alan.ui

import android.os.Bundle
import androidx.activity.ComponentActivity
import androidx.activity.compose.setContent
import androidx.activity.enableEdgeToEdge
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.runtime.*
import androidx.compose.ui.Modifier
import androidx.compose.ui.platform.LocalContext
import androidx.core.view.WindowCompat
import com.alan.agent.AlanCore
import com.alan.agent.AlanState
import com.alan.memory.MemoryRepository
import com.alan.ui.chat.ChatScreen
import com.alan.ui.settings.SettingsScreen
import com.alan.ui.theme.AlanTheme

class MainActivity : ComponentActivity() {
    
    private lateinit var memoryRepo: MemoryRepository
    private lateinit var alanCore: AlanCore
    
    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        
        enableEdgeToEdge()
        WindowCompat.setDecorFitsSystemWindows(window, false)
        
        memoryRepo = MemoryRepository(this)
        alanCore = AlanCore(this)
        
        setContent {
            AlanTheme {
                val context = LocalContext.current
                
                val alanState by alanCore.state.collectAsState()
                
                ChatScreen(
                    alanCore = alanCore,
                    memoryRepo = memoryRepo,
                    modifier = Modifier.fillMaxSize()
                )
            }
        }
    }
    
    override fun onDestroy() {
        super.onDestroy()
        alanCore.shutdown()
    }
}