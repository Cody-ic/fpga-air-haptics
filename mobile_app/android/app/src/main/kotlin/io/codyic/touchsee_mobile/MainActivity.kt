package io.codyic.touchsee_mobile

import android.app.Activity
import android.content.Intent
import io.flutter.embedding.android.FlutterActivity
import io.flutter.embedding.engine.FlutterEngine
import io.flutter.plugin.common.MethodChannel

class MainActivity : FlutterActivity() {
    private var pending: MethodChannel.Result? = null
    private var exportContent: String? = null
    override fun configureFlutterEngine(flutterEngine: FlutterEngine) {
        super.configureFlutterEngine(flutterEngine)
        MethodChannel(flutterEngine.dartExecutor.binaryMessenger, "io.codyic.touchsee/files")
            .setMethodCallHandler { call, result ->
                if (call.method != "import" && call.method != "export") {
                    result.notImplemented()
                } else if (pending != null) {
                    result.error("BUSY", "请先完成当前文件操作", null)
                } else {
                    pending = result
                    try {
                        if (call.method == "import") {
                            startActivityForResult(Intent(Intent.ACTION_OPEN_DOCUMENT).apply {
                                addCategory(Intent.CATEGORY_OPENABLE)
                                type = "*/*"
                            }, 401)
                        } else {
                            exportContent = call.argument<String>("content") ?: error("缺少图形内容")
                            require(exportContent!!.toByteArray(Charsets.UTF_8).size <= 65536) { "图形文件过大" }
                            startActivityForResult(Intent(Intent.ACTION_CREATE_DOCUMENT).apply {
                                addCategory(Intent.CATEGORY_OPENABLE)
                                type = "application/json"
                                putExtra(Intent.EXTRA_TITLE, call.argument<String>("name") ?: "触见图形.json")
                            }, 402)
                        }
                    } catch (error: Exception) {
                        pending = null
                        exportContent = null
                        result.error("FILE_ERROR", error.message, null)
                    }
                }
            }
    }

    @Deprecated("Legacy activity result used by this small Flutter bridge")
    override fun onActivityResult(requestCode: Int, resultCode: Int, data: Intent?) {
        super.onActivityResult(requestCode, resultCode, data)
        if (requestCode != 401 && requestCode != 402) return
        val reply = pending ?: return
        val content = exportContent
        pending = null
        exportContent = null
        if (resultCode != Activity.RESULT_OK || data?.data == null) {
            reply.success(if (requestCode == 401) null else false)
            return
        }
        try {
            val uri = data.data!!
            if (requestCode == 401) {
                val bytes = contentResolver.openInputStream(uri)?.use { input ->
                    val buffer = java.io.ByteArrayOutputStream()
                    val chunk = ByteArray(4096)
                    var count = input.read(chunk)
                    while (count != -1) {
                        require(buffer.size() + count <= 65536) { "图形文件不能超过 64 KiB" }
                        buffer.write(chunk, 0, count)
                        count = input.read(chunk)
                    }
                    buffer.toByteArray()
                } ?: error("无法读取文件")
                reply.success(String(bytes, Charsets.UTF_8))
            } else {
                contentResolver.openOutputStream(uri, "wt")?.use { output ->
                    output.write((content ?: error("导出内容已丢失，请重试")).toByteArray(Charsets.UTF_8))
                } ?: error("无法保存文件")
                reply.success(true)
            }
        } catch (error: Exception) {
            reply.error("FILE_ERROR", error.message, null)
        }
    }
}
