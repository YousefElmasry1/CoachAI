package com.coachai.android.ui.schedule

import android.Manifest
import android.content.pm.PackageManager
import android.os.Build
import androidx.activity.compose.rememberLauncherForActivityResult
import androidx.activity.result.contract.ActivityResultContracts
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.rememberUpdatedState
import androidx.compose.runtime.setValue
import androidx.compose.ui.platform.LocalContext
import androidx.core.content.ContextCompat
import com.coachai.android.notifications.FocusTimerService

/** Starts the focus notification from the visible activity so Android permits the foreground service. */
@Composable
internal fun rememberFocusTimerNotification(activeTask: ScheduleTask?): Boolean {
    val context = LocalContext.current
    val latestTask by rememberUpdatedState(activeTask)
    var visible by remember { mutableStateOf(false) }
    val permissionLauncher = rememberLauncherForActivityResult(
        ActivityResultContracts.RequestPermission(),
    ) { granted ->
        val task = latestTask
        visible = granted && task != null && FocusTimerService.show(
            context,
            task.title,
            task.remainingSeconds,
            task.state == TaskState.Paused,
        )
    }

    LaunchedEffect(activeTask?.id, activeTask?.state) {
        val task = activeTask
        if (task == null) {
            FocusTimerService.stop(context)
            visible = false
            return@LaunchedEffect
        }
        val granted = Build.VERSION.SDK_INT < Build.VERSION_CODES.TIRAMISU ||
            ContextCompat.checkSelfPermission(context, Manifest.permission.POST_NOTIFICATIONS) ==
            PackageManager.PERMISSION_GRANTED
        if (granted) {
            visible = FocusTimerService.show(
                context,
                task.title,
                task.remainingSeconds,
                task.state == TaskState.Paused,
            )
        } else {
            visible = false
            permissionLauncher.launch(Manifest.permission.POST_NOTIFICATIONS)
        }
    }
    return visible
}
