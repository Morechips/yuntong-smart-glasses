#include <inttypes.h>
#include <stdio.h>
#include <string.h>

#include "esp_camera.h"
#include "esp_check.h"
#include "esp_event.h"
#include "esp_http_server.h"
#include "esp_log.h"
#include "esp_netif.h"
#include "esp_system.h"
#include "esp_timer.h"
#include "esp_wifi.h"
#include "freertos/FreeRTOS.h"
#include "freertos/event_groups.h"
#include "freertos/task.h"
#include "glasses_board.h"
#include "glasses_transport.h"
#include "glasses_peripheral_bench.h"
#include "glasses_imu_stream.h"
#include "glasses_sync_core.h"
#include "nvs_flash.h"
#include "sdkconfig.h"
#include "soc/soc_caps.h"

#define WIFI_CONNECTED_BIT BIT0
#define SENSOR_BENCH_FRAMES 60

static const char *TAG = "camera_bench";
static EventGroupHandle_t s_wifi_event_group;
static int s_wifi_retries;
static httpd_handle_t s_httpd;

static portMUX_TYPE s_stats_lock = portMUX_INITIALIZER_UNLOCKED;
static uint64_t s_stream_frames;
static uint64_t s_stream_bytes;
static uint64_t s_stream_errors;
static int64_t s_stream_started_us;
static int64_t s_last_frame_us;

static const char *STREAM_TYPE = "multipart/x-mixed-replace;boundary=frame";
static const char *STREAM_BOUNDARY = "\r\n--frame\r\n";

static const char INDEX_HTML[] =
    "<!doctype html><html><head><meta name='viewport' content='width=device-width,initial-scale=1'>"
    "<title>OV3660 benchmark</title><style>body{font-family:system-ui;background:#111;color:#eee;margin:16px}"
    "img{display:block;max-width:100%;height:auto;background:#222}pre{white-space:pre-wrap}</style></head><body>"
    "<h2>OV3660 native-JPEG benchmark</h2><img src='/stream'><pre id='s'>loading stats...</pre>"
    "<script>setInterval(async()=>{try{let r=await fetch('/stats',{cache:'no-store'});"
    "document.getElementById('s').textContent=JSON.stringify(await r.json(),null,2)}catch(e){}},1000)</script>"
    "</body></html>";

static framesize_t configured_frame_size(void)
{
#if CONFIG_CAM_FRAME_SIZE_QVGA
    return FRAMESIZE_QVGA;
#elif CONFIG_CAM_FRAME_SIZE_SVGA
    return FRAMESIZE_SVGA;
#else
    return FRAMESIZE_VGA;
#endif
}

static const char *configured_frame_size_name(void)
{
#if CONFIG_CAM_FRAME_SIZE_QVGA
    return "QVGA 320x240";
#elif CONFIG_CAM_FRAME_SIZE_SVGA
    return "SVGA 800x600";
#else
    return "VGA 640x480";
#endif
}

static void wifi_event_handler(void *arg, esp_event_base_t event_base,
                               int32_t event_id, void *event_data)
{
    if (event_base == WIFI_EVENT && event_id == WIFI_EVENT_STA_START) {
        esp_wifi_connect();
    } else if (event_base == WIFI_EVENT && event_id == WIFI_EVENT_STA_DISCONNECTED) {
        const wifi_event_sta_disconnected_t *disconnect =
            (const wifi_event_sta_disconnected_t *)event_data;
        xEventGroupClearBits(s_wifi_event_group, WIFI_CONNECTED_BIT);
        s_wifi_retries++;
        ESP_LOGW(TAG, "Wi-Fi disconnected (reason=%d); reconnect attempt %d",
                 disconnect ? disconnect->reason : -1, s_wifi_retries);
        esp_wifi_connect();
    } else if (event_base == IP_EVENT && event_id == IP_EVENT_STA_GOT_IP) {
        const ip_event_got_ip_t *event = (const ip_event_got_ip_t *)event_data;
        ESP_LOGI(TAG, "Wi-Fi connected: " IPSTR ", open http://" IPSTR "/",
                 IP2STR(&event->ip_info.ip), IP2STR(&event->ip_info.ip));
        s_wifi_retries = 0;
        xEventGroupSetBits(s_wifi_event_group, WIFI_CONNECTED_BIT);
    }
}

