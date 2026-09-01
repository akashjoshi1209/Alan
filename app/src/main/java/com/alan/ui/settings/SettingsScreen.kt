package com.alan.ui.settings

import android.content.Context
import androidx.compose.animation.AnimatedVisibility
import androidx.compose.animation.animateColorAsState
import androidx.compose.animation.core.animateFloatAsState
import androidx.compose.animation.core.spring
import androidx.compose.animation.core.tween
import androidx.compose.animation.expandVertically
import androidx.compose.animation.fadeIn
import androidx.compose.animation.fadeOut
import androidx.compose.animation.shrinkVertically
import androidx.compose.animation.slideInVertically
import androidx.compose.animation.slideOutVertically
import androidx.compose.foundation.BorderStroke
import androidx.compose.foundation.background
import androidx.compose.foundation.clickable
import androidx.compose.foundation.layout.*
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.foundation.text.BasicTextField
import androidx.compose.foundation.verticalScroll
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.filled.Check
import androidx.compose.material.icons.filled.KeyboardArrowLeft
import androidx.compose.material.icons.filled.KeyboardArrowRight
import androidx.compose.material3.*
import androidx.compose.runtime.*
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.clip
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.graphics.graphicsLayer
import androidx.compose.ui.text.SpanStyle
import androidx.compose.ui.text.TextStyle
import androidx.compose.ui.text.buildAnnotatedString
import androidx.compose.ui.text.font.FontFamily
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.input.PasswordVisualTransformation
import androidx.compose.ui.text.input.VisualTransformation
import androidx.compose.ui.text.withStyle
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import com.alan.llm.UniversalAdapter
import com.alan.ui.theme.AlanColors

