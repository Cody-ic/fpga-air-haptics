# 触见 Android 应用

更新日期：2026-09-25。手机端可绘制图形、保存与导入、连接 Demo 或 BLE 设备，并根据回传状态显示掌面上的焦点位置。当前为 **Android 内部测试版**；没有 iOS 安装包，也尚无手机连接真实模块／板卡的验证结果。HAP3 沿用[上位机协议草案](../desktop_app/PROTOCOL.md)，仍需与板端共同定稿。

## 1. 安装与首次体验

本机构建产物为 `dist/TouchSee-Android-0.1.0.apk`，复制到 Android 手机后安装。支持 Android 7.0（API 24）及以上，包含 ARM 32 位、ARM64 和 x86_64。APK 使用本机调试密钥签名，适合团队内部体验；正式分发需另设发布签名。安装包不提交到 Git。

1. 打开“设备”，选择演示阵列，点击“连接演示设备”。无需蓝牙或硬件。
2. 在“图形”选择圆、三角形等预设，或选择“自己绘制”。
3. 点击“发送到设备”。收到接收确认和匹配的回传图形后，播放按钮才可点击。
4. 点击“播放”，在手掌上观察移动亮点；可以暂停、继续和停止输出。

Demo 默认两秒一圈，以慢速说明路径与通信流程，不模拟实际皮肤感觉。真实设备也必须回传状态，界面才显示当前位置；手机不会用本地动画代替丢失的设备反馈。

## 2. 触屏绘图

| 操作 | 用法 |
|---|---|
| 直线、矩形 | 在任意位置拖动，确定端点或对角点 |
| 圆 | 从圆心向外拖动，确定半径 |
| 曲线 | 拖动生成曲线，再用“选择”拖动中间控制点调整弧度 |
| 手绘 | 单指连续描画，自动简化成路径 |
| 选择、移动 | 点选线条后拖动；矩形也可拖中央圆柄，其他图形可调整端点 |
| 尺寸、旋转 | 输入毫米尺寸或角度；圆设置直径，其他图形设置沿画布坐标轴的外接宽高 |
| 整体缩放 | 输入倍数，同时缩放所有图形及其位置 |
| 双指操作 | 缩放、平移视图，不改变图形实际毫米尺寸；右下角按钮恢复掌面视图 |
| 撤销、删除、顺序 | 撤回编辑、删除选中图形、调整各图形的呈现次序 |

画布内的触摸优先用于绘图；滑动画布外的区域可滚动页面。手机端暂不提供相切、平行等几何约束，也不支持只擦除矩形的一条边。曲线是三控制点二次曲线，尚非完整 CAD 样条编辑器。

手掌轮廓与电脑端共用同一组毫米坐标，缩小视图会自然显示更多手指。掌面作为推荐范围，明显超出时提示但不因手掌大小禁止发送；设备声明的工作空间与容量仍需遵守。手掌是参考图，不是位置检测。

多个图形按指定顺序呈现，默认绘制顺序；编译时去除已覆盖的共线片段，图形间关闭输出后转移。上限为 32 段、256 个编译坐标，复杂图形超限时提示简化。坐标与阵元数无关；Demo 可选 4×4、8×8、12×12、16×16，真实阵列以设备握手为准，切换阵列不缩放图形。

界面截图：[触屏草图](docs/phone-sketch.png)、[Demo 回传播放](docs/phone-playback.png)。截图由 Flutter 界面测试渲染，并非实体手机或触觉实测。

## 3. 图形库与文件互通

- “保存图形”存入本机图形库（最多 100 项）；进入“图形库”点击“打开图形”继续编辑。
- 图形库的“导入文件”和“导出当前图形”使用 Android 系统文件选择器，可交换电脑端的 JSON 文件，无需访问整个存储空间。[3]
- 支持 `haptics-config-2`、`haptics-config-3`；旧网格编号的版本 1 请先用电脑端转换。文件最大 64 KiB。
- 手机保存的 `mobile_sketch` 保留圆、矩形、曲线的原始控制点，并与编译路径校验。桌面文件导入手机保留坐标与配置，但桌面专用的尺寸关系和 `sketch` 约束不参与手机编辑，也不随再次导出保留。请保留原始文件。
- 打开文件或重新连接后必须重新发送确认，不自动恢复输出。卸载应用会清除本机图形库，需保留的图形请先导出。

## 4. BLE 连接

