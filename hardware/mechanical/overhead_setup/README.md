# P5 上发射、下接收固定高度支架

## 1. 适用布局

2026-10-15，P5。发射 PCB 水平固定在上方，换能器朝下；接收 PCB 水平朝上，放在独立平底托盘中，由人手平移定位。发射架和接收托盘无需连接，没有滑轨、升降槽、转轴或调节关节。

两种发射板共用左右侧架，换用各自的承载框：4×4 对应 R5、11 mm 间距；8×8 对应 R2、10.3 mm 间距。保留原 [P3](../README.md) 与 [P4](../vertical_setup/README.md)，不覆盖旧模型。

本版沿用早期布局的 **70 mm 名义声面间距**。这是机械参考，不表示已验证聚焦距离或触觉效果。

**2026-10-22 补充：**已打印的本版零件不用重打。新增[30 mm 固定增高底座与薄膜架](../overhead_addons/README.md)，可将名义声面间距增至 100 mm；原接收板托盘及接线空间保留。此页及原 P5 打印包仍记录原始 70 mm 模型，新增件使用单独的补充订单。

## 2. 打印件与文件

**学校数量说明（2026-10-20，订单 P5-SCHOOL-R2）：**两块发射板分别保留承载框、共用侧架，
整套装配／试配用量 18 件，另备套筒 4 个、压片 2 个，总计 24 件。
已备 7 种模型各 1 件时，本批补侧架 1、绝缘套筒 11、压片 5，共 17 件，已含备件。
使用[学校打印交付单](学校打印需求.md)和[逐件补充打印包](../P5_school_spares_20261020/TouchSee_P5_School_TOPUP_17pcs_spares_20261020.zip)，
包内 17 个模型文件，每文件打印 1 件。保留同形副本，不同时执行全量包。
原始学校包保留用于追溯几何；下表是一种发射配置的数量，并非本次两块板预装订单。

按所用 PCB 选择一个承载框，另一款不必同时打印。所有尺寸单位为 mm，打印比例为 100%；学校使用最终发布包中的 STL。零件准确包络、数量、网格与草图核验结果以最终 manifest 和核验报告为准，当前生成状态见第 7 节。

发布目录为 `hardware/mechanical/P5_20261015/`：完整CAD包 `TouchSee_Overhead_P5_20261015.zip`，学校打印包 `TouchSee_Overhead_STL_P5_20261015.zip`。生成工作目录位于忽略提交的 `hardware/mechanical/dist/P5-20261015/`；打包脚本只接受本版原生与网格核验均通过的输出。

`REF_*_electronics`、PCB/元件包络及参考紧固件用于装配和避空检查，**不是打印件**。学校只打印正式STL清单中的零件，不把参考电子元件、螺钉或整套装配体打印出来。

预览中的**琥珀色透明区域是50 mm参考线束空间，不是打印罩**；该区域实际保持开放。电子元件显示为PCB工程的XY位置与假设高度形成的包络，不是已测量的完整器件/线缆模型。

预览：[4×4总装](../P5_20261015/overhead_4x4.png)、[8×8总装](../P5_20261015/overhead_8x8.png)、[8×8底部视角](../P5_20261015/overhead_8x8_underside.png)、[接收托盘细节](../P5_20261015/receiver_tray_detail.png)、[接收托盘分解](../P5_20261015/receiver_tray_exploded.png)。

| STL 名称 | 用途 | 一套数量 | 名义外包络 |
|---|---|---:|---|
| `01_side_trestle.stl` | 共用侧架，左右各一 | 2 | 188×95.3×30 |
| `02_tx_carrier_4x4.stl` | R5 4×4 发射板承载框 | 1，与下一项二选一 | 182×190×10 |
| `03_tx_carrier_8x8.stl` | R2 8×8 发射板承载框 | 1，与上一项二选一 | 182×190×10 |
| `04_m3_rear_sleeve_6x5.stl` | 发射板背面绝缘抬高套筒 | 4 | 外径6、高5 |
| `05_receiver_flat_tray.stl` | 独立接收托盘 | 1 | 76×54.619×17.6 |
| `06_receiver_edge_clip.stl` | 接收板短压片 | 4 | 6×9.3×3 |
| `07_fastener_fit_coupon.stl` | 螺钉孔径试片 | 1，正式装配不计入 | 60×18×5 |

先试打孔径试片和接收托盘，再打印大件。试片四孔分别为Ø3.4、Ø3.6、Ø4.5、Ø4.7；螺母槽配合在接收托盘上核对。去除毛刺，修整打印孔，不整体缩放。最大承载框边长190，名义200打印平台仍需容纳裙边和切片机边界。

## 3. 按焊装后的 PCB 安装

坐标统一为 **看 PCB Top 面，板框左上角为原点，X 向右、Y 向下**。倒挂后发射板 Bottom 面朝上，不能直接照背面照片左右套用。

