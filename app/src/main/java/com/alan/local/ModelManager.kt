package com.alan.local

import android.app.ActivityManager
import android.content.Context
import android.util.Log
import kotlinx.coroutines.*
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import okhttp3.OkHttpClient
import okhttp3.Request
import java.io.File
import java.util.concurrent.TimeUnit

class ModelManager(private val context: Context) {
    
    private val scope = CoroutineScope(Dispatchers.IO + SupervisorJob())
    private var unloadJob: Job? = null
    private var downloadJob: Job? = null
    
    private val _state = MutableStateFlow<ModelState>(ModelState.Unloaded)
    val state: StateFlow<ModelState> = _state
    
    private val modelDir: File
        get() = File(context.filesDir, "models").also { it.mkdirs() }
    
    private val client = OkHttpClient.Builder()
        .connectTimeout(30, TimeUnit.SECONDS)
        .readTimeout(300, TimeUnit.SECONDS)
        .build()
    
    fun getModelPath(tier: ModelTier): String {
        return File(modelDir, tier.fileName).absolutePath
    }
    
    fun isModelDownloaded(tier: ModelTier): Boolean {
        val file = File(modelDir, tier.fileName)
        return file.exists() && file.length() >= tier.minSize
    }
    
    fun getDownloadedTier(): ModelTier? {
        return ModelTier.entries.find { isModelDownloaded(it) }
    }
    
    fun canLoadModel(tier: ModelTier): Boolean {
        if (!isModelDownloaded(tier)) return false
        
        val activityManager = context.getSystemService(Context.ACTIVITY_SERVICE) as ActivityManager
        val memInfo = ActivityManager.MemoryInfo()
        activityManager.getMemoryInfo(memInfo)
        
        val availableMB = memInfo.availMem / (1024 * 1024)
        val requiredMB = tier.ramRequiredMB
        
        return availableMB > (requiredMB + 500)
    }
    
    suspend fun loadModel(tier: ModelTier): Result<Unit> = withContext(Dispatchers.IO) {
        runCatching {
            if (!canLoadModel(tier)) {
                throw Exception("Not enough RAM. Need ${tier.ramRequiredMB}MB free.")
            }
            
            _state.value = ModelState.Loading(tier)
            
            resetUnloadTimer()
            
            // In a real implementation, this would start the llama.cpp server
            // For now, we just mark as loaded
            _state.value = ModelState.Loaded(tier)
        }
    }
    
    fun unloadModel() {
        unloadJob?.cancel()
        
        scope.launch {
            _state.value = ModelState.Unloading
            
            delay(500)
            
            _state.value = ModelState.Unloaded
        }
    }
    
    private fun resetUnloadTimer() {
        unloadJob?.cancel()
        unloadJob = scope.launch {
            delay(UNLOAD_TIMEOUT_MS)
            unloadModel()
        }
    }
    
    suspend fun downloadModel(
        tier: ModelTier,
        onProgress: (Float) -> Unit
    ): Result<Long> = withContext(Dispatchers.IO) {
        runCatching {
            if (downloadJob?.isActive == true) {
                throw Exception("Download already in progress")
            }
            
            _state.value = ModelState.Downloading(tier, 0f)
            
            val file = File(modelDir, tier.fileName)
            val existingSize = if (file.exists()) file.length() else 0L
            
            val request = Request.Builder()
                .url(tier.downloadUrl)
                .apply {
                    if (existingSize > 0) {
                        addHeader("Range", "bytes=$existingSize-")
                    }
                }
                .build()
            
            val response = client.newCall(request).execute()
            
            if (!response.isSuccessful && response.code != 206 && response.code != 200) {
                throw Exception("Download failed: HTTP ${response.code}")
            }
            
            val totalSize = response.header("Content-Length")?.toLongOrNull()?.let { existingSize + it } 
                ?: tier.minSize
            val body = response.body ?: throw Exception("Empty response")
            
            body.byteStream().use { input ->
                file.outputStream().let { output ->
                    if (existingSize > 0) {
                        output.channel.position(existingSize)
                    }
                    
                    val buffer = ByteArray(8192)
                    var downloaded = existingSize
                    var lastProgress = 0f
                    
                    while (true) {
                        val bytes = input.read(buffer)
                        if (bytes <= 0) break
                        
                        output.write(buffer, 0, bytes)
                        downloaded += bytes
                        
                        val progress = downloaded.toFloat() / totalSize
                        if (progress - lastProgress > 0.01f) {
                            _state.value = ModelState.Downloading(tier, progress)
                            onProgress(progress)
                            lastProgress = progress
                        }
                    }
                }
            }
            
            _state.value = ModelState.Downloaded(tier)
            
            totalSize
        }
    }
    
    enum class ModelTier(
        val displayName: String,
        val fileName: String,
        val downloadUrl: String,
        val minSize: Long,
        val ramRequiredMB: Int,
        val description: String
    ) {
        MINIMUM(
            "Gemma 2B Q5",
            "gemma-2-2b-it-q5_k_m.gguf",
            "https://huggingface.co/bartowski/gemma-2-2b-it-GGUF/resolve/main/gemma-2-2b-it-q5_k_m.gguf",
            1_500_000_000,
            1500,
            "Fast, 1.5GB, good for daily tasks"
        ),
        BALANCED(
            "Phi-3.5 Mini Q4",
            "phi-3.5-mini-instruct-q4_k_m.gguf",
            "https://huggingface.co/microsoft/Phi-3.5-mini-instruct-GGUF/resolve/main/Phi-3.5-mini-instruct-q4_k_m.gguf",
            2_200_000_000,
            2000,
            "Balanced, 2.2GB, best accuracy"
        ),
        POWER(
            "Llama 3.2 3B Q5",
            "llama-3.2-3b-instruct-q5_k_m.gguf",
            "https://huggingface.co/bartowski/Llama-3.2-3B-Instruct-GGUF/resolve/main/Llama-3.2-3B-Instruct-Q5_K_M.gguf",
            2_000_000_000,
            2200,
            "Powerful, 2.0GB, complex reasoning"
        );
        
        companion object {
            fun fromName(name: String): ModelTier? {
                return entries.find { it.displayName.equals(name, ignoreCase = true) }
            }
        }
    }
    
    sealed class ModelState {
        object Unloaded : ModelState()
        data class Loading(val tier: ModelTier) : ModelState()
        data class Loaded(val tier: ModelTier) : ModelState()
        object Unloading : ModelState()
        data class Downloading(val tier: ModelTier, val progress: Float) : ModelState()
        data class Downloaded(val tier: ModelTier) : ModelState()
    }
    
    companion object {
        private const val UNLOAD_TIMEOUT_MS = 5 * 60 * 1000L
        
        @Volatile
        private var INSTANCE: ModelManager? = null
        
        fun getInstance(context: Context): ModelManager {
            return INSTANCE ?: synchronized(this) {
                INSTANCE ?: ModelManager(context.applicationContext).also { INSTANCE = it }
            }
        }
    }
}