static esp_err_t wifi_init_sta(void)
{
    s_wifi_event_group = xEventGroupCreate();
    ESP_RETURN_ON_FALSE(s_wifi_event_group, ESP_ERR_NO_MEM, TAG, "event group allocation failed");

    ESP_RETURN_ON_ERROR(esp_netif_init(), TAG, "esp_netif_init failed");
    ESP_RETURN_ON_ERROR(esp_event_loop_create_default(), TAG, "event loop creation failed");
    ESP_RETURN_ON_FALSE(esp_netif_create_default_wifi_sta(), ESP_FAIL, TAG, "STA netif failed");

    wifi_init_config_t init_cfg = WIFI_INIT_CONFIG_DEFAULT();
    ESP_RETURN_ON_ERROR(esp_wifi_init(&init_cfg), TAG, "esp_wifi_init failed");

    esp_event_handler_instance_t wifi_instance;
    esp_event_handler_instance_t ip_instance;
    ESP_RETURN_ON_ERROR(esp_event_handler_instance_register(WIFI_EVENT, ESP_EVENT_ANY_ID,
                        &wifi_event_handler, NULL, &wifi_instance), TAG, "Wi-Fi handler failed");
    ESP_RETURN_ON_ERROR(esp_event_handler_instance_register(IP_EVENT, IP_EVENT_STA_GOT_IP,
                        &wifi_event_handler, NULL, &ip_instance), TAG, "IP handler failed");

    wifi_config_t wifi_config = {0};
    strlcpy((char *)wifi_config.sta.ssid, CONFIG_CAM_WIFI_SSID, sizeof(wifi_config.sta.ssid));
    strlcpy((char *)wifi_config.sta.password, CONFIG_CAM_WIFI_PASSWORD, sizeof(wifi_config.sta.password));
    wifi_config.sta.threshold.authmode = WIFI_AUTH_WPA2_PSK;
    wifi_config.sta.sae_pwe_h2e = WPA3_SAE_PWE_BOTH;
#ifdef CONFIG_ESP_WIFI_WPA3_COMPATIBLE_SUPPORT
    wifi_config.sta.disable_wpa3_compatible_mode = 0;
#endif
    wifi_config.sta.pmf_cfg.capable = true;
    wifi_config.sta.pmf_cfg.required = false;

    ESP_RETURN_ON_ERROR(esp_wifi_set_mode(WIFI_MODE_STA), TAG, "set Wi-Fi mode failed");
    ESP_RETURN_ON_ERROR(esp_wifi_set_config(WIFI_IF_STA, &wifi_config), TAG, "set Wi-Fi config failed");
    ESP_RETURN_ON_ERROR(esp_wifi_start(), TAG, "start Wi-Fi failed");
    ESP_RETURN_ON_ERROR(esp_wifi_set_ps(WIFI_PS_NONE), TAG, "disable Wi-Fi power save failed");

    ESP_LOGI(TAG, "Connecting to 2.4 GHz SSID: %s", CONFIG_CAM_WIFI_SSID);
    xEventGroupWaitBits(s_wifi_event_group, WIFI_CONNECTED_BIT,
                        pdFALSE, pdFALSE, portMAX_DELAY);
    return ESP_OK;
}

