/* Manual bench, independent of the camera HTTP server and transport v1.
 * Register layout: https://github.com/WITMOTION/WitIIC_JY901
 * No calibration, flash-save or automatic sound. Vibration at boot requires
 * the explicitly enabled motor-cycle diagnostic build.
 */
#include "sdkconfig.h"
#if CONFIG_GLASSES_PERIPHERAL_BENCH_ENABLE
#include "glasses_peripheral_bench.h"

#include <inttypes.h>
#include <math.h>
#include <stdlib.h>
#include <string.h>
#include "esp_check.h"
#include "esp_http_server.h"
#include "esp_log.h"
#include "esp_timer.h"
#include "freertos/FreeRTOS.h"
#include "freertos/task.h"
#include "glasses_board.h"
#include "glasses_imu_stream.h"
#include "speaker_volume.h"

#define PCM_RATE 24000
#define MAX_PCM_BYTES (PCM_RATE * 2 * 10)
#define MOTOR_LEFT_PIN GPIO_NUM_44
#define MOTOR_RIGHT_PIN GPIO_NUM_43
#if CONFIG_GLASSES_BENCH_MOTOR_CYCLE_ENABLE
#define MOTOR_CYCLE_JSON "true"
#else
#define MOTOR_CYCLE_JSON "false"
#endif

static const char *TAG = "peripheral_bench";
static httpd_handle_t s_server;
#if !CONFIG_GLASSES_IMU_SYNC_ENABLE
static i2c_master_dev_handle_t s_imu;
#endif

static esp_err_t json_reply(httpd_req_t *req, const char *body)
{
    httpd_resp_set_type(req, "application/json");
    httpd_resp_set_hdr(req, "Cache-Control", "no-store");
    return httpd_resp_sendstr(req, body);
}

static esp_err_t state_handler(httpd_req_t *req)
{
    glasses_board_status_t board;
    glasses_board_get_status(&board);
    char text[2304];
    snprintf(text, sizeof(text),
             "{\"bench\":\"peripherals_v2\",\"uptime_ms\":%" PRId64
             ",\"speaker_pa\":%s,\"motor_enabled\":%s,\"motor_gpio\":44,"
             "\"battery_hold_requested\":%s,"
             "\"motor_cycle_enabled\":" MOTOR_CYCLE_JSON ","
             "\"motor_gpios\":{\"left\":44,\"right\":43},\"motor_default_channel\":\"left\","
             "\"pcm_rate\":24000,\"pcm_bits\":16,\"pcm_channels\":1,"
             "\"speaker_volume_query\":true,\"speaker_volume_default\":25,\"speaker_volume_max\":70,"
             "\"imu_address\":80,\"scl_gpio\":7,\"sda_gpio\":8}",
             esp_timer_get_time() / 1000,
             board.speaker_pa_enabled ? "true" : "false",
#if CONFIG_GLASSES_BENCH_MOTOR_ENABLE
             "true",
#else
             "false",
#endif
             board.battery_hold_requested ? "true" : "false"
    );
#if CONFIG_GLASSES_IMU_SYNC_ENABLE
    size_t used=strlen(text);
    if (!used || text[used-1]!='}') return ESP_FAIL;
    text[--used]=0;
    int n=snprintf(text+used,sizeof(text)-used,",\"imu_stream\":");
    if (n<0 || (size_t)n>=sizeof(text)-used) return ESP_FAIL;
    used+=(size_t)n;
    n=glasses_imu_state_json(text+used,sizeof(text)-used-1);
    if (n<0) return ESP_FAIL;
    used+=(size_t)n; text[used++]='}';text[used]=0;
#endif
    return json_reply(req, text);
}

