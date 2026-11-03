# 第三方组件与材料来源

本文件记录本包实际引用的来源；不把本工程整理表述为全部内容的原创声明，也不替代上游许可证。

| 内容 | 来源与处理 |
|---|---|
| ESP-IDF 与组件 | 原工程依赖清单 / 锁文件保留；托管组件交由 Component Manager 获取，第三方源码未整树复制 |
| ESP 板级适配 | `glasses_board` 基于现有微雪板卡接口，工程中保留原注释；板型和接口以源码为准 |
| PC Python 运行库 | NumPy、OpenCV、ONNX Runtime、Paramiko、Pillow；依赖由用户安装，上游授权独立适用 |
| 地面模型 | 项目 TinyTaskProbe 从头训练；随包仅有已使用的开发图、粗监督、模型和一致性产物，不包含独立保留场景 |
| 提示音 | 原工程 Windows 系统语音离线合成的七句中文 WAV，格式 24kHz / LE16 / mono；不主张为真人录音或独立语音模型 |
| CadQuery / OpenCascade / Trimesh | 用于参数化建模和数字检查，不将其运行库一起复制 |
| B3 X5 几何参考 | 地瓜机器人官方 RDK X5 STEP 派生的参考 BREP / 装配参考，保持来源标记；不是本项目设计的 X5 板卡 |
| 盲道候选 | 复用原 GuideTWSI / YOLO 分割适配与运行工具源码，原来源许可证保留于 `third_party/`；外部权重 / 图片未随包附带 |

候选 `runtime_utils/preprocess.py` 和 `postprocess.py` 保留 D-Robotics 原版权及 Apache-2.0 文件头，对应许可证全文在 `third_party/RDK_MODEL_ZOO_LICENSE.txt`。GuideTWSI 的原 MIT 文本另列于 `third_party/GuideTWSI_LICENSE_MIT.txt`，不据此推断全部模型训练链的许可。

官方 X5 几何原始入口：[RDK X5 STEP](https://archive.d-robotics.cc/downloads/hardware/rdk_x5/RDK_X5_LPDDR4_4266MHz_V1P0_pcb.stp)。建模子目录原 README 中也保留了来源。供应商资料未自动成为本项目自有授权范围。

盲道外部模型与训练链的授权需要按实际取得版本核对，不能仅根据一个仓库许可证推断全部权重的许可。本提交包不附这些外部权重，也不自动联网下载。被保留的候选适配源码不表示盲道功能已经验收。
