#pragma once
#include "sdkconfig.h"
#if CONFIG_GLASSES_IMU_SYNC_ENABLE
#include "esp_err.h"
#include "esp_http_server.h"
#include <stddef.h>
void glasses_sync_identity_init(void);
const char *glasses_sync_boot_id(void);
esp_err_t glasses_imu_stream_start(void);
esp_err_t glasses_imu_batch_handler(httpd_req_t *req);
esp_err_t glasses_imu_cached_handler(httpd_req_t *req);
int glasses_imu_state_json(char *out, size_t capacity);
#endif
