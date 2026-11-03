# 机械物理检查报告 · ArcMask v0.4 B 分体内架（六件）实体物理筛选

> 由 `mechsim` v0.1.0 生成于 2026-11-02T11:35:38，用时 115.5 s。报告只回答“数字上是否站得住”，不替代实物试装。

| 总判定 | 通过 | 不通过 | 未验证 | 参考 |
|---|---|---|---|---|
| **FAIL** | 29 | 14 | 6 | 13 |

**实物待验（本报告不会替你关掉）**：
- 真实元件/焊点/插头与所列包络的差异（未在本规格内）
- 实际螺丝/螺母批次尺寸与扭矩手感
- 相机/IMU 最终接触面与约 0.1 mm 薄定位层的真实厚度
- FPC 整条路线、弯曲半径与光学视场
- 原线束长度/走向/止拉与理线座夹持
- PETG 层间强度与 4 墙/20% 填充的实际等效性能（材料库为文献值，需试片标定）
- 清支撑后的表面/根部强度、热变形与佩戴压力分布

## 必须先修的问题

### B01_common_frame.geometry.winding

- 实测/计算：0.07835（限值 0.999）
- 方法：有符号体积/绝对体积和
- 说明：绕向不一致会让体积、惯性矩、内外判定全部失效

### B02_mb_cradle.geometry.winding

- 实测/计算：0.1067（限值 0.999）
- 方法：有符号体积/绝对体积和
- 说明：绕向不一致会让体积、惯性矩、内外判定全部失效

### B02_mb_cradle.print.min_wall

- 实测/计算：0.75 mm（限值 1.2 mm）
- 方法：表面采样点沿内法线射线求局部厚度（p05）
- 说明：min=0.75 p05=0.75 p50=2.48；射线法局部厚度；尖角/相切面处可能读到相邻壁而不是真实厚度，需结合截面图看

### B03_camera_cradle.geometry.winding

- 实测/计算：0.2648（限值 0.999）
- 方法：有符号体积/绝对体积和
- 说明：绕向不一致会让体积、惯性矩、内外判定全部失效

### B04_imu_cradle.geometry.winding

- 实测/计算：0.1468（限值 0.999）
- 方法：有符号体积/绝对体积和
- 说明：绕向不一致会让体积、惯性矩、内外判定全部失效

### B05_camera_cap.geometry.winding

- 实测/计算：0.2414（限值 0.999）
- 方法：有符号体积/绝对体积和
- 说明：绕向不一致会让体积、惯性矩、内外判定全部失效

### B06_imu_keeper.geometry.winding

- 实测/计算：0.1093（限值 0.999）
- 方法：有符号体积/绝对体积和
- 说明：绕向不一致会让体积、惯性矩、内外判定全部失效

### assembly.clearance.B03_camera_cradle_B05_camera_cap

- 实测/计算：-2.228 mm（限值 0.15 mm）
- 方法：双向表面采样 + 有符号距离
- 说明：最小间隙 0.000 mm，穿透深度 2.228 mm，穿透采样点 137

### assembly.insertion.B05_camera_cap

- 实测/计算：11.85（限值 —）
- 方法：沿装配方向逐步推进 + 有符号距离
- 说明：被 B03_camera_cradle 挡在剩余 11.8 mm 处

### joint.IMU 限位条 M2×16 ×2（进钢螺母）

- 实测/计算：0.63 安全系数（限值 2 安全系数）
- 方法：预紧力/承压/螺纹拉脱/螺丝拉伸
- 说明：夹紧力 375 N，承压 33.8 MPa（许用 21.4），拉脱能力 1608 N
- 假设：扭矩按经验 K=0.2 估算；塑料件实际应使用扭矩限制或凭手感，过度拧紧会压塌承压面或使柱根开裂
- 假设：拧入钢螺母/嵌件：螺纹不控制，实际由塑料承压面与柱根控制

### joint.主板 M2×12 ×4

