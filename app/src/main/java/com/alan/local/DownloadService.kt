package com.alan.local

import android.app.DownloadManager
import android.app.Notification
import android.app.NotificationChannel
import android.app.NotificationManager
import android.app.PendingIntent
import android.app.Service
import android.content.Context
import android.content.Intent
import android.os.Build
import android.os.IBinder
import androidx.core.app.NotificationCompat
import com.alan.R
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.launch

class DownloadService : Service() {
    
    private val scope = CoroutineScope(Dispatchers.IO + SupervisorJob())
    
    companion object {
        private const val CHANNEL_ID = "download_channel"
        private const val NOTIFICATION_ID = 1001
    }
    
    override fun onCreate() {
        super.onCreate()
        createNotificationChannel()
    }
    
    override fun onStartCommand(intent: Intent, flags: Int, startId: Int): Int {
        val tierName = intent.getStringExtra("tier") ?: return START_NOT_STICKY
        val modelManager = ModelManager.getInstance(this)
        val tier = ModelManager.ModelTier.fromName(tierName) ?: return START_NOT_STICKY
        
        val notification = buildNotification("Downloading ${tier.displayName}...", 0f)
        startForeground(NOTIFICATION_ID, notification, FOREGROUND_SERVICE_TYPE_DATA_SYNC)
        
        scope.launch {
            modelManager.downloadModel(tier) { progress ->
                val notif = buildNotification("Downloading ${tier.displayName}...", progress)
                val manager = getSystemService(Context.NOTIFICATION_SERVICE) as NotificationManager
                manager.notify(NOTIFICATION_ID, notif)
            }.fold(
                onSuccess = { size ->
                    val notif = buildNotification("Downloaded ${tier.displayName} (${formatBytes(size)})", 1f)
                    val manager = getSystemService(Context.NOTIFICATION_SERVICE) as NotificationManager
                    manager.notify(NOTIFICATION_ID, notif)
                    stopSelf()
                },
                onFailure = { error ->
                    val notif = buildNotification("Failed: ${error.message}", 1f)
                        .setColorized(true)
                        .setColor(0xFFEF4444.toInt())
                        .build()
                    val manager = getSystemService(Context.NOTIFICATION_SERVICE) as NotificationManager
                    manager.notify(NOTIFICATION_ID, notif)
                    stopSelf()
                }
            )
        }
        
        return START_NOT_STICKY
    }
    
    private fun buildNotification(text: String, progress: Float): NotificationCompat.Builder {
        return NotificationCompat.Builder(this, CHANNEL_ID)
            .setContentTitle("Alan Model Download")
            .setContentText(text)
            .setSmallIcon(R.drawable.ic_download)
            .setProgress(100, (progress * 100).toInt(), progress < 1f)
            .setOngoing(progress < 1f)
            .setOnlyAlertOnce(true)
            .setCategory(NotificationCompat.CATEGORY_PROGRESS)
    }
    
    private fun createNotificationChannel() {
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.O) {
            val channel = NotificationChannel(
                CHANNEL_ID,
                "Model Downloads",
                NotificationManager.IMPORTANCE_LOW
            ).apply {
                description = "Notifications for AI model downloads"
            }
            val manager = getSystemService(Context.NOTIFICATION_SERVICE) as NotificationManager
            manager.createNotificationChannel(channel)
        }
    }
    
    private fun formatBytes(bytes: Long): String {
        return when {
            bytes >= 1_000_000_000 -> String.format("%.1f GB", bytes / 1_000_000_000.0)
            bytes >= 1_000_000 -> String.format("%.1f MB", bytes / 1_000_000.0)
            else -> String.format("%.1f KB", bytes / 1000.0)
        }
    }
    
    override fun onBind(intent: Intent?): IBinder? = null
    
    override fun onDestroy() {
        scope.cancel()
        super.onDestroy()
    }
}