static esp_err_t camera_init(void)
{
    camera_config_t config = {
        .pin_pwdn = GPIO_NUM_NC,
        .pin_reset = GPIO_NUM_NC,
        .pin_xclk = GLASSES_CAMERA_XCLK,
        .pin_sccb_sda = GPIO_NUM_NC,
        .pin_sccb_scl = GPIO_NUM_NC,
        .pin_d7 = GLASSES_CAMERA_D7,
        .pin_d6 = GLASSES_CAMERA_D6,
        .pin_d5 = GLASSES_CAMERA_D5,
        .pin_d4 = GLASSES_CAMERA_D4,
        .pin_d3 = GLASSES_CAMERA_D3,
        .pin_d2 = GLASSES_CAMERA_D2,
        .pin_d1 = GLASSES_CAMERA_D1,
        .pin_d0 = GLASSES_CAMERA_D0,
        .pin_vsync = GLASSES_CAMERA_VSYNC,
        .pin_href = GLASSES_CAMERA_HREF,
        .pin_pclk = GLASSES_CAMERA_PCLK,
        .xclk_freq_hz = 20000000,
        .ledc_timer = LEDC_TIMER_0,
        .ledc_channel = LEDC_CHANNEL_0,
        .pixel_format = PIXFORMAT_JPEG,
        .frame_size = configured_frame_size(),
        .jpeg_quality = CONFIG_CAM_JPEG_QUALITY,
        .fb_count = CONFIG_CAM_FB_COUNT,
        .fb_location = CAMERA_FB_IN_PSRAM,
        .grab_mode = CAMERA_GRAB_LATEST,
        .sccb_i2c_port = GLASSES_I2C_PORT,
    };

    ESP_RETURN_ON_ERROR(esp_camera_init(&config), TAG, "esp_camera_init failed");

    sensor_t *sensor = esp_camera_sensor_get();
    ESP_RETURN_ON_FALSE(sensor, ESP_FAIL, TAG, "camera sensor handle is null");
    ESP_LOGI(TAG, "Sensor PID=0x%04x, profile=%s, native JPEG, quality=%d, fb_count=%d",
             sensor->id.PID, configured_frame_size_name(), CONFIG_CAM_JPEG_QUALITY,
             CONFIG_CAM_FB_COUNT);

    if (sensor->id.PID == OV3660_PID) {
        sensor->set_vflip(sensor, 1);
        sensor->set_brightness(sensor, 1);
        sensor->set_saturation(sensor, -2);
        ESP_LOGI(TAG, "OV3660 detected and orientation/color defaults applied");
    }
    return ESP_OK;
}

static esp_err_t index_handler(httpd_req_t *req)
{
    httpd_resp_set_type(req, "text/html");
    httpd_resp_set_hdr(req, "Cache-Control", "no-store");
    return httpd_resp_send(req, INDEX_HTML, HTTPD_RESP_USE_STRLEN);
}

static esp_err_t capture_handler(httpd_req_t *req)
{
    camera_fb_t *fb = esp_camera_fb_get();
    if (!fb) {
        httpd_resp_send_500(req);
        return ESP_FAIL;
    }
    httpd_resp_set_type(req, "image/jpeg");
    httpd_resp_set_hdr(req, "Cache-Control", "no-store");
    esp_err_t ret = httpd_resp_send(req, (const char *)fb->buf, fb->len);
    esp_camera_fb_return(fb);
    return ret;
}