@Composable
fun SettingsScreen(
    onNavigateBack: () -> Unit,
    onSaveProvider: (String, String, String, String) -> Unit,
    modifier: Modifier = Modifier
) {
    var selectedProvider by remember { mutableStateOf("Groq") }
    var baseUrl by remember { mutableStateOf("") }
    var apiKey by remember { mutableStateOf("") }
    var model by remember { mutableStateOf("llama-3.3-70b-versatile") }
    var showBaseUrlField by remember { mutableStateOf(false) }
    var showProvidersDropdown by remember { mutableStateOf(false) }
    var showPassword by remember { mutableStateOf(false) }
    var lastSaved by remember { mutableStateOf(0L) }
    var savedVisible by remember { mutableStateOf(false) }
    var useLocalModel by remember { mutableStateOf(false) }
    var localModelTier by remember { mutableStateOf("BALANCED") }
    
    LaunchedEffect(selectedProvider) {
        showBaseUrlField = selectedProvider == "Custom" || selectedProvider == "Ollama (Local)" || selectedProvider == "LM Studio"
    }
    
    val scrollState = rememberScrollState()
    
    Box(
        modifier = modifier
            .fillMaxSize()
            .background(AlanColors.Void950)
    ) {
        Column(
            modifier = Modifier
                .fillMaxSize()
                .statusBarsPadding()
        ) {
            SettingsHeader(onNavigateBack = onNavigateBack)
            
            Column(
                modifier = Modifier
                    .fillMaxSize()
                    .verticalScroll(scrollState)
                    .padding(horizontal = 24.dp, vertical = 16.dp)
            ) {
                SectionLabel("AI PROVIDER")
                
                Spacer(modifier = Modifier.height(12.dp))
                
                ProviderSelectorCard(
                    selectedProvider = selectedProvider,
                    isExpanded = showProvidersDropdown,
                    onToggle = { showProvidersDropdown = !showProvidersDropdown },
                    onSelect = { provider ->
                        selectedProvider = provider
                        showProvidersDropdown = false
                    }
                )
                
                Spacer(modifier = Modifier.height(16.dp))
                
                FloatingLabelTextField(
                    value = baseUrl,
                    onValueChange = { baseUrl = it },
                    label = "Base URL",
                    isFocused = showBaseUrlField,
                    visible = showBaseUrlField
                )
                
                FloatingLabelTextField(
                    value = apiKey,
                    onValueChange = { apiKey = it },
                    label = "API Key",
                    isFocused = false,
                    isPassword = !showPassword,
                    onTogglePassword = { showPassword = !showPassword }
                )
                
                FloatingLabelTextField(
                    value = model,
                    onValueChange = { model = it },
                    label = "Model",
                    isFocused = false
                )
                
                Text(
                    text = "Works with any OpenAI-compatible API",
                    style = TextStyle(
                        fontFamily = FontFamily.Default,
                        fontWeight = FontWeight(400),
                        fontSize = 11.sp,
                        color = AlanColors.TextDisabled
                    )
                )
                
                Spacer(modifier = Modifier.height(24.dp))
                
                SectionLabel("LOCAL MODELS")
                
                Spacer(modifier = Modifier.height(12.dp))
                
                Surface(
                    modifier = Modifier.fillMaxWidth(),
                    shape = RoundedCornerShape(16.dp),
                    color = AlanColors.Void900,
                    border = BorderStroke(1.dp, AlanColors.BorderSubtle)
                ) {
                    Column(modifier = Modifier.padding(16.dp)) {
                        Row(
                            modifier = Modifier.fillMaxWidth(),
                            horizontalArrangement = Arrangement.SpaceBetween,
                            verticalAlignment = Alignment.CenterVertically
                        ) {
                            Column {
                                Text("Use Local Model", style = TextStyle(fontWeight = FontWeight(500), fontSize = 14.sp, color = AlanColors.TextPrimary))
                                Text("Run models on-device via llama.cpp", style = TextStyle(fontWeight = FontWeight(400), fontSize = 11.sp, color = AlanColors.TextDisabled))
                            }
                            Switch(
                                checked = useLocalModel,
                                onCheckedChange = { useLocalModel = !useLocalModel },
                                colors = SwitchDefaults.colors(
                                    checkedThumbColor = AlanColors.TextPrimary,
                                    checkedTrackColor = AlanColors.Violet,
                                    uncheckedThumbColor = AlanColors.TextDisabled,
                                    uncheckedTrackColor = AlanColors.Void600
                                )
                            )
                        }
                        
                        if (useLocalModel) {
                            Spacer(modifier = Modifier.height(16.dp))
                            Text("Model Tier", style = TextStyle(fontWeight = FontWeight(500), fontSize = 14.sp, color = AlanColors.TextSecondary))
                            Spacer(modifier = Modifier.height(8.dp))
                            Row {
                                listOf("MINIMUM", "BALANCED", "POWER").forEach { tier ->
                                    val isSelected = localModelTier == tier
                                    val colors = mapOf(
                                        "MINIMUM" to AlanColors.Green,
                                        "BALANCED" to AlanColors.Cyan,
                                        "POWER" to AlanColors.Violet
                                    )
                                    
                                    Surface(
                                        modifier = Modifier
                                            .weight(1f)
                                            .height(48.dp)
                                            .padding(horizontal = 4.dp)
                                            .clickable { localModelTier = tier },
                                        shape = RoundedCornerShape(12.dp),
                                        color = if (isSelected) colors[tier]!!.copy(alpha = 0.2f) else AlanColors.Void800,
                                        border = BorderStroke(1.dp, if (isSelected) colors[tier]!! else AlanColors.BorderSubtle)
                                    ) {
                                        Column(
                                            modifier = Modifier.fillMaxSize(),
                                            horizontalAlignment = Alignment.CenterHorizontally,
                                            verticalArrangement = Arrangement.Center
                                        ) {
                                            Text(tier, style = TextStyle(fontWeight = FontWeight(500), fontSize = 12.sp, color = if (isSelected) colors[tier]!! else AlanColors.TextPrimary))
                                            Text(
                                                when (tier) {
                                                    "MINIMUM" -> "Gemma 2B (1.5GB)"
                                                    "BALANCED" -> "Phi-3.5 Mini (2.2GB)"
                                                    "POWER" -> "Llama 3.2 3B (2.0GB)"
                                                    else -> ""
                                                },
                                                style = TextStyle(fontWeight = FontWeight(400), fontSize = 10.sp, color = AlanColors.TextDisabled)
                                            )
                                        }
                                    }
                                }
                            }
                        }
                    }
                }
                
                Spacer(modifier = Modifier.height(24.dp))
                
                TestConnectionButton(
                    apiKey = apiKey,
                    baseUrl = baseUrl,
                    model = model,
                    onSave = { name, url, key, mdl ->
                        onSaveProvider(name, url, key, mdl)
                        lastSaved = System.currentTimeMillis()
                        savedVisible = true
                    }
                )
                
                Spacer(modifier = Modifier.height(32.dp))
                
                SectionLabel("MEMORY")
                
                Spacer(modifier = Modifier.height(12.dp))
                
                Surface(
                    modifier = Modifier.fillMaxWidth(),
                    shape = RoundedCornerShape(16.dp),
                    color = AlanColors.Void900,
                    border = BorderStroke(1.dp, AlanColors.BorderSubtle)
                ) {
                    Column {
                        SettingsRow(
                            title = "Export Memory",
                            subtitle = "Save all memories to encrypted file",
                            actionText = "Export",
                            onAction = { /* TODO: Implement export */ }
                        )
                        Divider(thickness = 1.dp, color = AlanColors.BorderSubtle, modifier = Modifier.padding(horizontal = 16.dp))
                        SettingsRow(
                            title = "Import Memory",
                            subtitle = "Load memories from backup file",
                            actionText = "Import",
                            onAction = { /* TODO: Implement import */ }
                        )
                        Divider(thickness = 1.dp, color = AlanColors.BorderSubtle, modifier = Modifier.padding(horizontal = 16.dp))
                        SettingsRow(
                            title = "Clear All Memory",
                            subtitle = "Permanently delete all stored memories",
                            actionText = "Clear",
                            actionColor = AlanColors.Red,
                            onAction = { /* TODO: Implement clear with confirmation */ }
                        )
                    }
                }
                
                Spacer(modifier = Modifier.height(32.dp))
                
                SectionLabel("ABOUT")
                
                Spacer(modifier = Modifier.height(12.dp))
                
                AboutSection()
                
                Spacer(modifier = Modifier.height(32.dp))
            }
            
            Box(
                modifier = Modifier.fillMaxWidth(),
                contentAlignment = Alignment.BottomCenter
            ) {
                SavedToast(
                    visible = savedVisible,
                    onDismiss = { savedVisible = false },
                    modifier = Modifier
                        .navigationBarsPadding()
                        .padding(16.dp)
                )
            }
        }
    }
}

