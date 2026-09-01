package com.alan.ui.chat

import android.content.ClipData
import android.content.ClipboardManager
import android.content.Context
import androidx.compose.animation.core.animateFloatAsState
import androidx.compose.animation.core.spring
import androidx.compose.animation.core.tween
import androidx.compose.foundation.background
import androidx.compose.foundation.clickable
import androidx.compose.foundation.gestures.detectTapGestures
import androidx.compose.foundation.layout.*
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.lazy.items
import androidx.compose.foundation.lazy.rememberLazyListState
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.foundation.text.BasicTextField
import androidx.compose.foundation.text.KeyboardActions
import androidx.compose.foundation.text.KeyboardOptions
import androidx.compose.foundation.text.selection.SelectionContainer
import androidx.compose.foundation.verticalScroll
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.filled.*
import androidx.compose.material3.*
import androidx.compose.runtime.*
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.clip
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.input.pointer.pointerInput
import androidx.compose.ui.layout.ContentScale
import androidx.compose.ui.layout.onGloballyPositioned
import androidx.compose.ui.text.*
import androidx.compose.ui.text.font.FontFamily
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.input.*
import androidx.compose.ui.text.style.TextAlign
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import androidx.compose.ui.window.Dialog
import com.alan.agent.AlanState
import com.alan.agent.ToolResult
import com.alan.agent.ToolDefinition
import com.alan.memory.MemoryRepository
import com.alan.ui.theme.AlanColors
import com.alan.ui.theme.AlanTheme
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.launch

@OptIn(ExperimentalMaterial3Api::class)
@Composable
fun ChatScreen(
    alanCore: com.alan.agent.AlanCore,
    memoryRepo: MemoryRepository,
    modifier: Modifier = Modifier
) {
    var messages by remember { mutableStateOf<List<ChatMessage>>(emptyList()) }
    var inputText by remember { mutableStateOf("") }
    var showSettings by remember { mutableStateOf(false) }
    var scrollToBottom by remember { mutableStateOf(false) }
    val lazyListState = rememberLazyListState()
    val coroutineScope = rememberCoroutineScope()
    val alanState by alanCore.state.collectAsState()
    
    // Auto-scroll to bottom when new messages arrive
    LaunchedEffect(messages.size, scrollToBottom) {
        if (scrollToBottom) {
            lazyListState.animateScrollToItem(messages.size - 1)
            scrollToBottom = false
        }
    }
    
    // Send a user message
    fun sendMessage() {
        val text = inputText.trim()
        if (text.isBlank()) return

        val userMessage = ChatMessage(
            id = System.currentTimeMillis().toString(),
            role = ChatRole.User,
            content = text,
            timestamp = System.currentTimeMillis()
        )

        messages = messages + userMessage
        inputText = ""
        scrollToBottom = true

        // Execute task
        CoroutineScope(Dispatchers.IO).launch {
            alanCore.executeTask(text)
        }
    }

    // Enter is handled through KeyboardActions(onDone) in the input field.
    
    Box(
        modifier = modifier
            .fillMaxSize()
            .background(AlanColors.Void950)
    ) {
        Column(
            modifier = Modifier.fillMaxSize(),
            verticalArrangement = Arrangement.Top
        ) {
            // App Bar
            TopAppBar(
                modifier = Modifier.fillMaxWidth(),
                title = { Text("Alan", style = TextStyle(fontWeight = FontWeight.Bold, fontSize = 18.sp, color = AlanColors.TextPrimary)) },
                colors = TopAppBarDefaults.topAppBarColors(
                    containerColor = AlanColors.Void900,
                    titleContentColor = AlanColors.TextPrimary
                ),
                actions = {
                    IconButton(onClick = { showSettings = true }) {
                        Icon(Icons.Default.Settings, contentDescription = "Settings", tint = AlanColors.TextSecondary)
                    }
                }
            )
            
            // Chat Messages
            LazyColumn(
                modifier = Modifier
                    .fillMaxSize()
                    .weight(1f)
                    .padding(bottom = 16.dp),
                state = lazyListState,
                reverseLayout = false,
                contentPadding = PaddingValues(16.dp),
                verticalArrangement = Arrangement.spacedBy(12.dp)
            ) {
                items(messages) { message ->
                    MessageBubble(message = message)
                }
                
                // Typing indicator
                val runningState = alanState as? AlanState.Running
                if (runningState != null) {
                    item {
                        TypingIndicator(step = runningState.step)
                    }
                }
            }
            
            // Error banner
            val errorState = alanState as? AlanState.Error
            if (errorState != null) {
                Surface(
                    modifier = Modifier
                        .fillMaxWidth()
                        .padding(horizontal = 16.dp, vertical = 8.dp),
                    shape = RoundedCornerShape(12.dp),
                    color = AlanColors.RedDim,
                    border = androidx.compose.foundation.BorderStroke(1.dp, AlanColors.Red.copy(alpha = 0.4f))
                ) {
                    Row(
                        modifier = Modifier.padding(horizontal = 16.dp, vertical = 8.dp),
                        verticalAlignment = Alignment.CenterVertically
                    ) {
                        Icon(Icons.Default.Error, contentDescription = "Error", tint = AlanColors.Red)
                        Spacer(modifier = Modifier.width(12.dp))
                        Text(
                            text = errorState.message,
                            color = AlanColors.Red,
                            style = TextStyle(fontSize = 14.sp),
                            modifier = Modifier.weight(1f)
                        )
                        Spacer(modifier = Modifier.width(8.dp))
                        TextButton(onClick = { alanCore.dismissError() }) {
                            Text("Dismiss", color = AlanColors.Red)
                        }
                    }
                }
            }
            
            // Input Area
            ChatInputArea(
                text = inputText,
                onTextChange = { inputText = it },
                onSend = { sendMessage() },
                isLoading = alanState is AlanState.Running,
                modifier = Modifier
                    .fillMaxWidth()
                    .padding(16.dp)
                    .padding(bottom = 16.dp)
            )
        }
        
        // Settings Dialog
        if (showSettings) {
            SettingsDialog(
                onDismiss = { showSettings = false },
                alanCore = alanCore,
                memoryRepo = memoryRepo
            )
        }
    }
}