| PCB | 板框 | 安装孔/定位点 |
|---|---|---|
| 4×4 R5 | 80×88，圆角约5 | Ø3.2孔 `(6,6)`、`(74,6)`、`(20,82)`、`(60,82)`；阵列中心 `(38.5,38.5)` |
| 8×8 R2 | 141×151 | Ø3.2孔 `(4,4)`、`(137,4)`、`(4,147)`、`(137,147)`；阵列中心 `(83.5,65.5)` |
| 接收板 | 60×34.619，圆角5 | 无安装孔；接收头U1中心 `(25.273,24.491)` |

阵列中心和接收头中心均不是 PCB 板框中心。发射承载框通过原孔固定，不新增 PCB 孔；接收托盘只在四块短板边区域承托和压紧，不使用连续四边台阶压住元件。

接收接触区：上边 `X=27～33`、`45～51，Y=0.2～1.8`；下边 `X=9～15`、`47～53，Y=32.819～34.419`。相关元件、焊盘及孔周 XY 数据见 [pcb_geometry.json](pcb_geometry.json) 和 [pcb_contact_regions.json](pcb_contact_regions.json)。库外框只作为位置核对依据，不能当成真实完整焊装外形。

## 4. 固定高度及名义假设

以托盘和侧架共同放置的平桌面为 `Z=0`，正 Z 向上。

| 参考面 | 名义 Z |
|---|---:|
| 接收 PCB 背面 | 16.0 |
| 接收 PCB 正面 | 17.6 |
| 接收头声面 | 24.7 |
| 发射换能器声面 | 94.7 |
| 发射承载框下表面 | 95.3 |
| 发射 PCB 正面，朝下 | 105.3 |
| 发射 PCB 背面，朝上 | 106.9 |

参数见 [parameters.json](parameters.json)，坐标计算见 [layout.py](layout.py)。发射 PCB 正面至声面沿用旧设计 **10.6**，接收正面至声面取 **7.1**，PCB厚度取 **1.6**；接收托盘底板厚 **4**、板底凹腔 **12**，测试针向下伸出按 **8**，发射板满线背面区按 **50**。这些都是建模名义值，尚未逐项实测。10.6 包含旧设计采用的安装偏置，不等于换能器壳高。

若实际换能器焊高、PCB 厚度或打印尺寸不同，真实声面间距随之变化。应量声面外缘而非网罩中心；不得靠压网罩、硬弯引脚或压线强行匹配70 mm。改变高度参数后重新生成固定件，不增加活动调节机构。

## 5. 接头、元件和线束避让

- **发射板上方开放**：4×4 的 CN1、8×8 的 H1 插满控制线；电解和焊脚也位于上方。50 mm 满线区不得再加顶盖、横梁或堆放开发板。线束向侧面引出并卸力，不能挂在接头焊点上。
- **发射 XT30**：4×4 靠下板边，8×8 位于右下区域；保留实际插头的插拔、捏握和转弯空间，不能把电源线夹在板与承载框之间。
- **接收 4 pin**：U4位于右侧 `(55.245,16.744)`。上方与右侧 `Y约10～24` 同时开放，允许插头及线束上提后转向右侧；托盘移动时留线长，不让线缆拖拽PCB。
- **接收 XT30/SW1**：左侧XT30开放；下侧SW1本体已超出板框约1.6，相关区域留缺口。
- **接收板底面**：C5/C6、RH-5000测试环和焊脚朝下，悬在12 mm凹腔内；托盘外底面保持平整。8 mm测试针伸出是假设，实际较长时先调整打印件，不能让板底焊点承重。

## 6. 组装与使用

完整零件、螺钉、垫圈及装配顺序见 [装配清单](装配清单.md)。全部采用标准螺钉固定，不需要胶水或在 PCB 上新增孔。

先用四套 M3×25 将发射板安装在对应承载框，再用四套 M4×30 将承载框连接左右侧架。发射板背面每孔放外径6、高5的绝缘套筒，抬起外径7的垫圈及锁紧螺母；4×4下侧两孔附近的D1/F1不允许被大垫圈贴板挤压。

接收托盘的四个侧向螺母槽使用 **DIN 934 普通 M3 六角螺母，高2.4**，配 M3×16和短压片。**不可替换为更高的尼龙锁紧螺母**。压片只贴所列板边，轻拧至无晃动，不压弯PCB。

接收托盘独立平移，将U1的中心投影对准发射阵列参考点。保持桌面平坦、两底座不垫入不等厚物件。拆装 PCB 或插拔线束前断开供电；夹具定位不等于已验证声场。

## 7. 生成与核验状态

2026-10-15，独立网格核验 `validation.json` 已通过：共18个STL，其中7类打印件通过封闭性、法向、连通实体与CAD体积一致性检查；最大边长190 mm，最大体积相对误差约0.0185%。参考电子包络采用有限坐标、体积和边界检查，不作为可制造网格验收。

两款发射框的原PCB孔位与轴线、发射朝下/接收朝上及70 mm名义声面间距均已核对。16与64个发射头共2640个投影探针未发现承载框遮挡；接收板四处边缘承托、12 mm底空腔及三个接头/开关侧向窗口检查通过。

接收托盘在XY各±36.05 mm连续平移的保守扫掠包络，与固定支架保持分离；右侧最小余量约3.223 mm，**不包含真实插头和线缆弯曲空间**。这不是可无条件使用的实物扫描行程。