@Composable
private fun SettingsHeader(onNavigateBack: () -> Unit) {
    Row(
        modifier = Modifier
            .fillMaxWidth()
            .height(88.dp)
            .padding(horizontal = 24.dp),
        verticalAlignment = Alignment.CenterVertically
    ) {
        IconButton(onClick = onNavigateBack) {
            Icon(
                imageVector = Icons.Default.KeyboardArrowLeft,
                contentDescription = "Back",
                tint = AlanColors.TextSecondary
            )
        }
        
        Text(
            text = "Settings",
            style = TextStyle(
                fontFamily = FontFamily.Default,
                fontWeight = FontWeight(600),
                fontSize = 20.sp,
                color = AlanColors.TextPrimary
            )
        )
    }
    
    Divider(
        modifier = Modifier.fillMaxWidth(),
        thickness = 1.dp,
        color = AlanColors.BorderSubtle
    )
}

@Composable
private fun SectionLabel(text: String) {
    Text(
        text = text,
        style = TextStyle(
            fontFamily = FontFamily.Default,
            fontWeight = FontWeight(600),
            fontSize = 10.sp,
            letterSpacing = 3.sp,
            color = AlanColors.TextDisabled
        )
    )
}

@Composable
private fun ProviderSelectorCard(
    selectedProvider: String,
    isExpanded: Boolean,
    onToggle: () -> Unit,
    onSelect: (String) -> Unit
) {
    val borderColor by animateColorAsState(
        targetValue = if (isExpanded) AlanColors.BorderGlow else AlanColors.BorderSubtle,
        animationSpec = tween(200),
        label = "border"
    )
    
    Surface(
        modifier = Modifier
            .fillMaxWidth()
            .height(56.dp)
            .clickable(onClick = onToggle),
        shape = RoundedCornerShape(16.dp),
        color = AlanColors.Void900,
        border = BorderStroke(1.dp, borderColor)
    ) {
        Row(
            modifier = Modifier
                .fillMaxSize()
                .padding(horizontal = 16.dp),
            verticalAlignment = Alignment.CenterVertically
        ) {
            ProviderDot(provider = selectedProvider)
            
            Spacer(modifier = Modifier.width(12.dp))
            
            Text(
                text = selectedProvider,
                style = TextStyle(
                    fontFamily = FontFamily.Default,
                    fontWeight = FontWeight(500),
                    fontSize = 14.sp,
                    color = AlanColors.TextPrimary
                ),
                modifier = Modifier.weight(1f)
            )
            
            val rotationAngle by animateFloatAsState(
                targetValue = if (isExpanded) 180f else 0f,
                animationSpec = tween(200),
                label = "rotation"
            )
            
            Icon(
                imageVector = Icons.Default.KeyboardArrowRight,
                contentDescription = null,
                tint = AlanColors.TextDisabled,
                modifier = Modifier.graphicsLayer(rotationZ = rotationAngle)
            )
        }
    }
    
    AnimatedVisibility(
        visible = isExpanded,
        enter = expandVertically() + fadeIn(animationSpec = tween(200)),
        exit = shrinkVertically() + fadeOut(animationSpec = tween(200))
    ) {
        Surface(
            modifier = Modifier.fillMaxWidth(),
            shape = RoundedCornerShape(12.dp),
            color = AlanColors.Void800,
            border = BorderStroke(1.dp, AlanColors.BorderSubtle)
        ) {
            Column {
                UniversalAdapter.AVAILABLE_PROVIDERS.forEach { provider ->
                    ProviderOption(
                        label = provider,
                        isSelected = provider == selectedProvider,
                        onClick = { onSelect(provider) }
                    )
                }
            }
        }
    }
}

