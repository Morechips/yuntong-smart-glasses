# 硬件与接口索引

## 当前主要模块

| 模块 | 用途 | 说明 |
|---|---|---|
| ESP32-S3 摄像头主板 + OV3660 | 采集与无线图流 | 本工程板级引脚来自 `glasses_board.h`，不能直接用于任意 ESP32-S3 开发板 |
| RDK X5 | 地面模型 BPU 推理 | 当前通过电脑与 SSH 调用 |
| JY61P | IMU 采样 | 当前源码走共享 I²C 总线；主入口未完成视觉姿态融合 |
| 双振动模块 | 短振方向提示 | 软件 left / right 与佩戴左右需实际确认 |
| 2209 扬声器及板载音频链 | 播放中文提示 | PCM 接口、音量能力由 ESP 状态报告 |
| P23C、电池、原接头线束 | 计算端供电与结构容纳 | 本包不改变现有接法，不对总电流、热与续航作新验收 |

## 源码对应引脚

以下为当前源码定义，供核对既有接线；不构成其他板型的通用接线图。

| 信号 | 当前定义 |
|---|---|
| I²C SCL / SDA | GPIO7 / GPIO8 |
| I²S MCLK / BCLK / WS | GPIO10 / GPIO11 / GPIO12 |
| I²S DIN / DOUT | GPIO13 / GPIO14 |
| 马达 left / right | GPIO44 / GPIO43；模块控制信号，不是由 GPIO 直接供电 |
| 相机 XCLK / PCLK | GPIO38 / GPIO41 |
| 相机 VSYNC / HREF | GPIO17 / GPIO18 |
| 相机 D0—D7 | GPIO45 / 47 / 48 / 46 / 42 / 40 / 39 / 21 |

原主板串口座上 RX / TX 对应的 GPIO 被用作马达控制，固件采用 USB Serial/JTAG 控制台。源码中通过 CH32 扩展器 EXIO5 请求保持 BAT_EN；该软件状态不等于实测电池电压。

## 当前演示使用的 HTTP 接口

| 地址 | 用途 |
|---|---|
| `GET http://ESP/stream` | 单路 MJPEG 图流；采集和显示尽量复用同一连接 |
| `GET http://ESP/capture` | 单张 JPEG |
| `GET http://ESP/stats` | 图传统计 |
| `GET http://ESP:8081/state` | 外设 / 音量 / 马达循环能力与状态 |
| `POST http://ESP:8081/motor?channel=left或right或both&ms=20..500` | 有界短脉冲，完成后返回关闭状态 |
| `POST http://ESP:8081/pcm?volume=0..70` | 请求体为 24kHz、16bit little-endian、单声道 PCM，不带 WAV 头 |

接口的严格参数及响应字段以 `firmware/esp32s3/main/glasses_peripheral_bench.c` 为准。主入口会先查询能力再发送，不使用旧固件静默忽略音量参数的行为作为成功依据。

本包没有对外开放这些设备服务或配置云端控制。实机使用步骤见 [使用说明](USAGE.md)。
