plugins {
    id("com.android.application")
    id("org.jetbrains.kotlin.android")
}

val releaseApiBaseUrl = providers.gradleProperty("RELEASE_API_BASE_URL").orElse("").get()
val releaseWsBaseUrl = providers.gradleProperty("RELEASE_WS_BASE_URL").orElse("").get()

fun String.asBuildConfigValue(): String = "\"${replace("\\", "\\\\").replace("\"", "\\\"")}\""

android {
    namespace = "com.kotakneo.terminal"
    compileSdk = 35

    defaultConfig {
        applicationId = "com.kotakneo.terminal"
        minSdk = 26
        targetSdk = 35
        versionCode = 1
        versionName = "1.0.0"
    }

    buildTypes {
        debug {
            // 10.0.2.2 reaches host machine localhost from Android Emulator
            buildConfigField("String", "API_BASE_URL", "\"http://10.0.2.2:8000/\"")
            buildConfigField("String", "WS_BASE_URL", "\"ws://10.0.2.2:8000\"")
        }
        release {
            isMinifyEnabled = false
            // Supply -PRELEASE_API_BASE_URL=https://host/ and -PRELEASE_WS_BASE_URL=wss://host.
            buildConfigField("String", "API_BASE_URL", releaseApiBaseUrl.asBuildConfigValue())
            buildConfigField("String", "WS_BASE_URL", releaseWsBaseUrl.asBuildConfigValue())
        }
    }

    buildFeatures {
        compose = true
        buildConfig = true
    }

    composeOptions {
        kotlinCompilerExtensionVersion = "1.5.15"
    }

    compileOptions {
        sourceCompatibility = JavaVersion.VERSION_17
        targetCompatibility = JavaVersion.VERSION_17
    }

    kotlinOptions {
        jvmTarget = "17"
    }
}

val validateReleaseConfig by tasks.registering {
    doLast {
        check(releaseApiBaseUrl.startsWith("https://") && releaseWsBaseUrl.startsWith("wss://")) {
            "Release builds require HTTPS RELEASE_API_BASE_URL and WSS RELEASE_WS_BASE_URL Gradle properties."
        }
    }
}

tasks.matching { it.name.contains("Release", ignoreCase = true) && it.name != "validateReleaseConfig" }
    .configureEach { dependsOn(validateReleaseConfig) }

dependencies {
    implementation(platform("androidx.compose:compose-bom:2024.09.03"))
    implementation("androidx.core:core-ktx:1.13.1")
    implementation("androidx.activity:activity-compose:1.9.3")
    implementation("androidx.lifecycle:lifecycle-viewmodel-compose:2.8.6")
    implementation("androidx.lifecycle:lifecycle-runtime-ktx:2.8.6")
    implementation("androidx.compose.ui:ui")
    implementation("androidx.compose.ui:ui-tooling-preview")
    implementation("androidx.compose.material3:material3")
    implementation("androidx.compose.material:material-icons-extended")

    implementation("com.squareup.retrofit2:retrofit:2.11.0")
    implementation("com.squareup.retrofit2:converter-gson:2.11.0")
    implementation("com.squareup.okhttp3:okhttp:4.12.0")
    implementation("com.google.code.gson:gson:2.11.0")
    implementation("org.jetbrains.kotlinx:kotlinx-coroutines-android:1.8.1")
}
