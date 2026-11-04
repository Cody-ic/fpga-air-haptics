"""Collect a portable archive, dependency notices and exact build versions."""

import argparse
from datetime import datetime, timezone
from hashlib import sha256
from importlib import metadata
import json
from pathlib import Path
import platform
import shutil
import sys
import zipfile


def main(dist_directory=None):
    app_dir = Path(__file__).resolve().parent
    dist = Path(dist_directory).resolve() if dist_directory else app_dir / 'dist'
    portable = dist / 'TouchSee'
    executable = portable / '触见图形工作台.exe'
    if not executable.is_file():
        raise FileNotFoundError(executable)
    # Keep the existing desktop shortcut's dist/EXE target working. Dependencies
    # live beside it, so launch never depends on writable system TEMP folders.
    shutil.copytree(portable/'_internal', dist/'_internal', dirs_exist_ok=True)
    shutil.copy2(executable, dist/executable.name)
    packages = {d.metadata['Name']: d.version for d in metadata.distributions()}
    record = dict(built_at=datetime.now(timezone.utc).isoformat(), python=platform.python_version(),
                  architecture=platform.machine(), executable=executable.name,
                  sha256=sha256(executable.read_bytes()).hexdigest(), packages=packages)
    (dist / 'build-info.json').write_text(json.dumps(record, ensure_ascii=False, indent=2), encoding='utf-8')
    instructions = (
        '触见图形工作台 · Windows 64 位便携版\n\n'
        '解压后双击“触见图形工作台.exe”，无需安装 Python。\n'
        '默认打开三维回放：内置 EgoPressure 366 帧真实掌心轻压样本，初始暂停。\n'
        '发送当前帧压力：固定所选帧 → 生成与预览 → 连接模拟设备 → 发送此帧；回读确认后单独播放。\n'
        '左键旋转、右键平移、滚轮缩放；时间轴/上一帧/下一帧和速度只控制离线数据。\n'
        '手部为 778 个拟合顶点与关节连线；当前没有完整手部表面或贴手压力纹理。\n'
        '数据使用须遵守 EgoPressure 的 CC BY-NC-SA 4.0 及 MANO 衍生标注许可，详见随包数据说明。\n'
        '工作空间：掌心绘图 / 播放查看 / 实验与设备；右侧检查器调整图形，底部固定发送和播放控制。\n'
        '连接设备：进入“实验与设备 → 设备设置”；右上角可开启调试模式。\n'
        '没有硬件：选择 Demo 模拟设备，连接后先发送图形，再播放。\n'
        '真实设备：选择真实串口、COM 端口和匹配的波特率，再连接。\n'
        'BLE：选择 BLE 蓝牙，扫描并选择模块，再连接；其他模块可修改连接设置。\n'
        '蓝牙模块尚未选型；设备指令暂用 HAP3 草案，需与 FPGA 固件共同确定并对接。\n'
        '图形需主动保存为 JSON。\n\n'
        '新建图形默认对齐按键实验：聚焦高度50 mm、每秒一圈、等级100%、不调制。\n'
        '已有图形可在检查器点击“套用按键实验参数”，再停止、发送确认、播放。\n'
        '实际观察平面应与聚焦高度一致；此参数预设不代表实测强度保证。\n\n'
        '接收板调试：打开调试模式→实测数据→接收波形，采集一次或连续查看。\n'
        'Demo 波形是合成数据。真实采样支持 F411/F103 固件，ADCV_P 接 PA0 并共地。\n'
        '本地播放时点击电脑控制，会先停止并确认，再切换模式；不会自动播放。\n'
        '每次采样自动保存原码 JSONL 与分析 CSV，可在接收波形页打开测量记录。\n'
        '记录目录：%LOCALAPPDATA%\\TouchSee\\measurements，每次连接独立保存。\n'
        '接收板只接 5 V 电源；默认分压还原倍数为 3.4，不能恢复前级削顶或换算声压。\n\n'
        '阵列参数：调试模式→设备设置，可导入 array-profiles 中的坐标作预测或 Demo。\n'
        '真实设备三维坐标由新版固件回传；球冠文件仅为软件示例，不是可打印的球冠夹具。\n'
        '相位校准：调试模式→实测数据→相位校准；先固定接收器并填写位置。\n'
        '校准完成保持停止，重新发送图形后播放。修正值断电丢失，可保存并重新导入。\n'
        'Demo 校准文件不能用于真实设备；目前尚无声学实测验证。\n\n'
        '请完整解压，保留程序旁的 _internal 文件夹；不要只复制 EXE。程序无需管理员权限。\n'
        '请保留 THIRD_PARTY_LICENSES 中的依赖许可与 build-info.json。\n'
        '启动失败日志：%LOCALAPPDATA%\\TouchSee\\logs\\startup-error.log\n'
    )
    (dist / '使用说明.txt').write_text(instructions, encoding='utf-8-sig')
    archive = dist / 'TouchSee-Windows-x64.zip'
    with zipfile.ZipFile(archive, 'w', compression=zipfile.ZIP_DEFLATED) as bundle:
        for path in (executable, dist / 'build-info.json', dist / '使用说明.txt'):
            bundle.write(path, path.name)
        for path in (portable/'_internal').rglob('*'):
            if path.is_file():
                bundle.write(path, path.relative_to(portable).as_posix())
        for path in (app_dir.parent/'hardware/arrays').glob('*.json'):
            bundle.write(path, 'array-profiles/'+path.name)
        bundle.write(app_dir/'ARRAY_CALIBRATION.md', '阵列与校准说明.md')
        bundle.write(app_dir/'UI_UPGRADE_2026-10-29.md', '界面与验证说明.md')
        bundle.write(app_dir/'EGOPRESSURE_REPLAY.md', '三维回放说明.md')
        bundle.write(app_dir/'PRESSURE_SINGLE_FRAME.md', '压力单帧说明.md')
        bundle.write(app_dir/'PRESSURE_FOCUS_2026-11-02.md', '双点增强说明.md')
        bundle.write(app_dir/'OUTPUT_STRENGTH_2026-10-31.md', 'OUTPUT_STRENGTH_2026-10-31.md')
        bundle.write(app_dir/'CONNECTION_DIAGNOSIS_2026-10-31.md', 'CONNECTION_DIAGNOSIS_2026-10-31.md')
        for path in (app_dir/'assets/egopressure').iterdir():
            if path.is_file() and path.suffix != '.npz':
                bundle.write(path, 'EgoPressure-data-notices/'+path.name)
        for distribution in metadata.distributions():
            name = distribution.metadata['Name']
            # Preserve full package metadata and shipped notices, including
            # notices for BLAS/LAPACK libraries bundled by NumPy and SciPy.
            for file in distribution.files or []:
                parts = file.parts
                if any(part.endswith('.dist-info') for part in parts) and (
                        file.name == 'METADATA' or any('license' in p.lower() or 'copying' in p.lower() or 'notice' in p.lower() for p in parts)):
                    source = Path(distribution.locate_file(file))
                    if source.is_file():
                        bundle.write(source, f'THIRD_PARTY_LICENSES/{name}/{file.as_posix()}')
        # python.org and Anaconda use different names for the same Python
        # license. Do not silently omit it when building inside a venv.
        base = Path(sys.base_prefix)
        candidates = (base / 'LICENSE.txt', base / 'LICENSE_PYTHON.txt')
        python_license = next((path for path in candidates if path.is_file()), None)
        if python_license is None:
            raise FileNotFoundError(f'Python license not found in {base}')
        bundle.write(python_license, 'THIRD_PARTY_LICENSES/Python-LICENSE.txt')
        for root in (base / 'tcl', base / 'Library' / 'lib'):
            for path in root.rglob('license.terms'):
                bundle.write(path, 'THIRD_PARTY_LICENSES/TclTk/'+path.relative_to(root).as_posix())
        # Conda keeps Tcl's notice in the installed package's metadata cache.
        for record_path in (base / 'conda-meta').glob('tk-*.json'):
            record = json.loads(record_path.read_text(encoding='utf-8'))
            package = f"{record['name']}-{record['version']}-{record['build']}"
            notices = base / 'pkgs' / package / 'info' / 'licenses'
            for path in notices.rglob('license.terms'):
                bundle.write(path, 'THIRD_PARTY_LICENSES/TclTk/'+path.relative_to(notices).as_posix())
    print(f'Created {executable.name}: {executable.stat().st_size / 1024**2:.1f} MiB')
    print(f'Created {archive.name}: {archive.stat().st_size / 1024**2:.1f} MiB')


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--dist', type=Path, help='Package an alternate build without replacing a running release')
    main(parser.parse_args().dist)
