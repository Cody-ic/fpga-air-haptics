# Gowin 命令行：建工程 + 综合 + 布局布线（一条命令跑完）
#
# 用法（在 fpga/board 目录下）：
#   "<Gowin 安装目录>\IDE\bin\gw_sh.exe" build.tcl
#
# 它会做四件事：
#   1. 在当前目录建一个工程（已存在就覆盖），器件固定成板子上那颗
#   2. 把脚本旁 ../rtl/*.v 全部加进设计文件（使用绝对路径）
#   3. 加物理约束（引脚）和时序约束（时钟）
#   4. 顶层设为 board_top，然后 run all（综合 + 布局布线）
#
# 产物都在这个目录下（impl/ 里放报告），.gitignore 已经忽略，不会污染仓库。

set root [file normalize [file dirname [info script]]]
cd $root

# 默认4×4直接输出；8×8使用R2移位接口和独立顶层/约束。
set array_profile 4x4
if {[info exists ::env(HAPTICS_ARRAY)]} {
    set array_profile $::env(HAPTICS_ARRAY)
}
if {$array_profile ni {4x4 8x8}} {
    error "HAPTICS_ARRAY must be 4x4 or 8x8"
}
set device_version B
if {[info exists ::env(HAPTICS_DEVICE_VERSION)]} {
    set device_version $::env(HAPTICS_DEVICE_VERSION)
}
if {$device_version ni {B C}} {
    error "HAPTICS_DEVICE_VERSION must be B or C; verify the chip marking"
}

# 默认输出到 J23；HAPTICS_PINOUT=pmod 可重新构建已经上板验证的旧接线。
# 两套工程使用不同目录，J23 构建不会覆盖旧 PMOD 比特流。
set pinout j23
if {[info exists ::env(HAPTICS_PINOUT)]} {
    set pinout $::env(HAPTICS_PINOUT)
}
switch -- $pinout {
    j23 {
        set project_name haptics138kpro_j23
        set pin_constraints tang_mega_138k_pro.cst
    }
    pmod {
        set project_name haptics138kpro
        set pin_constraints tang_mega_138k_pro_pmod.cst
    }
    default {
        error "HAPTICS_PINOUT must be j23 or pmod"
    }
}
set top_module board_top
if {$array_profile eq "8x8"} {
    if {$pinout ne "j23"} {
        error "8x8 requires the J23-to-H1 shift-register wiring"
    }
    set project_name haptics138kpro_8x8
    set pin_constraints tang_mega_138k_pro_8x8.cst
    set top_module board_shift_top
}
puts "Array: $array_profile; pinout: $pinout; device: $device_version; top: $top_module"
puts "Project: $project_name; constraints: $pin_constraints"

# ---- 1. 建工程 ----
# 器件：**GW5AST-138B，封装 FCPBG676A（676 球），型号 GW5AST-LV138FPG676AC1/I0**。
# 2026-10-14 定案：板子是 **Tang MEGA 138K Pro**，芯片 GW5AST-LV138FPG676A，
# 和之前按 60K、以及按 138K（非 Pro，484 球）建的工程都不是同一颗 ✗ ——
# 676 球和 484 球的球位编号毫无对应关系，引脚必须照这块板子的资料重排
# （现在是 tang_mega_138k_pro.cst，来源见那个文件开头的注释）。
#
# ⚠️ **教育版 IDE 的器件库里没有 676 球这个封装**（只有 PBGA484A），
#    官方也写明「138K Pro 目前未被教育版支持，需要 V1.9.9 或更新的商业版 IDE」✗。
#    所以这个脚本要配**商业版（Standard）**的 Gowin IDE 跑。
#    商业版授权不用买：Sipeed 提供免费在线 Float Lic，填 ip 106.55.34.119 / port 10559
#    （不通就用域名 gowinlic.sipeed.com 对应的 IP）。
#
# B/C可能共享JTAG识别码，不能单凭下载器读数区分。核对芯片表面版本标记后，
# 设置 HAPTICS_DEVICE_VERSION=B 或 C；此处默认B只为保留旧构建行为。
create_project -name $project_name -dir $root -pn GW5AST-LV138FPG676AC1/I0 \
    -device_version $device_version -force

# ---- 2. 设计文件（自动按扩展名认类型，.v = Verilog）----
set srcs [lsort [glob -nocomplain [file join $root .. rtl *.v]]]
if {[llength $srcs] == 0} {
    puts "ERROR: 没找到 fpga/rtl/*.v，请确认这个脚本放在 fpga/board/ 下"
    exit 1
}
puts "设计文件 [llength $srcs] 个"
add_file {*}$srcs

# ---- 3. 约束 ----
# 注意：必须给**绝对路径**。工程文件建在 $root/haptics138kpro/ 里，布局布线阶段
# 会拿工程目录当基准去解析相对路径，写成 tang_mega_138k_pro.cst 就会找不到
# （报 CM2001: Cannot open file）。
add_file [file join $root $pin_constraints]
add_file [file join $root tang_mega_138k_pro.sdc]

# ---- 4. 顶层 + 跑 ----
set_option -top_module $top_module
set_option -output_base_name $top_module

# 把 CPU 配置复用脚（D00~D15）当普通 IO 用。这一条是必须的：
# 板上两个 PMOD 里 P19/R19/T21/U21 就是 D06/D07/D04/D05，不放开的话布局阶段直接报
#   PR2017 : 'array_pos[0]' cannot be placed ... dedicated pin (CPU)
# 试过改 IDE\share\config\device.cfg 和工程里的 impl\pnr\device.cfg 都没用
# （每次跑都会被重新生成）；只有这个命令行开关真的有效，实测生成文件里会变成
#   set CPU regular_io = true
set_option -use_cpu_as_gpio 1

# 只放开 CPU 还不够：138K 上这 5 个脚是「双功能」脚，一半属于 CPU 配置口、
# 一半属于 SSPI 配置口——器件文件里写着
#   P19 = D06/SSPI_CLK   R19 = D07/SSPI_WPN   U21 = D05/SI
#   T20 = D08/SO         Y21 = SSPI_CS_N（只属于 SSPI）
# 60K 上它们没有这层 SSPI 别名，所以以前只开 CPU 就能过 ✗；换到 138K 之后
# 布局直接报
#   PR2017 : 'array_pos[0]' cannot be placed ... dedicated pin (CPU/SSPI)
# 官方 138K 例程（pmod_led）的工程配置里 CPU 和 SSPI 两项都是 true ✓，
# 实测补上这一条之后这 5 个脚立刻能布局（138K 那版工程里验证过；该验证工程是临时的，已删）。
set_option -use_sspi_as_gpio 1

# 时序报告默认只打印最差的一小批路径（我们一直是 50 条），于是"修掉 20 条之后
# 又有 21 条顶上来"，看起来像白改 ✗。把这个数字放大，一次就能看到**所有**违例路径，
# 也就能一次改完、只跑一次综合验证。
set_option -num_critical_paths 500
set_option -num_startend_points 500

# 关掉乘法器"资源共享"。综合器为了省 DSP，会把多处乘法复用同一个 DSP，
# 于是在 DSP 输入端造出一个很大的多路选择器——16 路版最差那条 −1.65 ns
# （`u_plan/mult_937` 的输入侧）就是它。这颗芯片有 118 个 DSP，我们只用了 24 个，
# 不值得为省资源牺牲时序。
set_option -resource_sharing 0

puts "=== 开始综合 ==="
run syn
puts "=== 开始布局布线 ==="
run pnr
puts "=== BUILD-DONE ==="
exit