@Composable
private fun ProviderDot(provider: String) {
    val colors = mapOf(
        "Groq" to AlanColors.Violet,
        "Google Gemini" to AlanColors.Cyan,
        "OpenRouter" to AlanColors.Green,
        "Anthropic Claude" to AlanColors.Amber,
        "OpenAI" to AlanColors.Green,
        "Ollama (Local)" to AlanColors.Cyan,
        "Local (LlamaCpp)" to AlanColors.Violet,
        "LM Studio" to AlanColors.Violet,
        "Custom" to AlanColors.Violet
    )
    
    Box(
        modifier = Modifier
            .size(8.dp)
            .clip(RoundedCornerShape(4.dp))
            .background(colors[provider] ?: AlanColors.Violet)
    )
}

@Composable
private fun ProviderOption(
    label: String,
    isSelected: Boolean,
    onClick: () -> Unit
) {
    Row(
        modifier = Modifier
            .fillMaxWidth()
            .clickable(onClick = onClick)
            .padding(16.dp),
        verticalAlignment = Alignment.CenterVertically
    ) {
        if (isSelected) {
            Box(
                modifier = Modifier
                    .width(3.dp)
                    .height(16.dp)
                    .background(AlanColors.Violet)
            )
            
            Spacer(modifier = Modifier.width(12.dp))
        } else {
            Spacer(modifier = Modifier.width(15.dp))
        }
        
        Text(
            text = label,
            style = TextStyle(
                fontFamily = FontFamily.Default,
                fontWeight = FontWeight(500),
                fontSize = 14.sp,
                color = if (isSelected) AlanColors.Violet else AlanColors.TextPrimary
            )
        )
    }
}

