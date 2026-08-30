package com.alan

import android.app.Application
import com.alan.local.ModelManager
import com.alan.memory.MemoryDB

class AlanApplication : Application() {
    
    override fun onCreate() {
        super.onCreate()
        
        // Initialize databases
        MemoryDB.getInstance(this)
        
        // Initialize ModelManager
        ModelManager.getInstance(this)
    }
}