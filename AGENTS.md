# 云瞳眼镜工程协作

- 当前功能和证据口径以 [设备实现状态](docs/STATUS.md)、[安卓对接](docs/COMPANION.md) 和对应运行记录为准。保留既有记录，分别注明自动测试、电脑模拟、RDK 台架、RDK 与 ESP32-S3 实机、完整佩戴链路。
- Android 维护者负责代码、自动检查、中文测试及反馈工具。Morechips 接线、处理设备授权、观察实机并提交脱敏结果。Android 轮次和反馈入口由 [安卓交接](https://github.com/Morechips/yuntong-mobile/blob/main/docs/handoff.md) 维护。
- 设备改动经 PR 提交。协议影响需对照两仓文档，验证控制和媒体入口与回执。修改后依 [提交说明](docs/GITHUB.md) 更新相关清单哈希并运行 `python scripts/verify_repository.py`。测试结果保留对应源提交、板端构建和固件证据。
- 本地 Wi-Fi 配置、令牌、签名材料和原始现场日志留在设备或维护电脑。日常联调保留已有固件和运行任务，服务操作、刷机与声振验收各自明确范围。