@Composable
fun FloatingLabelTextField(
    value: String,
    onValueChange: (String) -> Unit,
    label: String,
    isFocused: Boolean,
    isPassword: Boolean = false,
    visible: Boolean = true,
    onTogglePassword: (() -> Unit)? = null
) {
    var showPassword by remember { mutableStateOf(false) }
    
    val labelOffset by animateFloatAsState(
        targetValue = if (isFocused || value.isNotEmpty()) -20f else 0f,
        animationSpec = spring(stiffness = 300f, dampingRatio = 0.75f),
        label = "offset"
    )
    
    val labelScale by animateFloatAsState(
        targetValue = if (isFocused || value.isNotEmpty()) 0.75f else 1f,
        animationSpec = spring(stiffness = 300f, dampingRatio = 0.75f),
        label = "scale"
    )
    
    val borderColor by animateColorAsState(
        targetValue = if (isFocused) AlanColors.Violet else AlanColors.BorderSubtle,
        animationSpec = tween(200),
        label = "border"
    )
    
    if (!visible) return
    
    Surface(
        modifier = Modifier
            .fillMaxWidth()
            .height(56.dp),
        shape = RoundedCornerShape(14.dp),
        color = AlanColors.Void900,
        border = BorderStroke(1.dp, borderColor)
    ) {
        Box(modifier = Modifier.fillMaxSize()) {
            Text(
                text = label,
                style = TextStyle(
                    fontFamily = FontFamily.Default,
                    fontWeight = FontWeight(400),
                    fontSize = 14.sp,
                    color = AlanColors.TextSecondary
                ),
                modifier = Modifier.graphicsLayer {
                    translationY = labelOffset.dp.toPx()
                    scaleX = labelScale
                    scaleY = labelScale
                }
            )
            
            BasicTextField(
                value = value,
                onValueChange = onValueChange,
                modifier = Modifier
                    .fillMaxWidth()
                    .padding(top = 16.dp, start = 16.dp, end = 16.dp, bottom = 12.dp),
                textStyle = TextStyle(
                    fontFamily = FontFamily.Default,
                    fontWeight = FontWeight(400),
                    fontSize = 14.sp,
                    color = AlanColors.TextPrimary
                ),
                visualTransformation = if (isPassword && !showPassword) 
                    PasswordVisualTransformation() else VisualTransformation.None
            )
        }
    }
}

@Composable
private fun TestConnectionButton(
    apiKey: String,
    baseUrl: String,
    model: String,
    onSave: (String, String, String, String) -> Unit
) {
    var testState by remember { mutableStateOf<TestState>(TestState.Idle) }
    
    val borderColor by animateColorAsState(
        targetValue = when (testState) {
            is TestState.Idle -> AlanColors.VioletDim
            is TestState.Loading -> AlanColors.Violet
            is TestState.Success -> AlanColors.Green
            is TestState.Error -> AlanColors.Red
        },
        animationSpec = tween(200),
        label = "btn_border"
    )
    
    val textColor = when (testState) {
        is TestState.Idle -> AlanColors.Violet
        is TestState.Loading -> AlanColors.Violet
        is TestState.Success -> AlanColors.Green
        is TestState.Error -> AlanColors.Red
    }
    
    OutlinedButton(
        onClick = {
            onSave("Groq", baseUrl, apiKey, model)
            testState = TestState.Success(150)
        },
        modifier = Modifier.fillMaxWidth(),
        shape = RoundedCornerShape(12.dp),
        border = BorderStroke(1.dp, borderColor),
        colors = ButtonDefaults.outlinedButtonColors(
            contentColor = textColor
        )
    ) {
        when (val state = testState) {
            is TestState.Idle -> Text("Save Provider")
            is TestState.Loading -> CircularProgressIndicator(
                modifier = Modifier.size(16.dp),
                strokeWidth = 2.dp,
                color = AlanColors.Violet
            )
            is TestState.Success -> {
                Icon(Icons.Default.Check, contentDescription = null)
                Spacer(modifier = Modifier.width(8.dp))
                Text("Saved")
            }
            is TestState.Error -> {
                Text("Failed — check key")
            }
        }
    }
}

sealed class TestState {
    data object Idle : TestState()
    data object Loading : TestState()
    data class Success(val ms: Long) : TestState()
    data class Error(val message: String) : TestState()
}