static esp_err_t imu_handler(httpd_req_t *req)
{
#if CONFIG_GLASSES_IMU_SYNC_ENABLE
    return glasses_imu_cached_handler(req);
#else
    const int64_t started = esp_timer_get_time();
    uint8_t motion[12], angles[6];
    uint8_t reg = 0x34;  /* AX..GZ: six little-endian 16-bit registers. */
    esp_err_t result = i2c_master_transmit_receive(s_imu, &reg, 1,
                                                 motion, sizeof(motion), 100);
    if (result == ESP_OK) {
        reg = 0x3d;  /* Roll, Pitch, Yaw. */
        result = i2c_master_transmit_receive(s_imu, &reg, 1,
                                            angles, sizeof(angles), 100);
    }
    if (result != ESP_OK) {
        char text[192];
        snprintf(text, sizeof(text),
                 "{\"ok\":false,\"address\":80,\"error\":\"%s\","
                 "\"hint\":\"Check JY61P power, SCL/SDA and I2C address\"}",
                 esp_err_to_name(result));
        httpd_resp_set_status(req, "503 Service Unavailable");
        return json_reply(req, text);
    }
    int16_t values[9];
    for (int i = 0; i < 6; i++) {
        values[i] = (int16_t)((uint16_t)motion[2*i] |
                             ((uint16_t)motion[2*i+1] << 8));
    }
    for (int i = 0; i < 3; i++) {
        values[i+6] = (int16_t)((uint16_t)angles[2*i] |
                               ((uint16_t)angles[2*i+1] << 8));
    }
    char text[640];
    snprintf(text, sizeof(text),
             "{\"ok\":true,\"address\":80,\"read_start_us\":%" PRId64
             ",\"read_end_us\":%" PRId64 ",\"atomic_sample\":false,"
             "\"raw\":[%d,%d,%d,%d,%d,%d,%d,%d,%d],"
             "\"accel_g\":[%.5f,%.5f,%.5f],\"gyro_dps\":[%.4f,%.4f,%.4f],"
             "\"angles_deg\":[%.4f,%.4f,%.4f]}",
             started, esp_timer_get_time(),
             values[0], values[1], values[2], values[3], values[4], values[5],
             values[6], values[7], values[8],
             values[0]*16.0/32768, values[1]*16.0/32768, values[2]*16.0/32768,
             values[3]*2000.0/32768, values[4]*2000.0/32768, values[5]*2000.0/32768,
             values[6]*180.0/32768, values[7]*180.0/32768, values[8]*180.0/32768);
    return json_reply(req, text);
#endif
}

/* Every exit disables the PA, including open/unmute/write failures. */
static int play_pcm(const int16_t *mono, size_t samples, int volume)
{
    if (volume < 0 || volume > SPEAKER_VOLUME_MAX) return ESP_CODEC_DEV_INVALID_ARG;
    int result = glasses_board_speaker_open(PCM_RATE, 16, 2, volume);
    if (result == ESP_CODEC_DEV_OK) result = glasses_board_speaker_set_mute(false);
    if (result == ESP_CODEC_DEV_OK && glasses_board_speaker_pa_enable(true) != ESP_OK) {
        result = ESP_CODEC_DEV_WRONG_STATE;
    }
    int16_t stereo[960];
    for (size_t offset = 0; result == ESP_CODEC_DEV_OK && offset < samples;) {
        const size_t count = samples-offset > 480 ? 480 : samples-offset;
        for (size_t i = 0; i < count; ++i) {
            stereo[2*i] = mono[offset+i];
            stereo[2*i+1] = mono[offset+i];
        }
        result = glasses_board_speaker_write(stereo, count*2*sizeof(int16_t));
        offset += count;
    }
    /* Flush the final audio block before muting/closing the I2S channel. */
    if (result == ESP_CODEC_DEV_OK) {
        int16_t silence[960] = {0};
        result = glasses_board_speaker_write(silence, sizeof(silence));
        vTaskDelay(pdMS_TO_TICKS(40));
    }
    (void)glasses_board_speaker_pa_enable(false);
    (void)glasses_board_speaker_set_mute(true);
    const int closed = glasses_board_speaker_close();
    return result == ESP_CODEC_DEV_OK ? closed : result;
}

