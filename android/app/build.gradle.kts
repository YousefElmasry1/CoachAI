import groovy.json.JsonSlurper

plugins {
    id("com.android.application")
    id("com.google.gms.google-services") apply false
    id("org.jetbrains.kotlin.plugin.compose")
}

val apiBaseUrl = providers.gradleProperty("COACHAI_API_BASE_URL").orElse("")
val releaseApiBaseUrl = providers.gradleProperty("COACHAI_RELEASE_API_BASE_URL").orElse("")
val googleServicesJson = layout.projectDirectory.file("google-services.json")
val firebaseAuthLinkHost = providers.gradleProperty("FIREBASE_AUTH_LINK_HOST").orElse(
    providers.fileContents(googleServicesJson).asText.map { contents ->
        val root = JsonSlurper().parseText(contents) as Map<*, *>
        val projectInfo = root["project_info"] as Map<*, *>
        "${projectInfo["project_id"]}.firebaseapp.com"
    },
)
val skipGoogleServicesForTests = providers
    .gradleProperty("SKIP_GOOGLE_SERVICES_FOR_TESTS")
    .map(String::toBoolean)
    .orElse(false)

if (skipGoogleServicesForTests.get()) {
    require(
        gradle.startParameter.taskNames.isNotEmpty() &&
            gradle.startParameter.taskNames.any { it.contains("test", ignoreCase = true) },
    ) { "SKIP_GOOGLE_SERVICES_FOR_TESTS may only be used with test tasks." }
} else {
    apply(plugin = "com.google.gms.google-services")
    extensions.configure<
        com.google.gms.googleservices.GoogleServicesPlugin.GoogleServicesPluginConfig
    >("googleServices") {
        disableVersionCheck = true
    }
}

android {
    namespace = "com.coachai.android"
    compileSdk = 37

    defaultConfig {
        applicationId = "com.zenmaestro.app"
        minSdk = 26
        targetSdk = 37
        versionCode = 1
        versionName = "0.1.0"

        testInstrumentationRunner = "androidx.test.runner.AndroidJUnitRunner"
        vectorDrawables.useSupportLibrary = true
        buildConfigField(
            "String",
            "COACHAI_API_BASE_URL",
            "\"${apiBaseUrl.get().replace("\\", "\\\\").replace("\"", "\\\"")}\"",
        )
        buildConfigField("String", "FIREBASE_AUTH_LINK_HOST", "\"${firebaseAuthLinkHost.get()}\"")
        manifestPlaceholders["firebaseAuthLinkHost"] = firebaseAuthLinkHost.get()
    }

    buildFeatures {
        compose = true
        buildConfig = true
    }

    buildTypes {
        getByName("release") {
            buildConfigField(
                "String",
                "COACHAI_API_BASE_URL",
                "\"${releaseApiBaseUrl.get().replace("\\", "\\\\").replace("\"", "\\\"")}\"",
            )
        }
    }

    compileOptions {
        sourceCompatibility = JavaVersion.VERSION_17
        targetCompatibility = JavaVersion.VERSION_17
    }

    packaging {
        resources.excludes += "/META-INF/{AL2.0,LGPL2.1}"
    }

}

gradle.taskGraph.whenReady {
    val releaseRequested = allTasks.any { it.name.contains("Release", ignoreCase = true) }
    if (releaseRequested) {
        require(releaseApiBaseUrl.get().startsWith("https://")) {
            "COACHAI_RELEASE_API_BASE_URL must be a non-empty HTTPS URL for release builds."
        }
    }
}

dependencies {
    val composeBom = platform("androidx.compose:compose-bom:2026.09.00")
    val firebaseBom = platform("com.google.firebase:firebase-bom:35.1.0")
    implementation(composeBom)
    implementation(firebaseBom)
    androidTestImplementation(composeBom)

    implementation("com.google.firebase:firebase-auth")
    implementation("com.google.firebase:firebase-messaging")
    implementation("com.google.firebase:firebase-installations")
    implementation("androidx.credentials:credentials:1.3.0")
    implementation("androidx.credentials:credentials-play-services-auth:1.3.0")
    implementation("com.google.android.libraries.identity.googleid:googleid:1.1.1")

    implementation("androidx.activity:activity-compose:1.13.0")
    implementation("androidx.lifecycle:lifecycle-viewmodel-compose:2.11.0")
    implementation("androidx.lifecycle:lifecycle-runtime-compose:2.11.0")
    implementation("androidx.compose.ui:ui")
    implementation("androidx.compose.ui:ui-tooling-preview")
    implementation("androidx.compose.foundation:foundation")
    implementation("androidx.compose.material3:material3")
    implementation("androidx.compose.material:material-icons-extended")
    implementation("org.jetbrains.kotlinx:kotlinx-coroutines-android:1.11.0")

    debugImplementation("androidx.compose.ui:ui-tooling")
    debugImplementation("androidx.compose.ui:ui-test-manifest")

    testImplementation("junit:junit:4.13.2")
    testImplementation("org.jetbrains.kotlinx:kotlinx-coroutines-test:1.11.0")
    androidTestImplementation("androidx.test.ext:junit:1.3.0")
    androidTestImplementation("androidx.test.espresso:espresso-core:3.7.0")
    androidTestImplementation("androidx.compose.ui:ui-test-junit4")
}