@Composable
private fun SettingsRow(
    title: String,
    subtitle: String,
    actionText: String,
    actionColor: Color = AlanColors.Violet,
    onAction: () -> Unit
) {
    Surface(
        modifier = Modifier
            .fillMaxWidth()
            .height(56.dp)
            .clickable { onAction() },
        shape = RoundedCornerShape(16.dp),
        color = AlanColors.Void900,
        border = BorderStroke(1.dp, AlanColors.BorderSubtle)
    ) {
        Row(
            modifier = Modifier
                .fillMaxSize()
                .padding(16.dp),
            horizontalArrangement = Arrangement.SpaceBetween,
            verticalAlignment = Alignment.CenterVertically
        ) {
            Column {
                Text(
                    text = title,
                    style = TextStyle(
                        fontFamily = FontFamily.Default,
                        fontWeight = FontWeight(500),
                        fontSize = 14.sp,
                        color = AlanColors.TextPrimary
                    )
                )
                Text(
                    text = subtitle,
                    style = TextStyle(
                        fontFamily = FontFamily.Default,
                        fontWeight = FontWeight(400),
                        fontSize = 11.sp,
                        color = AlanColors.TextDisabled
                    )
                )
            }
            
            TextButton(onClick = onAction, colors = ButtonDefaults.textButtonColors(contentColor = actionColor)) {
                Text(actionText, style = TextStyle(fontWeight = FontWeight(500), fontSize = 14.sp))
            }
        }
    }
}

@Composable
private fun AboutSection() {
    Surface(
        modifier = Modifier.fillMaxWidth(),
        shape = RoundedCornerShape(16.dp),
        color = AlanColors.Void900,
        border = BorderStroke(1.dp, AlanColors.BorderSubtle)
    ) {
        Column {
            AboutRow(title = "Version", value = "1.0.0")
            Divider(thickness = 1.dp, color = AlanColors.BorderSubtle)
            AboutRow(title = "License", value = "MIT Open Source")
            Divider(thickness = 1.dp, color = AlanColors.BorderSubtle)
            AboutRow(title = "GitHub", value = "your-repo/alan", isLink = true)
            Divider(thickness = 1.dp, color = AlanColors.BorderSubtle)
            
            Text(
                text = "Built for Android • Local-first AI coding agent",
                style = TextStyle(
                    fontFamily = FontFamily.Default,
                    fontWeight = FontWeight(400),
                    fontSize = 12.sp,
                    color = AlanColors.TextDisabled
                ),
                modifier = Modifier
                    .fillMaxWidth()
                    .padding(16.dp)
            )
        }
    }
}

@Composable
private fun AboutRow(
    title: String,
    value: String,
    isLink: Boolean = false
) {
    Row(
        modifier = Modifier
            .fillMaxWidth()
            .padding(16.dp),
        horizontalArrangement = Arrangement.SpaceBetween
    ) {
        Text(
            text = title,
            style = TextStyle(
                fontFamily = FontFamily.Default,
                fontWeight = FontWeight(400),
                fontSize = 14.sp,
                color = AlanColors.TextSecondary
            )
        )
        
        Text(
            text = value,
            style = TextStyle(
                fontFamily = FontFamily.Default,
                fontWeight = FontWeight(400),
                fontSize = 14.sp,
                color = if (isLink) AlanColors.Violet else AlanColors.TextPrimary
            )
        )
    }
}

@Composable
private fun SavedToast(
    visible: Boolean,
    onDismiss: () -> Unit,
    modifier: Modifier = Modifier
) {
    androidx.compose.animation.AnimatedVisibility(
        visible = visible,
        enter = slideInVertically { 40 } + fadeIn(),
        exit = slideOutVertically { 40 } + fadeOut(),
        modifier = modifier
    ) {
        Surface(
            shape = RoundedCornerShape(999.dp),
            color = AlanColors.Void800,
            border = BorderStroke(1.dp, AlanColors.Green.copy(alpha = 0.25f))
        ) {
            Text(
                text = "✓ saved",
                style = TextStyle(
                    fontFamily = FontFamily.Default,
                    fontWeight = FontWeight(500),
                    fontSize = 12.sp,
                    color = AlanColors.Green
                ),
                modifier = Modifier.padding(horizontal = 16.dp, vertical = 8.dp)
            )
        }
    }
}