static esp_err_t tone_handler(httpd_req_t *req)
{
    if (req->content_len != 0) {
        return httpd_resp_send_err(req, HTTPD_400_BAD_REQUEST, "Empty body required");
    }
    const size_t samples = PCM_RATE * 3 / 5;
    int16_t *data = calloc(samples, sizeof(int16_t));
    if (!data) return httpd_resp_send_err(req, HTTPD_500_INTERNAL_SERVER_ERROR, "No memory");
    for (int note = 0; note < 2; ++note) {
        for (int i = 0; i < PCM_RATE/4; ++i) {
            float envelope = fminf(1.0f, fminf(i/240.0f, (PCM_RATE/4-1-i)/240.0f));
            data[note*(PCM_RATE*35/100)+i] = (int16_t)(4096*envelope*
                sinf(6.28318530718f*(note ? 880 : 660)*i/PCM_RATE));
        }
    }
    const int result = play_pcm(data, samples, SPEAKER_VOLUME_DEFAULT);
    free(data);
    char text[128];
    snprintf(text, sizeof(text), "{\"ok\":%s,\"codec_result\":%d,\"duration_ms\":600}",
             result == ESP_CODEC_DEV_OK ? "true" : "false", result);
    if (result != ESP_CODEC_DEV_OK) httpd_resp_set_status(req, "500 Internal Server Error");
    return json_reply(req, text);
}

static esp_err_t pcm_handler(httpd_req_t *req)
{
    char query[32] = {0};
    int volume = SPEAKER_VOLUME_DEFAULT;
    const size_t query_len = httpd_req_get_url_query_len(req);
    if (query_len >= sizeof(query) ||
        (query_len && httpd_req_get_url_query_str(req, query, sizeof(query)) != ESP_OK) ||
        !speaker_volume_parse(query, &volume)) {
        return httpd_resp_send_err(req, HTTPD_400_BAD_REQUEST, "Expected volume=0..70");
    }
    if (req->content_len < 2 || req->content_len > MAX_PCM_BYTES || req->content_len%2) {
        return httpd_resp_send_err(req, HTTPD_400_BAD_REQUEST,
                                  "Need 24kHz signed LE16 mono PCM, at most 10 seconds");
    }
    const size_t length = req->content_len;
    int16_t *data = malloc(length);
    if (!data) return httpd_resp_send_err(req, HTTPD_500_INTERNAL_SERVER_ERROR, "No memory");
    size_t received = 0;
    const int64_t deadline = esp_timer_get_time() + 15000000;
    while (received < length && esp_timer_get_time() < deadline) {
        int count = httpd_req_recv(req, (char *)data+received, length-received);
        if (count <= 0) break;
        received += count;
    }
    if (received != length) {
        free(data);
        /* Connection is closed on failure so unread body cannot become a request. */
        httpd_resp_send_err(req, HTTPD_400_BAD_REQUEST, "Incomplete PCM upload; nothing played");
        return ESP_FAIL;
    }
    const int result = play_pcm(data, length/2, volume);
    free(data);
    char text[128];
    snprintf(text, sizeof(text), "{\"ok\":%s,\"codec_result\":%d,\"pcm_bytes\":%u,\"volume\":%d}",
             result == ESP_CODEC_DEV_OK ? "true" : "false", result, (unsigned)length, volume);
    if (result != ESP_CODEC_DEV_OK) httpd_resp_set_status(req, "500 Internal Server Error");
    return json_reply(req, text);
}