static esp_err_t stream_handler(httpd_req_t *req)
{
#if CONFIG_GLASSES_IMU_SYNC_ENABLE
    static uint32_t next_stream_id;
    const uint32_t stream_id=++next_stream_id;
    uint64_t frame_seq=0;
    int64_t previous_source=0;
#endif
    ESP_RETURN_ON_ERROR(httpd_resp_set_type(req, STREAM_TYPE), TAG, "stream content type failed");
    httpd_resp_set_hdr(req, "Access-Control-Allow-Origin", "*");
    httpd_resp_set_hdr(req, "Cache-Control", "no-store, no-cache, must-revalidate");

    portENTER_CRITICAL(&s_stats_lock);
    s_stream_started_us = esp_timer_get_time();
    s_stream_frames = 0;
    s_stream_bytes = 0;
    s_stream_errors = 0;
    s_last_frame_us = 0;
    portEXIT_CRITICAL(&s_stats_lock);

    while (true) {
        int64_t frame_begin = esp_timer_get_time();
        camera_fb_t *fb = esp_camera_fb_get();
        if (!fb) {
            portENTER_CRITICAL(&s_stats_lock);
            s_stream_errors++;
            portEXIT_CRITICAL(&s_stats_lock);
            vTaskDelay(pdMS_TO_TICKS(2));
            continue;
        }

        char header[512];
#if CONFIG_GLASSES_IMU_SYNC_ENABLE
        const int64_t source=(int64_t)fb->timestamp.tv_sec*1000000+fb->timestamp.tv_usec;
        const char *time_status=source<=0?"zero-or-negative":
            source>esp_timer_get_time()?"future":
            previous_source && source<=previous_source?"non-monotonic":"ok";
        previous_source=source;
        int header_len=sync_frame_header(header,sizeof(header),fb->len,glasses_sync_boot_id(),
                                         stream_id,++frame_seq,source,time_status);
#else
        int header_len = snprintf(header, sizeof(header),
                                  "Content-Type: image/jpeg\r\nContent-Length: %u\r\n\r\n",
                                  (unsigned)fb->len);
#endif
        if (header_len<0 || (size_t)header_len>=sizeof(header)) {
            esp_camera_fb_return(fb);
            ESP_LOGE(TAG,"Frame metadata overflow");
            return ESP_FAIL;
        }
        esp_err_t ret = httpd_resp_send_chunk(req, STREAM_BOUNDARY, strlen(STREAM_BOUNDARY));
        if (ret == ESP_OK) ret = httpd_resp_send_chunk(req, header, header_len);
        if (ret == ESP_OK) ret = httpd_resp_send_chunk(req, (const char *)fb->buf, fb->len);

        size_t frame_len = fb->len;
        esp_camera_fb_return(fb);

        portENTER_CRITICAL(&s_stats_lock);
        if (ret == ESP_OK) {
            s_stream_frames++;
            s_stream_bytes += frame_len;
            s_last_frame_us = esp_timer_get_time() - frame_begin;
        } else {
            s_stream_errors++;
        }
        portEXIT_CRITICAL(&s_stats_lock);

        if (ret != ESP_OK) {
            ESP_LOGI(TAG, "Stream client disconnected after %" PRIu64 " frames", s_stream_frames);
            break;
        }
    }
    return ESP_OK;
}

static esp_err_t stats_handler(httpd_req_t *req)
{
    uint64_t frames;
    uint64_t bytes;
    uint64_t errors;
    int64_t started_us;
    int64_t last_frame_us;

    portENTER_CRITICAL(&s_stats_lock);
    frames = s_stream_frames;
    bytes = s_stream_bytes;
    errors = s_stream_errors;
    started_us = s_stream_started_us;
    last_frame_us = s_last_frame_us;
    portEXIT_CRITICAL(&s_stats_lock);

    int64_t now = esp_timer_get_time();
    double elapsed_s = started_us > 0 ? (now - started_us) / 1000000.0 : 0.0;
    double fps = elapsed_s > 0.0 ? frames / elapsed_s : 0.0;
    double avg_bytes = frames > 0 ? (double)bytes / frames : 0.0;

    wifi_ap_record_t ap = {0};
    int rssi = (esp_wifi_sta_get_ap_info(&ap) == ESP_OK) ? ap.rssi : 0;

    char json[384];
    int len = snprintf(json, sizeof(json),
                       "{\"profile\":\"%s\",\"native_jpeg\":true,\"frames\":%" PRIu64
                       ",\"fps\":%.2f,\"last_frame_ms\":%.2f,\"avg_frame_bytes\":%.0f"
                       ",\"stream_errors\":%" PRIu64 ",\"wifi_rssi_dbm\":%d"
                       ",\"free_heap\":%u,\"min_free_heap\":%u}",
                       configured_frame_size_name(), frames, fps, last_frame_us / 1000.0,
                       avg_bytes, errors, rssi, (unsigned)esp_get_free_heap_size(),
                       (unsigned)esp_get_minimum_free_heap_size());

    httpd_resp_set_type(req, "application/json");
    httpd_resp_set_hdr(req, "Cache-Control", "no-store");
    return httpd_resp_send(req, json, len);
}

