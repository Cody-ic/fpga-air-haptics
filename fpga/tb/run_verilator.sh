#!/usr/bin/env bash
# 用 Verilator 编译并运行 HAP2 接收通路的仿真。
#
#   cd fpga
#   bash tb/run_verilator.sh
#
# 必须在 fpga/ 目录下运行：测试台用相对路径 tb/vectors/ 读向量文件。
# 默认在 $HOME/.cache/hap2_sim 下编译，不往仓库里塞编译产物；
# 可以用 BUILD_DIR=... 覆盖。
set -euo pipefail

cd "$(dirname "$0")/.."

if [ ! -f tb/vectors/packets.mem ] || [ ! -f tb/vectors/plan.mem ]; then
    echo "缺少向量文件，请先在仓库根目录执行：python fpga/tb/gen_vectors.py" >&2
    exit 1
fi

BUILD_DIR="${BUILD_DIR:-$HOME/.cache/hap2_sim}"
mkdir -p "$BUILD_DIR"

# -Wno-WIDTH：Verilog-2001 里常量算术一律按 32 位算，赋给窄位寄存器时
# Verilator 会报大量位宽提示。这些在综合工具里无害，这里统一关闭。
# -Wno-PINMISSING：有些输出端口是有意不接的（例如只在调试时才看），
# 这类“端口没接”的提示在这里不需要。
verilator --binary --timing -j 4 -Wall -Wno-fatal -Wno-WIDTH -Wno-PINMISSING \
          --top-module tb_hap2_rx -o hap2_rx_sim -Mdir "$BUILD_DIR/obj" \
          rtl/uart_rx.v rtl/uart_tx.v rtl/dec_ascii.v rtl/hap2_line_rx.v \
          rtl/crc16_ccitt.v rtl/hap2_frame_check.v rtl/hap2_field_parse.v \
          rtl/hap2_cmd.v rtl/hap2_tx.v rtl/hap2_watchdog.v rtl/hap2_rx.v \
          rtl/hap2_scan_parse.v \
          tb/tb_hap2_rx.v

echo "-----------------------------------------"
"$BUILD_DIR/obj/hap2_rx_sim"

echo
echo "=== 算术单元（开方 + 除法）==="
verilator --binary --timing -j 4 -Wall -Wno-fatal -Wno-WIDTH -Wno-PINMISSING \
          --top-module tb_fix_units -o fix_units_sim -Mdir "$BUILD_DIR/obj_fix" \
          rtl/fix_sqrt.v rtl/fix_div.v tb/tb_fix_units.v
echo "-----------------------------------------"
"$BUILD_DIR/obj_fix/fix_units_sim"

echo
echo "=== 轨迹通路（节拍表 + 走步器）==="
verilator --binary --timing -j 4 -Wall -Wno-fatal -Wno-WIDTH -Wno-PINMISSING \
          --top-module tb_traj -o traj_sim -Mdir "$BUILD_DIR/obj_traj" \
          rtl/fix_sqrt.v rtl/fix_div.v rtl/hap2_traj_plan.v rtl/hap2_traj_walk.v \
          tb/tb_traj.v
echo "-----------------------------------------"
"$BUILD_DIR/obj_traj/traj_sim"

echo
echo "=== 相位计算（焦点 → 相位码，与参考实现逐通道对拍）==="
verilator --binary --timing -j 4 -Wall -Wno-fatal -Wno-WIDTH -Wno-PINMISSING \
          --top-module tb_phase -o phase_sim -Mdir "$BUILD_DIR/obj_phase" \
          rtl/fix_sqrt.v rtl/fix_div.v rtl/hap2_phase.v tb/tb_phase.v
echo "-----------------------------------------"
"$BUILD_DIR/obj_phase/phase_sim"

echo
echo "=== 输出级（相位码 → 引脚上的方波，量频率和相位）==="
verilator --binary --timing -j 4 -Wall -Wno-fatal -Wno-WIDTH -Wno-PINMISSING \
          --top-module tb_out -o out_sim -Mdir "$BUILD_DIR/obj_out" \
          rtl/fix_sqrt.v rtl/fix_div.v rtl/hap2_phase.v rtl/hap2_out.v tb/tb_out.v
echo "-----------------------------------------"
"$BUILD_DIR/obj_out/out_sim"

echo
echo "=== 端到端（整条通路，对着串口线）==="
verilator --binary --timing -j 4 -Wall -Wno-fatal -Wno-WIDTH -Wno-PINMISSING \
          --top-module tb_hap2_top -o hap2_top_sim -Mdir "$BUILD_DIR/obj_top" \
          rtl/uart_rx.v rtl/uart_tx.v rtl/dec_ascii.v rtl/crc16_ccitt.v \
          rtl/hap2_line_rx.v rtl/hap2_frame_check.v rtl/hap2_field_parse.v \
          rtl/hap2_cmd.v rtl/hap2_tx.v rtl/hap2_watchdog.v rtl/hap2_rx.v \
          rtl/hap2_scan_parse.v rtl/fix_sqrt.v rtl/fix_div.v \
          rtl/hap2_traj_plan.v rtl/hap2_traj_walk.v rtl/hap2_traj.v \
          rtl/hap2_traj_scan.v rtl/hap2_phase.v rtl/hap2_out.v \
          rtl/hap2_top.v tb/tb_hap2_top.v
echo "-----------------------------------------"
"$BUILD_DIR/obj_top/hap2_top_sim"
