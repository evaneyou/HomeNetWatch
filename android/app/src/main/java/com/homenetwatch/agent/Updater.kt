package com.homenetwatch.agent

import android.app.PendingIntent
import android.content.BroadcastReceiver
import android.content.Context
import android.content.Intent
import android.content.pm.PackageInstaller
import android.os.Build
import org.json.JSONObject
import java.io.File
import java.net.HttpURLConnection
import java.net.URL
import java.security.MessageDigest

/**
 * Updates the app from the newest Android release on GitHub, when the owner agrees. CI publishes each new version
 * as a release and points `latest.json` (on the `android-latest` release) at its APK. The app checks it each time it
 * starts; Android 11 then shows its own "update this app?" screen, so nothing installs without a press on the remote.
 * Android also refuses an APK signed with any key other than the one the installed app has.
 */
object Updater {
    data class Release(val versionCode: Int, val versionName: String, val apkUrl: String, val sha256: String)

    /** What the TV shows about an update in progress, or why one failed. Empty when there is nothing to say. */
    @Volatile var status = ""

    /** Reads `latest.json`. Only an HTTPS link and a full SHA-256 are accepted. */
    fun parse(text: String): Release {
        val json = JSONObject(text)
        val release = Release(
            versionCode = json.getInt("versionCode"),
            versionName = json.getString("versionName"),
            apkUrl = json.getString("apk"),
            sha256 = json.getString("sha256").lowercase(),
        )
        require(release.apkUrl.startsWith("https://")) { "the update link is not HTTPS" }
        require(Regex("[0-9a-f]{64}").matches(release.sha256)) { "the update has no valid SHA-256" }
        return release
    }

    fun isNewer(release: Release, installedCode: Int): Boolean = release.versionCode > installedCode

    /** The newest release, if it is newer than this app. Errors (no network, GitHub down) mean no update. */
    fun check(): Release? = try {
        parse(fetch(BuildConfig.UPDATE_URL).decodeToString()).takeIf { isNewer(it, BuildConfig.VERSION_CODE) }
    } catch (_: Exception) {
        null
    }

    /** Downloads the APK into the app's cache and checks its SHA-256, package and version before it is offered. */
    fun download(context: Context, release: Release): File {
        val file = File(context.cacheDir, "update.apk")
        file.writeBytes(fetch(release.apkUrl, timeoutMs = 120_000))
        val digest = MessageDigest.getInstance("SHA-256").digest(file.readBytes()).joinToString("") { "%02x".format(it) }
        check(digest == release.sha256) { "the download is damaged (SHA-256 does not match)" }
        @Suppress("DEPRECATION")
        val info = context.packageManager.getPackageArchiveInfo(file.path, 0)
        check(info?.packageName == context.packageName) { "the download is not the HomeNetWatch agent" }
        @Suppress("DEPRECATION")
        check(info?.versionCode == release.versionCode) { "the download is not version ${release.versionName}" }
        return file
    }

    /** Hands the APK to Android's installer. Android asks the owner to confirm (see [UpdateReceiver]). */
    fun install(context: Context, apk: File) {
        val installer = context.packageManager.packageInstaller
        val params = PackageInstaller.SessionParams(PackageInstaller.SessionParams.MODE_FULL_INSTALL)
            .apply { setAppPackageName(context.packageName) }
        val id = installer.createSession(params)
        installer.openSession(id).use { session ->
            session.openWrite("update.apk", 0, apk.length()).use { out ->
                apk.inputStream().use { it.copyTo(out) }
                session.fsync(out)
            }
            // Android fills in the result, so the intent must be mutable where that is a choice (Android 12+).
            val flags = PendingIntent.FLAG_UPDATE_CURRENT or
                if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.S) PendingIntent.FLAG_MUTABLE else 0
            val result = PendingIntent.getBroadcast(context, id, Intent(context, UpdateReceiver::class.java), flags)
            session.commit(result.intentSender)
        }
    }

    private fun fetch(url: String, timeoutMs: Int = 15_000): ByteArray {
        // GitHub answers release downloads with a redirect to its file host, also HTTPS, which this follows.
        val connection = URL(url).openConnection() as HttpURLConnection
        try {
            connection.connectTimeout = 15_000
            connection.readTimeout = timeoutMs
            val code = connection.responseCode
            if (code !in 200..299) throw HttpFailure(code, "HTTP $code")
            return connection.inputStream.use { it.readBytes() }
        } finally {
            connection.disconnect()
        }
    }
}

/** Android's answer to an install: show its confirmation screen, or record why the update failed. */
class UpdateReceiver : BroadcastReceiver() {
    override fun onReceive(context: Context, intent: Intent) {
        when (intent.getIntExtra(PackageInstaller.EXTRA_STATUS, PackageInstaller.STATUS_FAILURE)) {
            PackageInstaller.STATUS_PENDING_USER_ACTION -> {
                @Suppress("DEPRECATION")
                val confirm = intent.getParcelableExtra<Intent>(Intent.EXTRA_INTENT) ?: return
                context.startActivity(confirm.addFlags(Intent.FLAG_ACTIVITY_NEW_TASK))
            }
            // On success Android replaces the app; BootReceiver starts the agent again.
            PackageInstaller.STATUS_SUCCESS -> Updater.status = ""
            else -> Updater.status = "Update not installed: " +
                (intent.getStringExtra(PackageInstaller.EXTRA_STATUS_MESSAGE) ?: "cancelled")
        }
    }
}