data class ChatMessage(
    val id: String,
    val role: ChatRole,
    val content: String,
    val timestamp: Long,
    val toolCalls: List<ToolCallDisplay> = emptyList(),
    val toolResults: List<ToolResultDisplay> = emptyList()
)

enum class ChatRole { User, Assistant, Tool, System }

data class ToolCallDisplay(
    val tool: String,
    val args: String,
    val timestamp: Long
)

data class ToolResultDisplay(
    val tool: String,
    val success: Boolean,
    val output: String,
    val timestamp: Long
)

@Composable
fun MessageBubble(message: ChatMessage) {
    val isUser = message.role == ChatRole.User
    val isAssistant = message.role == ChatRole.Assistant
    val isTool = message.role == ChatRole.Tool
    
    val bgColor = when {
        isUser -> AlanColors.VioletDim
        isAssistant -> AlanColors.Void800
        isTool -> AlanColors.AmberDim
        else -> AlanColors.Void800
    }
    
    val textColor = when {
        isUser -> AlanColors.Violet
        isAssistant -> AlanColors.TextPrimary
        isTool -> AlanColors.Amber
        else -> AlanColors.TextPrimary
    }
    
    val alignment = if (isUser) Alignment.End else Alignment.Start
    
    Column(
        modifier = Modifier.fillMaxWidth(),
        horizontalAlignment = alignment
    ) {
        // Tool calls
        message.toolCalls.forEach { call ->
            ToolCallBubble(tool = call.tool, args = call.args)
        }
        
        // Tool results
        message.toolResults.forEach { result ->
            ToolResultBubble(tool = result.tool, success = result.success, output = result.output)
        }
        
        // Main message
        Box(
            modifier = Modifier
                .fillMaxWidth(0.85f)
                .padding(vertical = 4.dp),
            contentAlignment = if (isUser) Alignment.CenterEnd else Alignment.CenterStart
        ) {
            Surface(
                modifier = Modifier
                    .fillMaxWidth()
                    .wrapContentSize(),
                shape = RoundedCornerShape(16.dp),
                color = bgColor,
                border = androidx.compose.foundation.BorderStroke(1.dp, when {
                    isUser -> AlanColors.Violet.copy(alpha = 0.3f)
                    isTool -> AlanColors.Amber.copy(alpha = 0.3f)
                    else -> AlanColors.BorderSubtle
                })
            ) {
                SelectionContainer {
                    Text(
                        text = message.content,
                        style = TextStyle(
                            fontFamily = if (isAssistant || isTool) FontFamily.Monospace else FontFamily.Default,
                            fontSize = 14.sp,
                            color = textColor,
                            lineHeight = 22.sp
                        ),
                        modifier = Modifier.padding(16.dp)
                    )
                }
            }
        }
        
        // Timestamp
        Text(
            text = formatTime(message.timestamp),
            style = TextStyle(fontSize = 10.sp, color = AlanColors.TextDisabled),
            modifier = Modifier
                .padding(start = if (isUser) 0.dp else 16.dp, end = if (isUser) 16.dp else 0.dp, top = 4.dp)
                .align(alignment)
        )
    }
}

