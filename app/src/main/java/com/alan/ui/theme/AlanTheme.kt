package com.alan.ui.theme

import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.darkColorScheme
import androidx.compose.material3.lightColorScheme
import androidx.compose.runtime.Composable
import androidx.compose.ui.graphics.Color

// VOID-inspired dark color scheme for Alan
private val AlanDarkColorScheme = darkColorScheme(
    // Void 950 - Deep background
    surface = Color(0xFF0A0A0F),      // Void950
    surfaceContainerLow = Color(0xFF11111A),  // Void900
    surfaceContainer = Color(0xFF1A1A24),     // Void800
    surfaceContainerHigh = Color(0xFF22222E), // Void700
    
    // Primary - Violet
    primary = Color(0xFF8B5CF6),      // Violet500
    primaryContainer = Color(0xFF6D28D9),     // Violet600
    onPrimary = Color(0xFFFFFFFF),
    onPrimaryContainer = Color(0xFFFFFFFF),
    
    // Secondary - Cyan
    secondary = Color(0xFF06B6D4),    // Cyan500
    secondaryContainer = Color(0xFF0891B2),   // Cyan600
    onSecondary = Color(0xFF0A0A0F),
    onSecondaryContainer = Color(0xFFFFFFFF),
    
    // Tertiary - Amber
    tertiary = Color(0xFFF59E0B),     // Amber500
    tertiaryContainer = Color(0xFFD97706),    // Amber600
    onTertiary = Color(0xFF0A0A0F),
    onTertiaryContainer = Color(0xFFFFFFFF),
    
    // Error - Red
    error = Color(0xFFEF4444),
    errorContainer = Color(0xFFDC2626),
    onError = Color(0xFFFFFFFF),
    onErrorContainer = Color(0xFFFFFFFF),
    
    // Outline
    outline = Color(0xFF3F3F4E),      // BorderSubtle
    outlineVariant = Color(0xFF2A2A3A),
    
    // Text
    onSurface = Color(0xFFF8F8FF),    // TextPrimary
    onSurfaceVariant = Color(0xFFB0B0C0), // TextSecondary
    inverseOnSurface = Color(0xFF0A0A0F),
    inverseSurface = Color(0xFFF8F8FF),
    
    // Scrim
    scrim = Color(0xFF000000),
    
    // Shadow
    shadow = Color(0xFF000000),
    
    // Surface tint
    surfaceTint = Color(0xFF8B5CF6),
)

private val AlanLightColorScheme = lightColorScheme(
    surface = Color(0xFFF8F8FF),
    surfaceContainerLow = Color(0xFFF0F0FA),
    surfaceContainer = Color(0xFFE8E8F0),
    surfaceContainerHigh = Color(0xFFDCDCE0),
    
    primary = Color(0xFF6D28D9),
    primaryContainer = Color(0xFFEDE9FE),
    onPrimary = Color(0xFFFFFFFF),
    onPrimaryContainer = Color(0xFF2E1065),
    
    secondary = Color(0xFF0891B2),
    secondaryContainer = Color(0xFFCFFAFE),
    onSecondary = Color(0xFFFFFFFF),
    onSecondaryContainer = Color(0xFF164E63),
    
    tertiary = Color(0xFFD97706),
    tertiaryContainer = Color(0xFFFEF3C7),
    onTertiary = Color(0xFFFFFFFF),
    onTertiaryContainer = Color(0xFF78350F),
    
    error = Color(0xFFDC2626),
    errorContainer = Color(0xFFFEF2F2),
    onError = Color(0xFFFFFFFF),
    onErrorContainer = Color(0xFFB91C1C),
    
    outline = Color(0xFFB0B0C0),
    outlineVariant = Color(0xFFD0D0E0),
    
    onSurface = Color(0xFF0A0A0F),
    onSurfaceVariant = Color(0xFF4A4A5A),
    inverseOnSurface = Color(0xFFF8F8FF),
    inverseSurface = Color(0xFF0A0A0F),
    
    scrim = Color(0xFF000000),
    shadow = Color(0xFF000000),
    surfaceTint = Color(0xFF6D28D9),
)

