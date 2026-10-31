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
        versionCode = 4
        versionName = "0.4.0"
        // The dashboard the TV's QR code opens. It is public (the landing page links to it); build with
        // -PhnwDashboard=https://your.dashboard to point the app at another server.
        val dashboard = (project.findProperty("hnwDashboard") as String?) ?: "https://live.homenetwatch.com"
        buildConfigField("String", "DASHBOARD_URL", "\"$dashboard\"")
    }

    buildFeatures {
        buildConfig = true
    }

    // Every build must be signed with the same key, or Android refuses to install it over the app already on the
    // TV. CI passes the key in HNW_KEYSTORE (from repository secrets); without it, the machine's own debug key is
    // used as before. The key file itself is never in the repository.
    val keystore = System.getenv("HNW_KEYSTORE")?.takeIf { it.isNotBlank() && file(it).isFile }
    signingConfigs {
        if (keystore != null) {
            create("shared") {
                storeFile = file(keystore)
                storePassword = System.getenv("HNW_KEYSTORE_PASSWORD")
                keyAlias = System.getenv("HNW_KEY_ALIAS")
                keyPassword = System.getenv("HNW_KEY_PASSWORD")
            }
        }
    }

    buildTypes {
        debug {
            if (keystore != null) signingConfig = signingConfigs.getByName("shared")
        }
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
    implementation("com.google.zxing:core:3.5.3")
    testImplementation("junit:junit:4.13.2")
    testImplementation("org.json:json:20240303")
}