开启蓝牙，在“设备”点击“扫描设备”，允许系统权限，选择模块后连接。Android 12 及以上使用附近设备权限；较早版本扫描还需定位权限／服务。[1][2] 当前支持 BLE GATT，不支持经典蓝牙 SPP、USB 串口或多客户端同时控制。

“连接设置”可配置服务 UUID、手机发送／接收通道 UUID、写入方式及分包上限，自动保存到本机。默认使用与电脑端相同的 NUS 映射，详见 [BLE 对接说明](../desktop_app/BLE.md)。这不是已选定的硬件方案，模块还须配套实现 HAP3 的固件。

连接后先订阅通知再握手；命令按 MTU 拆成连续字节，不额外添加换行。GATT 写入完成不代表图形生效，必须收到应用层 ACK 和匹配 STATE。真实连接拒绝 `simulated=1`，失败时不会转入 Demo。单命令发送限 1.5 秒，ACK 限 2 秒，状态缺失 2.5 秒断开；帧最长 8192 字节。复杂图形与大阵列的实际无线吞吐尚待测量。

请保持应用在前台。进入后台或锁屏时中止扫描并断开连接；REMOTE 模式尽力发送 STOP，失联后不能保证命令已到达，板端必须有独立看门狗。LOCAL 模式允许板端按键自主控制。重连不会自动继续播放。

## 5. 开发与构建

已验证环境：Flutter 3.44.4／Dart 3.12.2、Java 21、Android SDK 36、AGP 8.13.0、Gradle 8.14.3、Kotlin 2.2.20。依赖版本由 `pubspec.lock` 固定。

在 `mobile_app/` 执行：

```powershell
flutter pub get
dart format lib test
flutter analyze
flutter test
flutter run
```

`flutter run` 需要已连接手机或已配置的 Android 模拟器。本机未连接手机、未配置模拟器。Windows 中文项目路径会触发当前 AOT 工具路径编码错误；构建脚本复制源码到独立英文目录，完成后将 APK 复制回 `dist/`：

```powershell
powershell -File mobile_app/build_android.ps1 -Flutter D:/Flutter/flutter/bin/flutter.bat -BuildRoot D:/codex-tmp/touchsee-builds
```

上条命令从仓库根目录运行。依赖已缓存时可加 `-Offline`，仅用于 Pub；Gradle 首次构建仍可能联网。脚本保留临时构建目录便于排错，不移动源码。若分析器也因中文路径报错，可在英文路径创建指向 `mobile_app` 的目录联接后运行分析与测试；APK 仍使用真实英文副本构建。

目录：`lib/main.dart` 为界面，`canvas.dart`／`sketch.dart` 为绘图，`model.dart`／`protocol.dart`／`session.dart` 为模型与会话，`transport.dart` 包含 BLE 和 Demo，`library.dart` 管理文件。Android 原生文件选择桥接位于 `android/app/src/main/kotlin/`。

## 6. 已完成验证与待联调项

29 项 Flutter 测试覆盖 CRC／破损帧恢复、32 组 Python 轨迹及相位向量、配置确认门槛、状态超时、BLE 分包与发送失败、连接取消、几何编译、旧图形另存与保存恢复。界面测试覆盖小屏、1.5 倍字体、旋转与尺寸、双指缩放不误画、Demo 发送与播放，截图输出到忽略的 `.runtime/screenshots/`。

从仓库根目录可运行 `python mobile_app/tool/generate_fixtures.py` 更新 Python 参考夹具；运行 Flutter 测试后执行 `python mobile_app/tool/check_desktop_exchange.py`，用电脑端导入器检查手机导出文件。参考夹具依赖电脑端的 Python 依赖。

BLE 测试使用假 GATT 对象，不等同于 Android 原生蓝牙栈实测。仍需实体手机验证权限、文件选择器、扫描和长帧收发，并与板端共同确认协议、容量、状态周期及看门狗。当前没有超声、触觉辨识或 FPGA 性能测量结果。

## 9. 参考资料

2026-09-25 对照本机安装的依赖源码与平台接口实现；下列资料用于软件接口说明。

1. [FlutterBluePlus 1.36.8](https://pub.dev/packages/flutter_blue_plus/versions/1.36.8)：扫描、GATT 通知、MTU 与写入；此锁定版本使用 BSD-3-Clause 许可，许可随 Flutter 打包保留。
2. [Android 蓝牙权限](https://developer.android.com/develop/connectivity/bluetooth/bt-permissions)：不同 Android 版本的权限范围。
3. [Android 系统文件选择器](https://developer.android.com/training/data-storage/shared/documents-files)：打开／创建文档。
