"""Collect a portable archive, dependency notices and exact build versions."""

from datetime import datetime, timezone
from hashlib import sha256
from importlib import metadata
import json
from pathlib import Path
import platform
import sys
import zipfile


def main():
    app_dir = Path(__file__).resolve().parent
    dist = app_dir / 'dist'
    executable = dist / '触见图形工作台.exe'
    if not executable.is_file():
        raise FileNotFoundError(executable)
    packages = {d.metadata['Name']: d.version for d in metadata.distributions()}
    record = dict(built_at=datetime.now(timezone.utc).isoformat(), python=platform.python_version(),
                  architecture=platform.machine(), executable=executable.name,
                  sha256=sha256(executable.read_bytes()).hexdigest(), packages=packages)
    (dist / 'build-info.json').write_text(json.dumps(record, ensure_ascii=False, indent=2), encoding='utf-8')
    instructions = (
        '触见图形工作台 · Windows 64 位便携版\n\n'
        '解压后双击“触见图形工作台.exe”，无需安装 Python。\n'
        '没有硬件：选择 Demo 模拟设备，连接后先发送图形，再播放。\n'
        '真实设备：选择真实串口、COM 端口和匹配的波特率，再连接。\n'
        'BLE：选择 BLE 蓝牙，扫描并选择模块，再连接；其他模块可修改连接设置。\n'
        'XIAO MG24 使用默认蓝牙服务；需配套构建接收后端为 mg24 的 FPGA 程序。\n'
        '图形需主动保存为 JSON。\n\n'
        '声场页→接收板测点：默认底面坐标，原点为左增高底座左前外角。\n'
        'X向右、Y向后；Z为接收面离桌高度。居中接收头R=(132,94,24.7) mm。\n'
        '可切换填写托盘左前角A，保持4针口向右；查看坐标示意图核对方向。\n'
        '本坐标适用于30mm增高版P5支架，仍需核实实际尺寸；旧阵列坐标也可继续使用。\n'
        '测点导出同时保留原始波形、底面坐标与阵列坐标。\n'
        '串口收发自动镜像：%LOCALAPPDATA%\\TouchSee\\live，电脑助手可读同一份记录。\n'
        '其中latest.json指向本次连接，snapshot.json为最新状态，traffic.jsonl为逐条收发。\n\n'
        '接收板调试：打开调试模式→实测数据→接收波形，采集一次或连续查看。\n'
        'Demo 波形是合成数据。MG24 方案将 ADCV_P 接 D0 并共地；旧 STM32 原型仍接 PA0。\n'
        'MG24 的电脑控制入口是它自身的 USB 串口或蓝牙，非 FPGA 的调试 USB 串口。\n'
        '量程与采样率采用设备报告值；MG24 标称量程为 1210 mV，未做声压标定。\n'
        '本地播放时点击电脑控制，会先停止并确认，再切换模式；不会自动播放。\n'
        '每次采样自动保存原码 JSONL 与分析 CSV，可在接收波形页打开测量记录。\n'
        '记录目录：%LOCALAPPDATA%\\TouchSee\\measurements，每次连接独立保存。\n'
        '接收板只接 5 V 电源；默认分压还原倍数为 3.4，不能恢复前级削顶或换算声压。\n\n'
        '阵列参数：调试模式→设备设置，可导入 array-profiles 中的坐标作预测或 Demo。\n'
        '真实设备三维坐标由新版固件回传；球冠文件仅为软件示例，不是可打印的球冠夹具。\n'
        '相位校准：调试模式→实测数据→相位校准；先固定接收器并填写位置。\n'
        '校准完成保持停止，重新发送图形后播放。修正值断电丢失，可保存并重新导入。\n'
        'Demo 校准文件不能用于真实设备；目前尚无声学实测验证。\n\n'
        '首次启动需要解包运行库，请稍等。程序无需管理员权限。\n'
        '请保留 THIRD_PARTY_LICENSES 中的依赖许可与 build-info.json。\n'
        '启动失败日志：%LOCALAPPDATA%\\TouchSee\\logs\\startup-error.log\n'
    )
    (dist / '使用说明.txt').write_text(instructions, encoding='utf-8-sig')
    archive = dist / 'TouchSee-Windows-x64.zip'
    with zipfile.ZipFile(archive, 'w', compression=zipfile.ZIP_DEFLATED) as bundle:
        for path in (executable, dist / 'build-info.json', dist / '使用说明.txt'):
            bundle.write(path, path.name)
        for path in (app_dir.parent/'hardware/arrays').glob('*.json'):
            bundle.write(path, 'array-profiles/'+path.name)
        bundle.write(app_dir/'SOUND_FIELD.md', '声场与测绘说明.md')
        bundle.write(app_dir/'MEASUREMENT_COORDINATES.md', '底面坐标与实时记录.md')
        bundle.write(app_dir/'ARRAY_CALIBRATION.md', '阵列与校准说明.md')
        bundle.write(app_dir.parent/'firmware/xiao_mg24/README.md', 'MG24接线与使用说明.md')
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
        bundle.write(Path(platform.__file__).parents[1] / 'LICENSE.txt', 'THIRD_PARTY_LICENSES/Python-LICENSE.txt')
        for path in (Path(sys.base_prefix) / 'tcl').rglob('license.terms'):
            bundle.write(path, 'THIRD_PARTY_LICENSES/TclTk/'+path.relative_to(Path(sys.base_prefix) / 'tcl').as_posix())
    print(f'Created {executable.name}: {executable.stat().st_size / 1024**2:.1f} MiB')
    print(f'Created {archive.name}: {archive.stat().st_size / 1024**2:.1f} MiB')


if __name__ == '__main__':
    main()
