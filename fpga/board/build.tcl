# Gowin 命令行：建工程 + 综合 + 布局布线（一条命令跑完）
#
# 用法（在 fpga/board 目录下）：
#   "<Gowin 安装目录>\IDE\bin\gw_sh.exe" build.tcl
#
# 它会做四件事：
#   1. 在当前目录建一个工程（已存在就覆盖），器件固定成板子上那颗
#   2. 把 ../../rtl/*.v 全部加进设计文件
#   3. 加物理约束（引脚）和时序约束（时钟）
#   4. 顶层设为 board_top，然后 run all（综合 + 布局布线）
#
# 产物都在这个目录下（impl/ 里放报告），.gitignore 已经忽略，不会污染仓库。

set root [file normalize [file dirname [info script]]]
cd $root

# ---- 1. 建工程 ----
# 器件：GW5AT-LV60PG484A，封装 PBGA484A，器件版本 B（和板子上那颗一致）
create_project -name haptics60k -dir $root -pn GW5AT-LV60PG484AC1/I0 \
    -device_version B -force

# ---- 2. 设计文件（自动按扩展名认类型，.v = Verilog）----
set srcs [lsort [glob -nocomplain ../../rtl/*.v]]
if {[llength $srcs] == 0} {
    puts "ERROR: 没找到 ../../rtl/*.v，请确认这个脚本放在 fpga/board/ 下"
    exit 1
}
puts "设计文件 [llength $srcs] 个"
add_file {*}$srcs

# ---- 3. 约束 ----
# 注意：必须给**绝对路径**。工程文件建在 $root/haptics60k/ 里，布局布线阶段
# 会拿工程目录当基准去解析相对路径，写成 tang_mega_60k.cst 就会找不到
# （报 CM2001: Cannot open file）。
add_file [file join $root tang_mega_60k.cst]
add_file [file join $root tang_mega_60k.sdc]

# ---- 4. 顶层 + 跑 ----
set_option -top_module board_top
set_option -output_base_name board_top

puts "=== 开始综合 ==="
run syn
puts "=== 开始布局布线 ==="
run pnr
puts "=== BUILD-DONE ==="
exit