// Semantic color aliases for easy access
object AlanColors {
    // Void scale
    val Void950 = Color(0xFF0A0A0F)
    val Void900 = Color(0xFF11111A)
    val Void800 = Color(0xFF1A1A24)
    val Void700 = Color(0xFF22222E)
    val Void600 = Color(0xFF2A2A3A)
    val Void500 = Color(0xFF3F3F4E)
    val Void400 = Color(0xFF5A5A70)
    val Void300 = Color(0xFF8A8A9A)
    val Void200 = Color(0xFFB0B0C0)
    val Void100 = Color(0xFFD0D0E0)
    val Void50 = Color(0xFFE8E8F0)
    val Void0 = Color(0xFFF8F8FF)
    
    // Accent colors
    val Violet = Color(0xFF8B5CF6)
    val VioletDim = Color(0xFF8B5CF6).copy(alpha = 0.3f)
    val VioletGlow = Color(0xFFA855F7)
    val Cyan = Color(0xFF06B6D4)
    val CyanDim = Color(0xFF06B6D4).copy(alpha = 0.3f)
    val Green = Color(0xFF22C55E)
    val GreenDim = Color(0xFF22C55E).copy(alpha = 0.3f)
    val Amber = Color(0xFFF59E0B)
    val AmberDim = Color(0xFFF59E0B).copy(alpha = 0.3f)
    val Red = Color(0xFFEF4444)
    val RedDim = Color(0xFFEF4444).copy(alpha = 0.3f)
    
    // Text
    val TextPrimary = Color(0xFFF8F8FF)
    val TextSecondary = Color(0xFFB0B0C0)
    val TextDisabled = Color(0xFF6A6A7A)
    val TextMuted = Color(0xFF4A4A5A)
    
    // Borders
    val BorderSubtle = Color(0xFF2A2A3A)
    val BorderDefault = Color(0xFF3F3F4E)
    val BorderGlow = Color(0xFF8B5CF6)
}

@Composable
fun AlanTheme(
    darkTheme: Boolean = true,
    content: @Composable () -> Unit
) {
    val colorScheme = if (darkTheme) AlanDarkColorScheme else AlanLightColorScheme
    
    MaterialTheme(
        colorScheme = colorScheme,
        typography = AlanTypography,
        shapes = AlanShapes,
        content = content
    )
}

// Typography - Monospace-heavy for code
object AlanTypography {
    val fontFamily = androidx.compose.ui.text.font.FontFamily.Default
    val fontFamilyMono = androidx.compose.ui.text.font.FontFamily.Monospace
    
    val displayLarge = androidx.compose.material3.TextStyle(
        fontFamily = fontFamily,
        fontWeight = androidx.compose.ui.text.font.FontWeight(700),
        fontSize = 48.sp,
        lineHeight = 56.sp,
        letterSpacing = -1.sp,
        color = AlanColors.TextPrimary
    )
    
    val displayMedium = androidx.compose.material3.TextStyle(
        fontFamily = fontFamily,
        fontWeight = androidx.compose.ui.text.font.FontWeight(600),
        fontSize = 36.sp,
        lineHeight = 44.sp,
        letterSpacing = -0.5.sp,
        color = AlanColors.TextPrimary
    )
    
    val displaySmall = androidx.compose.material3.TextStyle(
        fontFamily = fontFamily,
        fontWeight = androidx.compose.ui.text.font.FontWeight(600),
        fontSize = 28.sp,
        lineHeight = 36.sp,
        color = AlanColors.TextPrimary
    )
    
    val headlineLarge = androidx.compose.material3.TextStyle(
        fontFamily = fontFamily,
        fontWeight = androidx.compose.ui.text.font.FontWeight(600),
        fontSize = 24.sp,
        lineHeight = 32.sp,
        color = AlanColors.TextPrimary
    )
    
    val headlineMedium = androidx.compose.material3.TextStyle(
        fontFamily = fontFamily,
        fontWeight = androidx.compose.ui.text.font.FontWeight(600),
        fontSize = 20.sp,
        lineHeight = 28.sp,
        color = AlanColors.TextPrimary
    )
    