原生核验 `native_validation.json` 已通过：重新打开并检查18个SLDPRT和2个SLDASM，无原生重建错误；123张草图全部为完全定义状态3，Fix固定关系数为0。两种配置的BREP组件干涉均为0，零件引用路径与装配变换已核对。参考电子包络内部的重叠不计为组件干涉，不将其等同真实电子器件的精确模型。本节采用P5本版结果，不沿用旧P3/P4结论。

打印配合、实际接头/线束净空、承重稳定性和声学效果尚无实测。核验报告与最终 manifest 完成后再更新发布包和本节状态。

## 8. 修改与复用

两种承载框共享同一侧架连接孔和固定高度；更换 PCB 时只换承载框及板子。接收托盘独立保留。输入来源、板框、安装孔、阵列中心、短接触区和开窗必须一起复核，不能只改行列数。

参数或原生CAD修改后重新检查：四个发射安装孔、上下声面间距、PCB两面元件与紧固件、满线连接器、接收压片/螺母槽及托盘平底。保留原稿和旧版本，重新导出匹配的新 STL。

在仓库根目录使用PowerShell依次执行。需要已安装的SolidWorks、当前Python环境及Blender；更换电脑先核对脚本中的模板和程序路径。运行生成脚本会更新本版输出，手工编辑的CAD应先另存。

脚本：[生成零件](build_overhead.py)、[生成装配](assemble_overhead.py)、[原生核验](check_overhead.py)、[网格核验](validate_overhead.py)、[预览渲染](render_overhead.py)、[发布打包](package_overhead.py)。

```powershell
& D:/python/python.exe hardware/mechanical/overhead_setup/build_overhead.py
& D:/python/python.exe hardware/mechanical/overhead_setup/assemble_overhead.py
& D:/python/python.exe hardware/mechanical/overhead_setup/check_overhead.py
& D:/python/python.exe hardware/mechanical/overhead_setup/validate_overhead.py
& 'C:/Program Files/Blender Foundation/Blender 5.2/blender.exe' --background --factory-startup --python hardware/mechanical/overhead_setup/render_overhead.py
& D:/python/python.exe hardware/mechanical/overhead_setup/package_overhead.py
# 学校逐件数量包：校验原始模型，展开为含备件的全量 24 件与补充 17 件。
& D:/python/python.exe hardware/mechanical/overhead_setup/package_school.py
```

先完成原生与网格核验，再渲染和打包。命令列出复现顺序，不表示本轮核验已经通过。

## 9. 尺寸依据与资料

- [4×4 R5 工程与贴片坐标](../../Haptics_4x4_R5_12VDC/README.md)、[8×8 R2 工程变更 PR #15](https://github.com/Cody-ic/fpga-air-haptics/pull/15)：本版读取板框、孔位、元件面和坐标，不修改 PCB。接收来源为用户提供的 `ProPrj_接收端_布线优化_2026-09-28_2026-10-10.epro2`；来源SHA256记录在 `pcb_geometry.json`。
- [P3/P4 参数依据](../vertical_setup/parameters.json)：沿用旧发射焊装偏置10.6和名义换能器高度7.1；本版改为上下布局，不能沿用旧装配的干涉结论。
- [Murata MA40S4S/R 官方资料](https://www.murata.com/-/media/webrenewal/products/sensor/ultrasonic/open/datasheet_maopn.ashx)：DM-U16-483 Rev.2，2017-08，壳体名义外径9.9、壳高7.1及其公差；不包含实际离板焊高。
- [Panasonic EEUFR1V102](https://industrial.panasonic.com/ww/products/pt/aluminum-cap-lead/models/EEUFR1V102)：壳体Ø12.5×20、引脚间距5；本次复核日期2026-10-15。
- [Bossard DIN 934 普通六角螺母](https://www.bossard.com/global-en/eshop/hex-nuts/hex-nuts-0-8d/p/115/)及[制造商CAD尺寸表](https://kvt.partcommunity.com/3d-cad-models/?info=bossard%2F01%2F01_100%2F01_100_200%2F01_100_200_10%2Fbn_115_116_117_20237_20539_20540_118_82415_119%2Fbn_115.prj&languageIso=en)：M3名义对边5.5、高2.4；本次检索日期2026-10-15。槽高2.8为本版打印配合留量，不是标准螺母尺寸。
- [DIN 985 锁紧螺母尺寸](https://www.rcfastener.com/documents/RC_Specs/Metric_Locknut_Hex_Nylon_Insert_DIN985.pdf)、[M3平垫圈尺寸](https://marketplacemedia.witglobal.net/source/marketplace/stmedia/wuerth/documents/documents/std.lang.all/29208623.pdf)：沿用P4在2026-10-07核验的名义包络，M3锁紧螺母高4、垫圈Ø7×0.5；与接收托盘普通螺母区别使用。
- [SolidWorks 草图状态定义](https://help.solidworks.com/2022/English/SolidWorks/sldworks/c_Sketch_Status_Conventions.htm)：作为完全定义草图检查依据，不能替代本版实际生成后的检查结果。