- 实测/计算：0.63 安全系数（限值 2 安全系数）
- 方法：预紧力/承压/螺纹拉脱/螺丝拉伸
- 说明：夹紧力 375 N，承压 33.8 MPa（许用 21.4），拉脱能力 1608 N
- 假设：扭矩按经验 K=0.2 估算；塑料件实际应使用扭矩限制或凭手感，过度拧紧会压塌承压面或使柱根开裂
- 假设：拧入钢螺母/嵌件：螺纹不控制，实际由塑料承压面与柱根控制

### joint.共架-旧壳 M2 ×6

- 实测/计算：0.48 安全系数（限值 2 安全系数）
- 方法：预紧力/承压/螺纹拉脱/螺丝拉伸
- 说明：夹紧力 500 N，承压 45.0 MPa（许用 21.4），拉脱能力 2011 N
- 假设：扭矩按经验 K=0.2 估算；塑料件实际应使用扭矩限制或凭手感，过度拧紧会压塌承压面或使柱根开裂
- 假设：拧入钢螺母/嵌件：螺纹不控制，实际由塑料承压面与柱根控制

### joint.相机压盖 M2×8 ×2（进钢螺母）

- 实测/计算：0.63 安全系数（限值 2 安全系数）
- 方法：预紧力/承压/螺纹拉脱/螺丝拉伸
- 说明：夹紧力 375 N，承压 33.8 MPa（许用 21.4），拉脱能力 1608 N
- 假设：扭矩按经验 K=0.2 估算；塑料件实际应使用扭矩限制或凭手感，过度拧紧会压塌承压面或使柱根开裂
- 假设：拧入钢螺母/嵌件：螺纹不控制，实际由塑料承压面与柱根控制

### tolerance.M2 螺丝穿过主板 Ø2.2 孔（孔距 32.6 已确认）

- 实测/计算：[-0.25, 0.25]（限值 [-0.15, 0.15]）
- 方法：最坏情况 + RSS
- 说明：名义 0.000；主要贡献：打印柱位（FDM 定位误差） 60.00%，主板孔位（官方图/实测） 40.00%

## 零件质量与几何

| 件 | 材料 | 体积 mm³ | 实体质量 g | 质心 mm | 水密 | 最小壁厚 p05 mm | 需支撑面积 mm² |
|---|---|---|---|---|---|---|---|
| B01_common_frame | PETG | 6006.0 | 7.63 | [5.14, 27.3, 3.81] | 是 | 1.71 | 66.7 |
| B02_mb_cradle | PETG | 3920.3 | 4.98 | [-39.75, 21.94, 3] | 是 | 0.75 | 44.5 |
| B03_camera_cradle | PETG | 5069.9 | 6.44 | [0, 16.73, 7.01] | 是 | 2.4 | 229.6 |
| B04_imu_cradle | PETG | 4467.3 | 5.67 | [32.5, 20.11, 0.93] | 是 | 2.4 | 231.8 |
| B05_camera_cap | PETG | 862.3 | 1.10 | [-0, 9.29, 4.94] | 是 | 1.6 | 8.5 |
| B06_imu_keeper | PETG | 228.8 | 0.29 | [26.8, 10.1, 1] | 是 | 1.8 | 0.0 |

## 强度（FEA）

| 件 | 工况 | 最大等效应力 MPa | σ_zz MPa | 安全系数 | 最大位移 mm | 四面体 / 自由度 |
|---|---|---|---|---|---|---|
| B01_common_frame | 整组前部惯性载荷（50g） | 未计算（NameError: name 'fea' is not defined） | — | — | — | — |
| B02_mb_cradle | 主板 50g 冲击（向下） | 未计算（NameError: name 'fea' is not defined） | — | — | — | — |
| B03_camera_cradle | 相机模组 50g 冲击（+Y 向头） | 未计算（NameError: name 'fea' is not defined） | — | — | — | — |
| B04_imu_cradle | IMU + 杜邦头 50g 冲击（+Y 向头） | 未计算（NameError: name 'fea' is not defined） | — | — | — | — |
| B05_camera_cap | 正面按压/撞击 5N（两螺丝固定） | 未计算（NameError: name 'fea' is not defined） | — | — | — | — |
| B06_imu_keeper | IMU 反向顶 1.5N（-Y） | 未计算（NameError: name 'fea' is not defined） | — | — | — | — |