static esp_err_t sensor_bench_handler(httpd_req_t *req)
{
    uint64_t bytes = 0;
    uint32_t errors = 0;
    int64_t started_us = esp_timer_get_time();

    for (int i = 0; i < SENSOR_BENCH_FRAMES; ++i) {
        camera_fb_t *fb = esp_camera_fb_get();
        if (!fb) {
            errors++;
            continue;
        }
        bytes += fb->len;
        esp_camera_fb_return(fb);
    }

    int64_t elapsed_us = esp_timer_get_time() - started_us;
    uint32_t captured = SENSOR_BENCH_FRAMES - errors;
    double elapsed_s = elapsed_us / 1000000.0;
    double fps = elapsed_s > 0.0 ? captured / elapsed_s : 0.0;
    double avg_bytes = captured > 0 ? (double)bytes / captured : 0.0;

    char json[256];
    int len = snprintf(json, sizeof(json),
                       "{\"profile\":\"%s\",\"requested_frames\":%d,"
                       "\"captured_frames\":%" PRIu32 ",\"errors\":%" PRIu32
                       ",\"elapsed_s\":%.3f,"
                       "\"sensor_fps\":%.2f,\"avg_frame_bytes\":%.0f}",
                       configured_frame_size_name(), SENSOR_BENCH_FRAMES, captured,
                       errors, elapsed_s, fps, avg_bytes);

    httpd_resp_set_type(req, "application/json");
    httpd_resp_set_hdr(req, "Cache-Control", "no-store");
    return httpd_resp_send(req, json, len);
}

static esp_err_t drivers_handler(httpd_req_t *req)
{
    glasses_board_status_t status;
    glasses_board_get_status(&status);

    char json[256];
    int len = snprintf(json, sizeof(json),
                       "{\"i2c\":%s,\"io_expander\":%s,\"buttons\":%s,"
                       "\"audio\":%s,\"audio_self_test\":%s,\"speaker_pa\":%s,"
                       "\"battery_hold_requested\":%s}",
                       status.i2c_ready ? "true" : "false",
                       status.io_expander_ready ? "true" : "false",
                       status.buttons_ready ? "true" : "false",
                       status.audio_ready ? "true" : "false",
                       status.audio_self_test_ok ? "true" : "false",
                       status.speaker_pa_enabled ? "true" : "false",
                       status.battery_hold_requested ? "true" : "false");
    httpd_resp_set_type(req, "application/json");
    httpd_resp_set_hdr(req, "Cache-Control", "no-store");
    return httpd_resp_send(req, json, len);
}

static void run_boot_sensor_bench(void)
{
    uint64_t bytes = 0;
    uint32_t errors = 0;
    int64_t started_us = esp_timer_get_time();

    for (int i = 0; i < SENSOR_BENCH_FRAMES; ++i) {
        camera_fb_t *fb = esp_camera_fb_get();
        if (!fb) {
            errors++;
            continue;
        }
        bytes += fb->len;
        esp_camera_fb_return(fb);
    }

    uint32_t captured = SENSOR_BENCH_FRAMES - errors;
    double elapsed_s = (esp_timer_get_time() - started_us) / 1000000.0;
    double fps = elapsed_s > 0.0 ? captured / elapsed_s : 0.0;
    double avg_bytes = captured > 0 ? (double)bytes / captured : 0.0;
    ESP_LOGI(TAG, "BOOT_SENSOR_BENCH profile=%s frames=%" PRIu32
             " errors=%" PRIu32 " elapsed=%.3fs fps=%.2f avg_bytes=%.0f",
             configured_frame_size_name(), captured, errors, elapsed_s, fps, avg_bytes);
}