static esp_err_t motor_handler(httpd_req_t *req)
{
    if (req->content_len != 0) {
        return httpd_resp_send_err(req, HTTPD_400_BAD_REQUEST, "Empty body required");
    }
#if CONFIG_GLASSES_BENCH_MOTOR_CYCLE_ENABLE
    httpd_resp_set_status(req, "409 Conflict");
    return json_reply(req, "{\"ok\":false,\"reason\":\"Board motor cycle owns GPIO43/44\"}");
#elif CONFIG_GLASSES_BENCH_MOTOR_ENABLE
    long pulse_ms = 100;
    char channel[8] = "left";
    const size_t query_len = httpd_req_get_url_query_len(req);
    if (query_len) {
        char query[64], value[12];
        if (query_len >= sizeof(query) ||
            httpd_req_get_url_query_str(req, query, sizeof(query)) != ESP_OK) {
            return httpd_resp_send_err(req, HTTPD_400_BAD_REQUEST, "Invalid motor query");
        }
        bool recognized = false;
        esp_err_t field = httpd_query_key_value(query, "ms", value, sizeof(value));
        if (field == ESP_OK) {
            recognized = true;
            char *end = NULL;
            pulse_ms = strtol(value, &end, 10);
            if (end == value || *end || pulse_ms < 20 || pulse_ms > 500) {
                return httpd_resp_send_err(req, HTTPD_400_BAD_REQUEST, "Expected ms=20..500");
            }
        } else if (field != ESP_ERR_NOT_FOUND) {
            return httpd_resp_send_err(req, HTTPD_400_BAD_REQUEST, "Invalid pulse duration");
        }
        field = httpd_query_key_value(query, "channel", channel, sizeof(channel));
        if (field == ESP_OK) {
            recognized = true;
        } else if (field != ESP_ERR_NOT_FOUND) {
            return httpd_resp_send_err(req, HTTPD_400_BAD_REQUEST, "Invalid motor channel");
        }
        if (!recognized) {
            return httpd_resp_send_err(req, HTTPD_400_BAD_REQUEST, "Expected ms or channel");
        }
    }
    const bool left = strcmp(channel, "left") == 0 || strcmp(channel, "both") == 0;
    const bool right = strcmp(channel, "right") == 0 || strcmp(channel, "both") == 0;
    if (!left && !right) {
        return httpd_resp_send_err(req, HTTPD_400_BAD_REQUEST, "Expected left, right or both");
    }
    /* Bounded locally even if the HTTP client disconnects. */
    const esp_err_t on_left = gpio_set_level(MOTOR_LEFT_PIN, left ? 1 : 0);
    const esp_err_t on_right = gpio_set_level(MOTOR_RIGHT_PIN, right ? 1 : 0);
    if (on_left == ESP_OK && on_right == ESP_OK) {
        vTaskDelay(pdMS_TO_TICKS(pulse_ms));
    }
    const esp_err_t off_left = gpio_set_level(MOTOR_LEFT_PIN, 0);
    const esp_err_t off_right = gpio_set_level(MOTOR_RIGHT_PIN, 0);
    if (on_left != ESP_OK || on_right != ESP_OK || off_left != ESP_OK || off_right != ESP_OK) {
        return httpd_resp_send_err(req, HTTPD_500_INTERNAL_SERVER_ERROR, "Motor GPIO failed");
    }
    char text[192];
    const char *gpios = left && right ? "[44,43]" : left ? "[44]" : "[43]";
    snprintf(text, sizeof(text), "{\"ok\":true,\"channel\":\"%s\",\"gpios\":%s,\"pulse_ms\":%ld,\"final_level\":0}",
             channel, gpios, pulse_ms);
    return json_reply(req, text);
#else
    httpd_resp_set_status(req, "409 Conflict");
    return json_reply(req, "{\"ok\":false,\"reason\":\"Motor disabled in this build\"}");
#endif
}

#if CONFIG_GLASSES_BENCH_MOTOR_ENABLE
static esp_err_t motor_pins_init(void)
{
    ESP_RETURN_ON_ERROR(gpio_set_level(MOTOR_LEFT_PIN, 0), TAG, "left motor latch failed");
    ESP_RETURN_ON_ERROR(gpio_set_level(MOTOR_RIGHT_PIN, 0), TAG, "right motor latch failed");
    const gpio_config_t motor = {
        .pin_bit_mask = (1ULL << MOTOR_LEFT_PIN) | (1ULL << MOTOR_RIGHT_PIN),
        .mode = GPIO_MODE_OUTPUT,
        .pull_down_en = GPIO_PULLDOWN_ENABLE,
    };
    return gpio_config(&motor);
}
#endif

esp_err_t glasses_motor_outputs_off(void)
{
#if CONFIG_GLASSES_BENCH_MOTOR_ENABLE
    ESP_RETURN_ON_ERROR(motor_pins_init(), TAG, "early motor-off initialization failed");
    ESP_LOGI(TAG, "Manual motors held LOW before Wi-Fi (GPIO44/43)");
#endif
    return ESP_OK;
}

#if CONFIG_GLASSES_BENCH_MOTOR_CYCLE_ENABLE
static bool cycle_pulse(bool left, bool right, uint32_t rest_ms, const char *name)
{
    ESP_LOGI(TAG, "motor cycle: %s 500ms, then rest %" PRIu32 "ms", name, rest_ms);
    const esp_err_t on_left = gpio_set_level(MOTOR_LEFT_PIN, left ? 1 : 0);
    const esp_err_t on_right = gpio_set_level(MOTOR_RIGHT_PIN, right ? 1 : 0);
    if (on_left == ESP_OK && on_right == ESP_OK) {
        vTaskDelay(pdMS_TO_TICKS(500));
    }
    const esp_err_t off_left = gpio_set_level(MOTOR_LEFT_PIN, 0);
    const esp_err_t off_right = gpio_set_level(MOTOR_RIGHT_PIN, 0);
    if (on_left != ESP_OK || on_right != ESP_OK || off_left != ESP_OK || off_right != ESP_OK) {
        return false;
    }
    vTaskDelay(pdMS_TO_TICKS(rest_ms));
    return true;
}

