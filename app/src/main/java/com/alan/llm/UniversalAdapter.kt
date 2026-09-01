package com.alan.llm

import android.content.Context
import com.alan.llm.providers.*
import com.alan.local.ModelManager
import com.alan.security.EncryptedPrefs
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.withContext

class UniversalAdapter(private val context: Context) {
    
    private val prefs = EncryptedPrefs.getInstance(context)
    
    fun getActiveProvider(): LLMProvider {
        val name = prefs.getString("provider_name", "Groq")
        val baseUrl = prefs.getString("provider_base_url", "")
        val apiKey = prefs.getString("provider_api_key", "")
        val model = prefs.getString("provider_model", "")
        
        return when (name) {
            "Groq" -> GroqProvider(apiKey, model.ifEmpty { getDefaultModel("Groq") })
            "Google Gemini" -> GeminiProvider(apiKey, model.ifEmpty { getDefaultModel("Google Gemini") })
            "OpenRouter" -> OpenRouterProvider(apiKey, model.ifEmpty { getDefaultModel("OpenRouter") })
            "Anthropic Claude" -> AnthropicProvider(apiKey, model.ifEmpty { getDefaultModel("Anthropic Claude") })
            "OpenAI" -> OpenAIProvider(apiKey, model.ifEmpty { getDefaultModel("OpenAI") })
            "Ollama (Local)" -> OllamaProvider(baseUrl.ifEmpty { "http://localhost:11434" }, model.ifEmpty { getDefaultModel("Ollama (Local)") })
            "Local (LlamaCpp)" -> {
                val mm = ModelManager.getInstance(context)
                val tier = mm.getDownloadedTier() ?: ModelManager.ModelTier.MINIMUM
                LlamaProvider(mm.getModelPath(tier), tier.displayName)
            }
            "LM Studio" -> LMStudioProvider(baseUrl.ifEmpty { "http://localhost:1234" }, model.ifEmpty { getDefaultModel("LM Studio") })
            "Custom" -> CustomProvider(baseUrl, apiKey, model.ifEmpty { "gpt-4o-mini" })
            else -> CustomProvider(baseUrl, apiKey, model.ifEmpty { "gpt-4o-mini" })
        }
    }
    
    suspend fun complete(
        systemPrompt: String,
        userMessage: String,
        temperature: Float = 0.1f,
        maxTokens: Int = 2048
    ): Result<String> = withContext(Dispatchers.IO) {
        getActiveProvider().complete(systemPrompt, userMessage, temperature, maxTokens)
    }
    
    suspend fun completeWithTools(
        systemPrompt: String,
        userMessage: String,
        tools: List<ToolDefinition>,
        temperature: Float = 0.1f,
        maxTokens: Int = 2048
    ): Result<ToolCallResult> = withContext(Dispatchers.IO) {
        getActiveProvider().completeWithTools(systemPrompt, userMessage, tools, temperature, maxTokens)
    }
    
    suspend fun streamComplete(
        systemPrompt: String,
        userMessage: String,
        onChunk: (String) -> Unit,
        temperature: Float = 0.1f,
        maxTokens: Int = 2048
    ): Result<String> = withContext(Dispatchers.IO) {
        getActiveProvider().streamComplete(systemPrompt, userMessage, onChunk, temperature, maxTokens)
    }
    
    suspend fun testConnection(): Result<Long> = withContext(Dispatchers.IO) {
        getActiveProvider().testConnection()
    }
    
    fun saveSettings(name: String, baseUrl: String, apiKey: String, model: String) {
        prefs.putString("provider_name", name)
        prefs.putString("provider_base_url", baseUrl)
        prefs.putString("provider_api_key", apiKey)
        prefs.putString("provider_model", model)
    }
    
    fun getProviderName(): String = prefs.getString("provider_name", "Groq") ?: "Groq"
    fun getProviderBaseUrl(): String = prefs.getString("provider_base_url", "")
    fun getProviderApiKey(): String = prefs.getString("provider_api_key", "")
    fun getProviderModel(): String = prefs.getString("provider_model", "")
    
    companion object {
        val AVAILABLE_PROVIDERS = listOf(
            "Groq",
            "Google Gemini",
            "OpenRouter",
            "Anthropic Claude",
            "OpenAI",
            "Ollama (Local)",
            "Local (LlamaCpp)",
            "LM Studio",
            "Custom"
        )
        
        val PROVIDERS_WITH_MODELS = mapOf(
            "Groq" to listOf(
                "llama-3.3-70b-versatile",
                "llama-3.1-70b-versatile",
                "llama-3.1-8b-instant",
                "mixtral-8x7b-32768",
                "gemma2-9b-it"
            ),
            "Google Gemini" to listOf(
                "gemini-2.0-flash-exp",
                "gemini-1.5-pro",
                "gemini-1.5-flash",
                "gemini-1.5-flash-8b"
            ),
            "OpenRouter" to listOf(
                "anthropic/claude-3.5-sonnet",
                "anthropic/claude-3.5-haiku",
                "openai/gpt-4o",
                "openai/gpt-4o-mini",
                "meta-llama/llama-3.3-70b-instruct",
                "google/gemini-2.0-flash-exp:free",
                "meta-llama/llama-3.1-8b-instruct:free"
            ),
            "Anthropic Claude" to listOf(
                "claude-3-5-sonnet-20241022",
                "claude-3-5-haiku-20241022",
                "claude-3-opus-20240229",
                "claude-3-sonnet-20240229",
                "claude-3-haiku-20240307"
            ),
            "OpenAI" to listOf(
                "gpt-4o",
                "gpt-4o-mini",
                "gpt-4-turbo",
                "gpt-3.5-turbo"
            ),
            "Ollama (Local)" to listOf(
                "llama3.3",
                "llama3.2",
                "llama3.1",
                "codellama",
                "phi3.5",
                "gemma2"
            ),
            "LM Studio" to listOf(
                "local-model"
            )
        )
        
        fun getDefaultModel(provider: String): String = when (provider) {
            "Groq" -> "llama-3.3-70b-versatile"
            "Google Gemini" -> "gemini-2.0-flash-exp"
            "OpenRouter" -> "anthropic/claude-3.5-sonnet"
            "Anthropic Claude" -> "claude-3-5-sonnet-20241022"
            "OpenAI" -> "gpt-4o-mini"
            "Ollama (Local)" -> "llama3.3"
            "Local (LlamaCpp)" -> "Phi-3 Mini"
            "LM Studio" -> "local-model"
            else -> "gpt-4o-mini"
        }
        
        fun getAvailableModels(provider: String): List<String> {
            return PROVIDERS_WITH_MODELS[provider] ?: listOf(getDefaultModel(provider))
        }
        
        fun supportsStreaming(provider: String): Boolean = when (provider) {
            "Groq", "Google Gemini", "OpenRouter", "Anthropic Claude", 
            "OpenAI", "Ollama (Local)", "LM Studio", "Custom" -> true
            "Local (LlamaCpp)" -> false // Local llama.cpp server may not support streaming
            else -> false
        }
        
        fun supportsTools(provider: String): Boolean = when (provider) {
            "OpenRouter", "Anthropic Claude", "OpenAI", "Ollama (Local)", "LM Studio" -> true
            "Groq", "Google Gemini", "Local (LlamaCpp)", "Custom" -> false
            else -> false
        }
    }
}