static esp_err_t start_web_server(void)
{
    httpd_config_t config = HTTPD_DEFAULT_CONFIG();
    config.max_uri_handlers = 8;
    config.stack_size = 8192;
    config.send_wait_timeout = 10;
    config.recv_wait_timeout = 10;

    ESP_RETURN_ON_ERROR(httpd_start(&s_httpd, &config), TAG, "HTTP server start failed");

    const httpd_uri_t index_uri = {.uri = "/", .method = HTTP_GET, .handler = index_handler};
#if CONFIG_CAM_HTTP_STREAM_ENABLE
    const httpd_uri_t stream_uri = {.uri = "/stream", .method = HTTP_GET, .handler = stream_handler};
#endif
    const httpd_uri_t capture_uri = {.uri = "/capture", .method = HTTP_GET, .handler = capture_handler};
    const httpd_uri_t stats_uri = {.uri = "/stats", .method = HTTP_GET, .handler = stats_handler};
    const httpd_uri_t bench_uri = {.uri = "/bench", .method = HTTP_GET, .handler = sensor_bench_handler};
    const httpd_uri_t drivers_uri = {.uri = "/drivers", .method = HTTP_GET, .handler = drivers_handler};

    ESP_RETURN_ON_ERROR(httpd_register_uri_handler(s_httpd, &index_uri), TAG, "register / failed");
#if CONFIG_CAM_HTTP_STREAM_ENABLE
    ESP_RETURN_ON_ERROR(httpd_register_uri_handler(s_httpd, &stream_uri), TAG, "register /stream failed");
#endif
    ESP_RETURN_ON_ERROR(httpd_register_uri_handler(s_httpd, &capture_uri), TAG, "register /capture failed");
    ESP_RETURN_ON_ERROR(httpd_register_uri_handler(s_httpd, &stats_uri), TAG, "register /stats failed");
    ESP_RETURN_ON_ERROR(httpd_register_uri_handler(s_httpd, &bench_uri), TAG, "register /bench failed");
    ESP_RETURN_ON_ERROR(httpd_register_uri_handler(s_httpd, &drivers_uri), TAG, "register /drivers failed");
    return ESP_OK;
}

void app_main(void)
{
#if CONFIG_GLASSES_PERIPHERAL_BENCH_ENABLE
    /* Motor modules must not float while wifi_init_sta waits for an AP. */
    ESP_ERROR_CHECK(glasses_motor_outputs_off());
#endif
    esp_err_t ret = nvs_flash_init();
    if (ret == ESP_ERR_NVS_NO_FREE_PAGES || ret == ESP_ERR_NVS_NEW_VERSION_FOUND) {
        ESP_ERROR_CHECK(nvs_flash_erase());
        ESP_ERROR_CHECK(nvs_flash_init());
    }

    ESP_LOGI(TAG, "Starting OV3660 camera and board-driver benchmark");
    ESP_ERROR_CHECK(glasses_board_init());
#if CONFIG_GLASSES_IMU_SYNC_ENABLE
    glasses_sync_identity_init();
#endif
    ESP_ERROR_CHECK(camera_init());
    ESP_ERROR_CHECK(glasses_board_audio_init());
#if CONFIG_GLASSES_PERIPHERAL_BENCH_ENABLE
    ret = glasses_board_audio_self_test();
    if (ret != ESP_OK) {
        ESP_LOGW(TAG, "Audio self-test failed (%s); manual bench continues with failure recorded in /drivers",
                 esp_err_to_name(ret));
    }
#else
    ESP_ERROR_CHECK(glasses_board_audio_self_test());
#endif
#if CONFIG_GLASSES_IMU_SYNC_ENABLE
    ESP_ERROR_CHECK(glasses_imu_stream_start());
#endif
    run_boot_sensor_bench();
#if CONFIG_GLASSES_BENCH_MOTOR_CYCLE_ENABLE
    ESP_ERROR_CHECK(glasses_motor_cycle_start());
#endif
    ESP_ERROR_CHECK(wifi_init_sta());
#if CONFIG_CAM_HTTP_SERVER_ENABLE
    ESP_ERROR_CHECK(start_web_server());
    ESP_LOGI(TAG, "HTTP diagnostics server ready");
#endif
#if CONFIG_GLASSES_TRANSPORT_ENABLE
    ESP_ERROR_CHECK(glasses_transport_start());
#endif
#if CONFIG_GLASSES_PERIPHERAL_BENCH_ENABLE
    ESP_ERROR_CHECK(glasses_peripheral_bench_start());
#endif
    ESP_LOGI(TAG, "Camera firmware ready");
}