static void motor_cycle_task(void *arg)
{
    (void)arg;
    vTaskDelay(pdMS_TO_TICKS(2000));
    for (;;) {
        if (!cycle_pulse(true, true, 2000, "both") ||
            !cycle_pulse(true, false, 2000, "RX/GPIO44") ||
            !cycle_pulse(false, true, 3000, "TX/GPIO43")) {
            break;
        }
    }
    gpio_set_level(MOTOR_LEFT_PIN, 0);
    gpio_set_level(MOTOR_RIGHT_PIN, 0);
    ESP_LOGE(TAG, "motor cycle stopped after GPIO error");
    vTaskDelete(NULL);
}

esp_err_t glasses_motor_cycle_start(void)
{
    ESP_RETURN_ON_ERROR(motor_pins_init(), TAG, "cycle GPIO initialization failed");
    ESP_RETURN_ON_FALSE(xTaskCreate(motor_cycle_task, "motor_cycle", 3072, NULL, 3, NULL) == pdPASS,
                        ESP_ERR_NO_MEM, TAG, "motor cycle task creation failed");
    ESP_LOGW(TAG, "Motor-cycle diagnostic starts before Wi-Fi: both -> RX -> TX forever");
    return ESP_OK;
}
#endif

esp_err_t glasses_peripheral_bench_start(void)
{
#if !CONFIG_GLASSES_IMU_SYNC_ENABLE
    const i2c_device_config_t device = {
        .dev_addr_length = I2C_ADDR_BIT_LEN_7,
        .device_address = 0x50,
        .scl_speed_hz = 100000,
        .scl_wait_us = 20000,
    };
    ESP_RETURN_ON_ERROR(i2c_master_bus_add_device(glasses_board_i2c_handle(),
                        &device, &s_imu), TAG, "JY61P handle failed");
#endif
#if CONFIG_GLASSES_BENCH_MOTOR_ENABLE && !CONFIG_GLASSES_BENCH_MOTOR_CYCLE_ENABLE
    ESP_RETURN_ON_ERROR(motor_pins_init(), TAG, "motor configuration failed");
#endif
    httpd_config_t config = HTTPD_DEFAULT_CONFIG();
    config.server_port = 8081;
    config.ctrl_port = 32769;
    config.stack_size = 8192;
    config.recv_wait_timeout = 3;
    config.send_wait_timeout = 3;
    ESP_RETURN_ON_ERROR(httpd_start(&s_server, &config), TAG, "bench HTTP start failed");
    const httpd_uri_t routes[] = {
        {.uri="/state", .method=HTTP_GET, .handler=state_handler},
        {.uri="/imu", .method=HTTP_GET, .handler=imu_handler},
#if CONFIG_GLASSES_IMU_SYNC_ENABLE
        {.uri="/imu/batch", .method=HTTP_GET, .handler=glasses_imu_batch_handler},
#endif
        {.uri="/tone", .method=HTTP_POST, .handler=tone_handler},
        {.uri="/pcm", .method=HTTP_POST, .handler=pcm_handler},
        {.uri="/motor", .method=HTTP_POST, .handler=motor_handler},
    };
    for (size_t i = 0; i < sizeof(routes)/sizeof(routes[0]); ++i) {
        ESP_RETURN_ON_ERROR(httpd_register_uri_handler(s_server, &routes[i]), TAG,
                            "bench route registration failed");
    }
#if CONFIG_GLASSES_BENCH_MOTOR_CYCLE_ENABLE
    ESP_LOGW(TAG, "Motor-cycle diagnostic: both -> RX -> TX, repeating at boot");
#else
    ESP_LOGI(TAG, "Manual peripheral bench on :8081; PA off; no boot actuation");
#endif
    return ESP_OK;
}
#endif