    val headlineSmall = androidx.compose.material3.TextStyle(
        fontFamily = fontFamily,
        fontWeight = androidx.compose.ui.text.font.FontWeight(600),
        fontSize = 16.sp,
        lineHeight = 24.sp,
        color = AlanColors.TextPrimary
    )
    
    val titleLarge = androidx.compose.material3.TextStyle(
        fontFamily = fontFamily,
        fontWeight = androidx.compose.ui.text.font.FontWeight(500),
        fontSize = 18.sp,
        lineHeight = 24.sp,
        color = AlanColors.TextPrimary
    )
    
    val titleMedium = androidx.compose.material3.TextStyle(
        fontFamily = fontFamily,
        fontWeight = androidx.compose.ui.text.font.FontWeight(500),
        fontSize = 14.sp,
        lineHeight = 20.sp,
        color = AlanColors.TextPrimary
    )
    
    val titleSmall = androidx.compose.material3.TextStyle(
        fontFamily = fontFamily,
        fontWeight = androidx.compose.ui.text.font.FontWeight(500),
        fontSize = 12.sp,
        lineHeight = 16.sp,
        color = AlanColors.TextPrimary
    )
    
    val bodyLarge = androidx.compose.material3.TextStyle(
        fontFamily = fontFamily,
        fontWeight = androidx.compose.ui.text.font.FontWeight(400),
        fontSize = 16.sp,
        lineHeight = 24.sp,
        color = AlanColors.TextPrimary
    )
    
    val bodyMedium = androidx.compose.material3.TextStyle(
        fontFamily = fontFamily,
        fontWeight = androidx.compose.ui.text.font.FontWeight(400),
        fontSize = 14.sp,
        lineHeight = 20.sp,
        color = AlanColors.TextPrimary
    )
    
    val bodySmall = androidx.compose.material3.TextStyle(
        fontFamily = fontFamily,
        fontWeight = androidx.compose.ui.text.font.FontWeight(400),
        fontSize = 12.sp,
        lineHeight = 16.sp,
        color = AlanColors.TextSecondary
    )
    
    val labelLarge = androidx.compose.material3.TextStyle(
        fontFamily = fontFamily,
        fontWeight = androidx.compose.ui.text.font.FontWeight(500),
        fontSize = 14.sp,
        lineHeight = 20.sp,
        color = AlanColors.TextPrimary
    )
    
    val labelMedium = androidx.compose.material3.TextStyle(
        fontFamily = fontFamily,
        fontWeight = androidx.compose.ui.text.font.FontWeight(500),
        fontSize = 12.sp,
        lineHeight = 16.sp,
        color = AlanColors.TextPrimary
    )
    
    val labelSmall = androidx.compose.material3.TextStyle(
        fontFamily = fontFamily,
        fontWeight = androidx.compose.ui.text.font.FontWeight(500),
        fontSize = 11.sp,
        lineHeight = 16.sp,
        color = AlanColors.TextSecondary
    )
    
    // Code styles
    val codeInline = androidx.compose.material3.TextStyle(
        fontFamily = fontFamilyMono,
        fontWeight = androidx.compose.ui.text.font.FontWeight(400),
        fontSize = 13.sp,
        lineHeight = 18.sp,
        color = AlanColors.Cyan
    )
    
    val codeBlock = androidx.compose.material3.TextStyle(
        fontFamily = fontFamilyMono,
        fontWeight = androidx.compose.ui.text.font.FontWeight(400),
        fontSize = 12.sp,
        lineHeight = 18.sp,
        color = AlanColors.TextPrimary
    )
}

// Shapes
object AlanShapes {
    val extraSmall = androidx.compose.foundation.shape.RoundedCornerShape(4.dp)
    val small = androidx.compose.foundation.shape.RoundedCornerShape(8.dp)
    val medium = androidx.compose.foundation.shape.RoundedCornerShape(12.dp)
    val large = androidx.compose.foundation.shape.RoundedCornerShape(16.dp)
    val extraLarge = androidx.compose.foundation.shape.RoundedCornerShape(24.dp)
    val full = androidx.compose.foundation.shape.RoundedCornerShape(999.dp)
}