@Composable
fun ToolCallBubble(tool: String, args: String) {
    Box(
        modifier = Modifier
            .fillMaxWidth(0.8f)
            .padding(vertical = 4.dp)
            .wrapContentWidth(),
        contentAlignment = Alignment.CenterStart
    ) {
        Surface(
            shape = RoundedCornerShape(12.dp),
            color = AlanColors.CyanDim,
            border = androidx.compose.foundation.BorderStroke(1.dp, AlanColors.Cyan.copy(alpha = 0.3f))
        ) {
            Column(modifier = Modifier.padding(12.dp)) {
                Row(
                    modifier = Modifier.fillMaxWidth(),
                    horizontalArrangement = Arrangement.SpaceBetween
                ) {
                    Text("▶ $tool", style = TextStyle(fontWeight = FontWeight.Bold, fontSize = 12.sp, color = AlanColors.Cyan, fontFamily = FontFamily.Monospace))
                    Text("executing...", style = TextStyle(fontSize = 10.sp, color = AlanColors.TextDisabled))
                }
                Text(
                    text = args.take(200),
                    style = TextStyle(fontFamily = FontFamily.Monospace, fontSize = 11.sp, color = AlanColors.TextSecondary),
                    modifier = Modifier.padding(top = 4.dp).fillMaxWidth()
                )
            }
        }
    }
}

@Composable
fun ToolResultBubble(tool: String, success: Boolean, output: String) {
    val color = if (success) AlanColors.Green else AlanColors.Red
    val icon = if (success) Icons.Default.CheckCircle else Icons.Default.Error
    
    Box(
        modifier = Modifier
            .fillMaxWidth(0.8f)
            .padding(vertical = 4.dp)
            .wrapContentWidth(),
        contentAlignment = Alignment.CenterStart
    ) {
        Surface(
            shape = RoundedCornerShape(12.dp),
            color = color.copy(alpha = 0.15f),
            border = androidx.compose.foundation.BorderStroke(1.dp, color.copy(alpha = 0.3f))
        ) {
            Column(modifier = Modifier.padding(12.dp)) {
                Row(
                    modifier = Modifier.fillMaxWidth(),
                    horizontalArrangement = Arrangement.SpaceBetween
                ) {
                    Row(verticalAlignment = Alignment.CenterVertically) {
                        Icon(icon, contentDescription = null, tint = color, modifier = Modifier.size(16.dp))
                        Spacer(modifier = Modifier.width(8.dp))
                        Text(tool, style = TextStyle(fontWeight = FontWeight.Bold, fontSize = 12.sp, color = color, fontFamily = FontFamily.Monospace))
                    }
                    Text(if (success) "success" else "failed", style = TextStyle(fontSize = 10.sp, color = color))
                }
                if (output.isNotBlank()) {
                    Text(
                        text = output.take(500),
                        style = TextStyle(fontFamily = FontFamily.Monospace, fontSize = 11.sp, color = AlanColors.TextSecondary),
                        modifier = Modifier.padding(top = 4.dp).fillMaxWidth()
                    )
                }
            }
        }
    }
}

