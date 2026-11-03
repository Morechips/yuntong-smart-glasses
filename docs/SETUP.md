# 安装与部署

## 电脑环境

当前本机已验证的运行环境为 Python 3.10.21、NumPy 1.26.4、OpenCV 4.11.0.86、ONNX Runtime 1.23.2、Paramiko 5.1.0、Pillow 12.3.0。版本已写入根 `requirements.txt`；该环境记录不表示所有系统组合都验证过。

```powershell
py -3.10 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
.\.venv\Scripts\python.exe -m tkinter
.\.venv\Scripts\python.exe scripts/verify_repository.py
.\.venv\Scripts\python.exe scripts/offline_demo.py
```

Tkinter 来自 Python 安装本身。Linux 需另行安装发行版的 Tk 图形支持；正式桌面启动路径主要在 Windows 验证。只运行离线入口无需 Tk 窗口、GPU 或 ESP-IDF。

## 设备配置

复制 `config/device.json.example` 为 `config/device.local.json`。该本地文件已被忽略。

| 字段 | 含义 |
|---|---|
| `x5_host` / `x5_user` | X5 可访问的 IP 与 SSH 用户 |
| `x5_host_key` | 从可信 X5 控制台读取的 SSH 公钥 SHA256 指纹 |
| `esp_mac` | 实际 ESP 的 MAC；用于 RDK 热点下发现唯一地址 |
| `esp_url` | ESP 直连图像地址，例如 `http://192.168.137.2`；不得含口令 |
| `esp_via_x5` | `true` 经现有 RDK WLAN 发现并转发，`false` 使用 `esp_url` |
| `backend` | `x5` 或 `pc`；通过 X5 转发时必须用 `x5` |

在可信 X5 本地终端读取指纹：

```bash
ssh-keygen -lf /etc/ssh/ssh_host_ed25519_key.pub -E sha256
```

若 SSH 协商选择其他主机密钥类型，应核对该类型的 `.pub` 文件。提交版沿用固定公钥校验，不自动信任网络返回的新密钥。SSH 密码运行时从窗口输入，仅存内存；不要写进配置、命令行或仓库。

双击根 `启动演示.cmd`。启动脚本优先使用本仓库 `.venv`，否则使用 PATH 的 `python`。它不会开关热点、改 IP 或安装设备依赖。电脑直连路径可选择 `pc`；X5 模式要求现有设备环境正常。

## RDK X5

需要设备已有的 `hobot_dnn.pyeasy_dnn`、Python 3、NumPy、OpenCV 和可用 SSH / SFTP。`hobot_dnn` 不属于电脑的 pip requirements；本包不包含 X5 系统镜像和厂商运行库。

运行时将少量代码和地面 BIN 放进新建 `/dev/shm/yuntong_floor_demo_*` 目录，回读校验哈希后启动工作进程，正常退出时尝试清理。不会把模型安装为开机服务。原设备曾出现存储 I/O 故障且根因未定；先确认系统可正常读取文件，再做负载测试。

首次运行会用已收录的 `A01_01`、`A02_02` 开发 JPEG 和量化模拟结果核对类别像素。失败则结束本轮，不自动换 PC 后端。

## ESP32-S3 固件

工程在 `firmware/esp32s3/`，对应当前可调 PCM 音量、开机提前关闭马达、关闭自动循环、保留 IMU 采样的源码。只发布源码，不复制可能带 Wi-Fi 配置的已编译固件。

原构建使用 ESP-IDF **6.0.1**，目标 `esp32s3`；直接依赖版本和传递依赖锁保留在 `main/idf_component.yml` 与 `dependencies.lock`。依赖由 ESP-IDF Component Manager 下载，`managed_components/` 不随包复制。

在已配置好的 ESP-IDF 终端执行：

```powershell
cd firmware/esp32s3
idf.py set-target esp32s3
idf.py menuconfig
# OV3660 camera benchmark 中填写本地 2.4GHz Wi-Fi SSID 和密码。
idf.py build
```

本地 `sdkconfig`、构建目录和固件产物均已忽略。`sdkconfig.defaults` 已合并外设 / USB 控制台 / IMU 配置，Wi-Fi 密码默认留空。脱敏后未重新编译或烧录，不承诺重建字节与原固件一致。

刷写不是运行电脑演示的必要步骤。已有兼容固件时直接使用。需要刷写新板时，先核对实际板型、供电、分区和串口，再按 `idf.py` 构建输出的地址操作；不要把本包模型 BIN 当成 ESP 应用，也不要把其他板上的应用偏移当成通用地址。

## 训练与量化源码

`round02_supervision.py` 包含 TinyTaskProbe、固定六图训练 / 两图开发探针、粗标处理和 ONNX 导出。可选依赖在 `requirements-training.txt`。原代码要求 CUDA，输出目录已随包收录，复训应在单独副本中调整 `OUT`，不能直接覆盖冻结模型。

`compile_demo.py` 保留原 24 图校准和 bayes-e 转换流程，依赖原 WSL / Horizon 工具链；其 `/root/blindpath_compile/.venv` 等路径是历史实验环境，换机需自行调整。本包默认使用已收录 ONNX / BIN；量化和训练脚本不纳入“一键启动”承诺。

盲道候选代码另见 `docs/STATUS.md`；未随包复制外部 GuideTWSI 权重或示例照片。
