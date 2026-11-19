# 手机与眼镜接口

安卓由 [yuntong-mobile](https://github.com/Morechips/yuntong-mobile) 维护。本仓库保存 RDK X5 的服务端和 ESP32-S3 固件。手机连 RDK，RDK 再读取或控制 ESP32-S3。

## RDK X5 手机对接

| 接口 | 数据 |
| --- | --- |
| `/rdk` | 状态、静默观察启停、静置准备、正前方确认、导航信息与回执 |
| `/rdk/media` | JPEG 预览、按请求获取新图、无新图回执 |

两条 WebSocket 均使用 `yuntong-rdk-link-v1`，默认端口 8767，连接时用 `Authorization: Bearer <token>` 鉴权。本目录的服务代码与 rc7 板端接口包一致。对接字段说明见[安卓客户端协议](https://github.com/Morechips/yuntong-mobile/blob/main/docs/device-interface.md)。

RDK 须事先安装配套的佩戴运行包，提供 `democtl.py`、`runtime.py` 和运行状态。以 RDK 的普通用户进入本仓库根目录，安装依赖并生成本机配对令牌。

```bash
python3 -m venv --system-site-packages .venv
.venv/bin/python -m pip install -r mobile/v1/rdk/requirements.txt
umask 077
mkdir -p "$HOME/.config/yuntong"
token_file="$HOME/.config/yuntong/pairing.token"
test -f "$token_file" || .venv/bin/python -c 'import secrets; print(secrets.token_urlsafe(24))' > "$token_file"
.venv/bin/python -B mobile/v1/rdk/server.py \
  --runtime "$HOME/yuntong-wearable/current" \
  --token-file "$token_file" \
  --host 127.0.0.1
```

手机直接连接时，把 `--host` 改为手机可达的 RDK 局域网 IPv4，App 连接 `ws://<RDK局域网IP>:8767/rdk`。服务启动后先验证状态，观察任务由手机明确发起。终端停止服务前，先停止本轮观察。预览读取观察任务最新的 JPEG，主动取图等待请求后的新图。跌倒事件当前由受控事件文件验证。

### USB 台架连接与排障

在 RDK 的 SSH 终端运行 `ip -br -4 addr`，识别**电脑网线所在接口的 RDK IPv4**。SSH 可达只说明 SSH 服务能连通。用 `ss -ltn 'sport = :8767'` 检查手机接口服务是否已监听；若仅在 `127.0.0.1:8767`，另一台电脑不能连到它。由原启动者结束本轮观察并停止原服务，按上面的启动命令，把 `--host` 改为电脑实际可达的板端 IPv4，看到 `Yuntong RDK listening` 后保持终端打开。

Windows 工具填该板端地址。手机填「使用 USB 转发」后显示的手机端回环地址；重建转发后重新读取该地址。两条路径使用同一份 RDK 配对密钥。密钥位于该次启动的 `--token-file` 指定文件，默认示例为 `~/.config/yuntong/pairing.token`。从可信的 RDK 终端本地查看并只复制到手机配对框或电脑工具的掩码框。诊断中只记录连接阶段和结果。

低帧率应分别观察板端图像更新与手机版预览帧率。当前板端预览约每 250ms 检查一次 JPEG。主动取图以板端收请求的时间为界，等待约 2 秒，读取时校验同一文件版本；没有符合条件的新图便返回 `media.unavailable`。JPEG 写入时间用于台架新图判定，实际相机采集时延仍需用物体变化验证。

运行现有协议测试。

```bash
cd mobile/v1/rdk
../../../.venv/bin/python -B -m unittest -v test_server
```

### 安卓 R01 台架准备

先用现有 8082 固件和佩戴运行包。维护者使用上面的方式确认手机接口服务运行，并给 Morechips 一条电脑或手机可达的 RDK 地址，配对密钥通过私下渠道提供。若接口仍只监听回环，Windows 工具无法从另一台电脑连接；按上文选择可达的 RDK 局域网监听地址。保留原有服务和系统设置。

Morechips 在 Windows 使用 [安卓一键联调入口](https://github.com/Morechips/yuntong-mobile/tree/main/field-test)，从 App 发起、读取和停止本轮静默观察。通过实时变化的纸卡和请求后更换的纸卡分别核对预览和新图。若状态已有其他入口的观察任务，回原入口确认和处理。当前协议测试已覆盖鉴权、图传和请求后的新帧；真实 ESP 画面与源帧连续性由本轮台架反馈补证。R01 不需要刷写 8084、验证声振或触发跌倒事件。

记录实装的 RDK 包标识、ESP 固件指纹（可查时）和是否确见新图。服务握手只确认协议协商；板端包或指纹未知时在报告中明确。结果通过 [安卓反馈入口](https://github.com/Morechips/yuntong-mobile/issues) 交回，设备端独立验证仍按 [状态记录](STATUS.md) 的口径归档。

## ESP32-S3 固件

| 源码版本 | 接口与状态 |
| --- | --- |
| 主固件 8082 基线 | 8082 的 `/state`、`/imu` 和 `/imu/batch` 独立读取同一份 IMU 缓存。8081 继续处理 PCM 和短振。对应已装固件 ELF 指纹 `78c17a9855`。 |
| 8084 开发候选 | [候选补丁](../firmware/esp32s3/patches/0001-independent-motor-port.patch)增加 `8084/motor`，与 8081 马达入口共用互斥，短振结束后归零。候选原型已编译，历史编译指纹为 `09df141468a7`，实机并行待验。 |

日常重建按[固件部署说明](SETUP.md)。需要验证 8084 时，在独立检出的副本根目录应用候选补丁，再配置本机 Wi-Fi 并用 ESP-IDF 6.0.1 构建。补丁同时将 socket 上限从 16 调至 20。8084 在台架确认声振重叠、供电和图传后再决定是否纳入主固件。

```bash
git apply --check firmware/esp32s3/patches/0001-independent-motor-port.patch
git apply firmware/esp32s3/patches/0001-independent-motor-port.patch
```