> 求解器：?；。等效各向同性，层间按 σ_zz 对照 Z 向许用值单独判定。

## 整机

- 总质量（打印件）：**26.10 g**，质心 [1.28, 21.16, 3.84]
- 绕支点 [0, 95, -10] 的重力矩：**18.9 N·mm**，力臂 75.1 mm，前部质量占比 0%
- 刚体静力学；实际佩戴压力分布需要人体模型或实测，不能由本项代替舒适性验收

| 件 | 质量 g | 占比 |
|---|---|---|
| B01_common_frame | 7.63 | 29.2% |
| B02_mb_cradle | 4.98 | 19.1% |
| B03_camera_cradle | 6.44 | 24.7% |
| B04_imu_cradle | 5.67 | 21.7% |
| B05_camera_cap | 1.09 | 4.2% |
| B06_imu_keeper | 0.29 | 1.1% |

## 全部检查

| 状态 | 编号 | 分类 | 结果 | 限值 | 方法 |
|---|---|---|---|---|---|
| 不通过 | B01_common_frame.geometry.winding | geometry | 0.07835 | 0.999 | 有符号体积/绝对体积和 |
| 不通过 | B02_mb_cradle.geometry.winding | geometry | 0.1067 | 0.999 | 有符号体积/绝对体积和 |
| 不通过 | B02_mb_cradle.print.min_wall | printability | 0.75 mm | 1.2 mm | 表面采样点沿内法线射线求局部厚度（p05） |
| 不通过 | B03_camera_cradle.geometry.winding | geometry | 0.2648 | 0.999 | 有符号体积/绝对体积和 |
| 不通过 | B04_imu_cradle.geometry.winding | geometry | 0.1468 | 0.999 | 有符号体积/绝对体积和 |
| 不通过 | B05_camera_cap.geometry.winding | geometry | 0.2414 | 0.999 | 有符号体积/绝对体积和 |
| 不通过 | B06_imu_keeper.geometry.winding | geometry | 0.1093 | 0.999 | 有符号体积/绝对体积和 |
| 不通过 | assembly.clearance.B03_camera_cradle_B05_camera_cap | interference | -2.228 mm | 0.15 mm | 双向表面采样 + 有符号距离 |
| 不通过 | assembly.insertion.B05_camera_cap | motion | 11.85 | — | 沿装配方向逐步推进 + 有符号距离 |
| 不通过 | joint.IMU 限位条 M2×16 ×2（进钢螺母） | fastener | 0.63 安全系数 | 2 安全系数 | 预紧力/承压/螺纹拉脱/螺丝拉伸 |
| 不通过 | joint.主板 M2×12 ×4 | fastener | 0.63 安全系数 | 2 安全系数 | 预紧力/承压/螺纹拉脱/螺丝拉伸 |
| 不通过 | joint.共架-旧壳 M2 ×6 | fastener | 0.48 安全系数 | 2 安全系数 | 预紧力/承压/螺纹拉脱/螺丝拉伸 |
| 不通过 | joint.相机压盖 M2×8 ×2（进钢螺母） | fastener | 0.63 安全系数 | 2 安全系数 | 预紧力/承压/螺纹拉脱/螺丝拉伸 |
| 不通过 | tolerance.M2 螺丝穿过主板 Ø2.2 孔（孔距 32.6 已确认） | tolerance | [-0.25, 0.25] | [-0.15, 0.15] | 最坏情况 + RSS |
| 未验证 | B01_common_frame.strength.整组前部惯性载荷（50g） | strength | — | — |  |
| 未验证 | B02_mb_cradle.strength.主板 50g 冲击（向下） | strength | — | — |  |
| 未验证 | B03_camera_cradle.strength.相机模组 50g 冲击（+Y 向头） | strength | — | — |  |
| 未验证 | B04_imu_cradle.strength.IMU + 杜邦头 50g 冲击（+Y 向头） | strength | — | — |  |
| 未验证 | B05_camera_cap.strength.正面按压/撞击 5N（两螺丝固定） | strength | — | — |  |
| 未验证 | B06_imu_keeper.strength.IMU 反向顶 1.5N（-Y） | strength | — | — |  |
| 通过 | B01_common_frame.geometry.volume | geometry | 6006 mm3 | — | 散度定理精确积分 |
| 通过 | B01_common_frame.geometry.watertight | geometry | [0, 0, 0] | [0, 0, 0] | STL 边拓扑计数（焊接后） |
| 通过 | B01_common_frame.print.min_wall | printability | 1.706 mm | 1.2 mm | 表面采样点沿内法线射线求局部厚度（p05） |
| 通过 | B01_common_frame.print.overhang | printability | 0.0084 面积占比 | 0.35 面积占比 | 朝向 build 方向的下表面 + 向下射线判自支撑 |
| 通过 | B02_mb_cradle.geometry.volume | geometry | 3920 mm3 | — | 散度定理精确积分 |
| 通过 | B02_mb_cradle.geometry.watertight | geometry | [0, 0, 0] | [0, 0, 0] | STL 边拓扑计数（焊接后） |
| 通过 | B02_mb_cradle.print.overhang | printability | 0.0101 面积占比 | 0.35 面积占比 | 朝向 build 方向的下表面 + 向下射线判自支撑 |
| 通过 | B03_camera_cradle.geometry.volume | geometry | 5070 mm3 | — | 散度定理精确积分 |
| 通过 | B03_camera_cradle.geometry.watertight | geometry | [0, 0, 0] | [0, 0, 0] | STL 边拓扑计数（焊接后） |
| 通过 | B03_camera_cradle.print.min_wall | printability | 2.4 mm | 1.2 mm | 表面采样点沿内法线射线求局部厚度（p05） |
| 通过 | B03_camera_cradle.print.overhang | printability | 0.0565 面积占比 | 0.35 面积占比 | 朝向 build 方向的下表面 + 向下射线判自支撑 |
| 通过 | B03_camera_cradle.strength.beam | strength | 0.07 MPa | 31.99 MPa | 逐层截面 I + 悬臂近似（独立口径） |
| 通过 | B04_imu_cradle.geometry.volume | geometry | 4467 mm3 | — | 散度定理精确积分 |
| 通过 | B04_imu_cradle.geometry.watertight | geometry | [0, 0, 0] | [0, 0, 0] | STL 边拓扑计数（焊接后） |
| 通过 | B04_imu_cradle.print.min_wall | printability | 2.4 mm | 1.2 mm | 表面采样点沿内法线射线求局部厚度（p05） |
| 通过 | B04_imu_cradle.print.overhang | printability | 0.0518 面积占比 | 0.35 面积占比 | 朝向 build 方向的下表面 + 向下射线判自支撑 |
| 通过 | B05_camera_cap.geometry.volume | geometry | 862.3 mm3 | — | 散度定理精确积分 |
| 通过 | B05_camera_cap.geometry.watertight | geometry | [0, 0, 0] | [0, 0, 0] | STL 边拓扑计数（焊接后） |
| 通过 | B05_camera_cap.print.min_wall | printability | 1.6 mm | 1 mm | 表面采样点沿内法线射线求局部厚度（p05） |
| 通过 | B05_camera_cap.print.overhang | printability | 0.0068 面积占比 | 0.35 面积占比 | 朝向 build 方向的下表面 + 向下射线判自支撑 |
| 通过 | B06_imu_keeper.geometry.volume | geometry | 228.8 mm3 | — | 散度定理精确积分 |
| 通过 | B06_imu_keeper.geometry.watertight | geometry | [0, 0, 0] | [0, 0, 0] | STL 边拓扑计数（焊接后） |
| 通过 | B06_imu_keeper.print.min_wall | printability | 1.8 mm | 1 mm | 表面采样点沿内法线射线求局部厚度（p05） |
| 通过 | B06_imu_keeper.print.overhang | printability | 0 面积占比 | 0.35 面积占比 | 朝向 build 方向的下表面 + 向下射线判自支撑 |
| 通过 | assembly.clearance.B02_mb_cradle_B01_common_frame | interference | 0 mm | 0.15 mm | 双向表面采样 + 有符号距离 |
| 通过 | assembly.clearance.B03_camera_cradle_B01_common_frame | interference | 0 mm | 0.15 mm | 双向表面采样 + 有符号距离 |
| 通过 | assembly.clearance.B04_imu_cradle_B06_imu_keeper | interference | 0 mm | 0.15 mm | 双向表面采样 + 有符号距离 |
| 通过 | assembly.insertion.B06_imu_keeper | motion | — | — | 沿装配方向逐步推进 + 有符号距离 |
| 通过 | assembly.print_mass_vs_slicer | printability | 26.3 g | 24.34 g | 壳层+填充模型求和 vs 切片器预测 |
| 参考 | B01_common_frame.mass | mass | 7.628 g | — | 体积 × PETG 密度 1.27 g/cm³ |
| 参考 | B01_common_frame.print.mass_estimate | printability | 7.655 g | — | 壳层(1.80mm)+20% 填充模型 |
| 参考 | B02_mb_cradle.mass | mass | 4.979 g | — | 体积 × PETG 密度 1.27 g/cm³ |
| 参考 | B02_mb_cradle.print.mass_estimate | printability | 4.997 g | — | 壳层(1.80mm)+20% 填充模型 |
| 参考 | B03_camera_cradle.mass | mass | 6.439 g | — | 体积 × PETG 密度 1.27 g/cm³ |
| 参考 | B03_camera_cradle.print.mass_estimate | printability | 6.313 g | — | 壳层(1.80mm)+20% 填充模型 |
| 参考 | B04_imu_cradle.mass | mass | 5.674 g | — | 体积 × PETG 密度 1.27 g/cm³ |
| 参考 | B04_imu_cradle.print.mass_estimate | printability | 5.945 g | — | 壳层(1.80mm)+20% 填充模型 |
| 参考 | B05_camera_cap.mass | mass | 1.095 g | — | 体积 × PETG 密度 1.27 g/cm³ |
| 参考 | B05_camera_cap.print.mass_estimate | printability | 1.098 g | — | 壳层(1.80mm)+20% 填充模型 |
| 参考 | B06_imu_keeper.mass | mass | 0.2906 g | — | 体积 × PETG 密度 1.27 g/cm³ |
| 参考 | B06_imu_keeper.print.mass_estimate | printability | 0.291 g | — | 壳层(1.80mm)+20% 填充模型 |
| 参考 | assembly.wear.moment | balance | 18.9 N·mm | — |  |

## 方法边界（不属于“已通过”）

- FEA 为体素六面体线弹性，单元中心取应力，尖角/薄壁根部会低估峰值
- FDM 材料参数是文献典型值（calibration=assumed），层间强度必须用试片标定后才能定论
- 射线壁厚在相切/尖角处可能读到邻壁，需结合截面图
- 支撑体积按 15% 密度估算，不等于切片器结果
- 运动/干涉基于表面采样，采样点之外的小于采样间距的局部接触可能漏判
- 不包含：真实接触压力分布、蠕变/长期载荷、热变形、装配预紧导致的变形
