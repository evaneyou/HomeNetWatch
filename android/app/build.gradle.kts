plugins {
    id("com.android.application")
    id("org.jetbrains.kotlin.android")
}

android {
    namespace = "com.homenetwatch.agent"
    compileSdk = 34

    defaultConfig {
        applicationId = "com.homenetwatch.agent"
        minSdk = 26
        targetSdk = 34
        versionCode = 1
        versionName = "0.1.0"
    }

    buildTypes {
        release {
            isMinifyEnabled = false
        }
    }

    compileOptions {
        sourceCompatibility = JavaVersion.VERSION_17
        targetCompatibility = JavaVersion.VERSION_17
    }
    kotlinOptions {
        jvmTarget = "17"
    }

    packaging {
        // The traffic sender ships as a native library and is run from the app's library folder,
        // so it has to be extracted to disk.
        jniLibs.useLegacyPackaging = true
    }

    testOptions {
        unitTests.isReturnDefaultValues = true
    }
}

dependencies {
    implementation("org.bouncycastle:bcprov-jdk18on:1.78.1")
    implementation("com.wireguard.android:tunnel:1.0.20230706")
    testImplementation("junit:junit:4.13.2")
    testImplementation("org.json:json:20240303")
}
