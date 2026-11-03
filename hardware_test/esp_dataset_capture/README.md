# MJPEG 采集工具

安装根目录 requirements 后运行 `python hardware_test/esp_dataset_capture/app.py`，填写可访问的 ESP 图流地址。原始采集输出位于被忽略的 `datasets/`；不自动提交。

采集时图像预览和保存共用一路流，避免额外连接争抢；详见代码中的 recorder / live_stream。