@Composable
fun TypingIndicator(step: String) {
    Row(
        modifier = Modifier.padding(horizontal = 16.dp),
        horizontalArrangement = Arrangement.Start
    ) {
        Surface(
            shape = RoundedCornerShape(16.dp),
            color = AlanColors.Void800,
            border = androidx.compose.foundation.BorderStroke(1.dp, AlanColors.BorderSubtle)
        ) {
            Row(
                modifier = Modifier.padding(16.dp),
                verticalAlignment = Alignment.CenterVertically
            ) {
                CircularProgressIndicator(
                    modifier = Modifier.size(16.dp),
                    strokeWidth = 2.dp,
                    color = AlanColors.Cyan
                )
                Spacer(modifier = Modifier.width(12.dp))
                Text("Alan is $step", style = TextStyle(fontSize = 14.sp, color = AlanColors.TextSecondary))
            }
        }
    }
}

@Composable
fun ChatInputArea(
    text: String,
    onTextChange: (String) -> Unit,
    onSend: () -> Unit,
    isLoading: Boolean,
    modifier: Modifier = Modifier
) {
    var showAttachmentMenu by remember { mutableStateOf(false) }
    
    Row(
        modifier = modifier
            .fillMaxWidth()
            .height(56.dp),
        verticalAlignment = Alignment.CenterVertically
    ) {
        IconButton(onClick = { showAttachmentMenu = true }) {
            Icon(Icons.Default.AttachFile, contentDescription = "Attach", tint = AlanColors.TextDisabled, modifier = Modifier.size(24.dp))
        }
        
        BasicTextField(
            value = text,
            onValueChange = onTextChange,
            modifier = Modifier
                .fillMaxWidth()
                .weight(1f)
                .padding(horizontal = 12.dp),
            textStyle = TextStyle(fontSize = 14.sp, color = AlanColors.TextPrimary),
            singleLine = false,
            maxLines = 5,
            keyboardOptions = KeyboardOptions.Default.copy(imeAction = ImeAction.Send),
            keyboardActions = KeyboardActions(onDone = { onSend() })
        )
        
        IconButton(onClick = onSend, enabled = !isLoading && text.isNotBlank()) {
            Icon(
                Icons.Default.Send,
                contentDescription = "Send",
                tint = if (isLoading || text.isBlank()) AlanColors.TextDisabled else AlanColors.Violet,
                modifier = Modifier.size(24.dp)
            )
        }
    }
}

@Composable
fun SettingsDialog(
    onDismiss: () -> Unit,
    alanCore: com.alan.agent.AlanCore,
    memoryRepo: MemoryRepository
) {
    Dialog(onDismissRequest = onDismiss) {
        Surface(
            modifier = Modifier.fillMaxWidth().padding(24.dp),
            shape = RoundedCornerShape(16.dp),
            color = AlanColors.Void900
        ) {
            Column(modifier = Modifier.padding(24.dp)) {
                Row(
                    modifier = Modifier.fillMaxWidth(),
                    horizontalArrangement = Arrangement.SpaceBetween
                ) {
                    Text("Settings", style = TextStyle(fontSize = 20.sp, fontWeight = FontWeight.Bold, color = AlanColors.TextPrimary))
                    IconButton(onClick = onDismiss) {
                        Icon(Icons.Default.Close, contentDescription = "Close", tint = AlanColors.TextSecondary)
                    }
                }
                
                Divider(color = AlanColors.BorderSubtle, modifier = Modifier.padding(vertical = 16.dp).fillMaxWidth())
                
                SettingsContent(alanCore = alanCore, memoryRepo = memoryRepo)
            }
        }
    }
}

@Composable
fun SettingsContent(alanCore: com.alan.agent.AlanCore, memoryRepo: MemoryRepository) {
    Column(modifier = Modifier.fillMaxWidth()) {
        // Provider settings would go here
        Text("Settings coming soon...", style = TextStyle(color = AlanColors.TextSecondary, fontSize = 14.sp), modifier = Modifier.padding(16.dp).fillMaxWidth())
    }
}

fun formatTime(timestamp: Long): String {
    val date = java.util.Date(timestamp)
    return java.text.SimpleDateFormat("HH:mm", java.util.Locale.getDefault()).format(date)
}