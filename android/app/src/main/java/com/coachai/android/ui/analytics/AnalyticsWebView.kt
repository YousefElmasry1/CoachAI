package com.coachai.android.ui.analytics

import android.annotation.SuppressLint
import android.graphics.Color as AndroidColor
import android.net.Uri
import android.os.Handler
import android.os.Looper
import android.view.View
import android.webkit.JavascriptInterface
import android.webkit.WebResourceRequest
import android.webkit.WebSettings
import android.webkit.WebView
import android.webkit.WebViewClient
import androidx.activity.compose.BackHandler
import androidx.compose.foundation.background
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.statusBarsPadding
import androidx.compose.runtime.Composable
import androidx.compose.runtime.DisposableEffect
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableIntStateOf
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.rememberUpdatedState
import androidx.compose.runtime.setValue
import androidx.compose.ui.Modifier
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.viewinterop.AndroidView
import com.coachai.android.data.AnalyticsRepository
import com.coachai.android.data.AnalyticsFailureReason
import com.coachai.android.data.AnalyticsRepositoryException
import org.json.JSONObject

private const val ANALYTICS_ASSET_BASE_URL =
    "file:///android_asset/analytics/CoachAI_Analytics_Decision_Dashboard.html"

@SuppressLint("SetJavaScriptEnabled")
@Composable
fun AnalyticsWebView(
    repository: AnalyticsRepository,
    lightTheme: Boolean,
    onNavigate: (String) -> Unit,
    modifier: Modifier = Modifier,
) {
    var windowDays by remember { mutableIntStateOf(30) }
    var refreshVersion by remember { mutableIntStateOf(0) }
    var payload by remember {
        mutableStateOf(analyticsStatePayload("loading", "Loading your analytics.", 30))
    }
    var webView by remember { mutableStateOf<WebView?>(null) }
    var pageReady by remember { mutableStateOf(false) }
    val assetUrl = "$ANALYTICS_ASSET_BASE_URL?theme=${if (lightTheme) "light" else "dark"}"
    val webBackground = if (lightTheme) Color.White else Color(0xFF080809)
    val latestPayload by rememberUpdatedState(payload)
    val latestNavigate by rememberUpdatedState(onNavigate)

    LaunchedEffect(windowDays, refreshVersion) {
        val requestedWindow = windowDays
        payload = analyticsStatePayload("loading", "Loading your analytics.", requestedWindow)
        if (pageReady) renderAnalytics(webView, payload)
        repository.loadProfile(requestedWindow).onSuccess { json ->
            if (windowDays != requestedWindow) return@onSuccess
            payload = json
            if (pageReady) renderAnalytics(webView, json)
        }.onFailure { error ->
            if (windowDays != requestedWindow) return@onFailure
            val reason = (error as? AnalyticsRepositoryException)?.reason
                ?: AnalyticsFailureReason.NETWORK_ERROR
            payload = when (reason) {
                AnalyticsFailureReason.AUTHENTICATION_REQUIRED -> analyticsStatePayload(
                    "auth",
                    "Sign in is required to load your private analytics.",
                    requestedWindow,
                )
                AnalyticsFailureReason.NETWORK_ERROR -> analyticsStatePayload(
                    "error",
                    "Zen Maestro could not reach the service. Check your connection and try again.",
                    requestedWindow,
                )
                AnalyticsFailureReason.INVALID_RESPONSE -> analyticsStatePayload(
                    "error",
                    "The service returned analytics that could not be safely displayed.",
                    requestedWindow,
                )
            }
            if (pageReady) renderAnalytics(webView, payload)
        }
    }

    BackHandler(enabled = webView?.canGoBack() == true) { webView?.goBack() }

    AndroidView(
        modifier = modifier
            .fillMaxSize()
            .background(webBackground)
            .statusBarsPadding(),
        factory = { context ->
            WebView(context).apply {
                webView = this
                visibility = View.INVISIBLE
                setBackgroundColor(if (lightTheme) AndroidColor.WHITE else AndroidColor.rgb(8, 8, 9))
                isVerticalScrollBarEnabled = false
                isHorizontalScrollBarEnabled = false
                overScrollMode = View.OVER_SCROLL_NEVER
                settings.apply {
                    javaScriptEnabled = true
                    domStorageEnabled = false
                    databaseEnabled = false
                    allowFileAccess = true
                    allowContentAccess = false
                    allowFileAccessFromFileURLs = false
                    allowUniversalAccessFromFileURLs = false
                    blockNetworkLoads = true
                    mixedContentMode = WebSettings.MIXED_CONTENT_NEVER_ALLOW
                    loadsImagesAutomatically = true
                    cacheMode = WebSettings.LOAD_NO_CACHE
                }
                addJavascriptInterface(
                    AnalyticsBridge(
                        payload = { latestPayload },
                        onWindowChanged = { value ->
                            val selected = normalizeAnalyticsWindow(value)
                            if (selected == windowDays) refreshVersion += 1 else windowDays = selected
                        },
                        onRefresh = { refreshVersion += 1 },
                        onNavigate = { destination -> latestNavigate(destination) },
                    ),
                    "CoachAIAnalyticsBridge",
                )
                webViewClient = object : WebViewClient() {
                    override fun shouldOverrideUrlLoading(
                        view: WebView,
                        request: WebResourceRequest,
                    ): Boolean = request.url != Uri.parse(assetUrl)

                    override fun onPageFinished(view: WebView, url: String) {
                        if (url != assetUrl) return
                        pageReady = true
                        renderAnalytics(view, latestPayload)
                    }
                }
                loadUrl(assetUrl)
            }
        },
        update = { webView = it },
    )

    DisposableEffect(Unit) {
        onDispose {
            webView?.apply {
                removeJavascriptInterface("CoachAIAnalyticsBridge")
                stopLoading()
                loadUrl("about:blank")
                clearHistory()
                removeAllViews()
                destroy()
            }
            webView = null
        }
    }
}

private class AnalyticsBridge(
    private val payload: () -> String,
    private val onWindowChanged: (Int) -> Unit,
    private val onRefresh: () -> Unit,
    private val onNavigate: (String) -> Unit,
) {
    private val mainHandler = Handler(Looper.getMainLooper())

    @JavascriptInterface fun getAnalytics(): String = payload()
    @JavascriptInterface fun setWindow(days: Int) = mainHandler.post { onWindowChanged(days) }
    @JavascriptInterface fun refresh() = mainHandler.post(onRefresh)
    @JavascriptInterface fun navigate(destination: String) =
        mainHandler.post { onNavigate(destination) }
}

private fun renderAnalytics(webView: WebView?, payload: String) {
    webView?.evaluateJavascript("window.CoachAIAnalytics.render($payload);") {
        webView.visibility = View.VISIBLE
    }
}

internal fun normalizeAnalyticsWindow(days: Int): Int =
    if (days in setOf(7, 30, 90)) days else 30

private fun analyticsStatePayload(state: String, message: String, windowDays: Int): String =
    JSONObject()
        .put("_client_state", state)
        .put("_client_message", message)
        .put("window_days", windowDays)
        .toString()
