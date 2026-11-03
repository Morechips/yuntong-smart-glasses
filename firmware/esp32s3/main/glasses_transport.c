#include "glasses_transport.h"

#include <errno.h>
#include <inttypes.h>
#include <stdbool.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/select.h>

#include "esp_camera.h"
#include "esp_check.h"
#include "esp_codec_dev.h"
#include "esp_log.h"
#include "esp_mac.h"
#include "esp_netif.h"
#include "esp_system.h"
#include "esp_timer.h"
#include "esp_wifi.h"
#include "freertos/FreeRTOS.h"
#include "freertos/event_groups.h"
#include "freertos/semphr.h"
#include "freertos/stream_buffer.h"
#include "freertos/task.h"
#include "glasses_board.h"
#include "glasses_protocol.h"
#include "lwip/inet.h"
#include "lwip/sockets.h"
#include "lwip/tcp.h"
#include "sdkconfig.h"

#if CONFIG_GLASSES_TRANSPORT_ENABLE

#define SPEAKER_REQUEST_BIT BIT0
#define SPEAKER_END_BIT BIT1
#define MIC_FRAMES_PER_PACKET 320
#define MIC_RAW_BYTES (MIC_FRAMES_PER_PACKET * 2 * sizeof(int32_t))
#define MIC_PCM_BYTES (MIC_FRAMES_PER_PACKET * sizeof(int16_t))
#define SPEAKER_FRAMES_PER_PACKET 480
#define SPEAKER_MONO_BYTES (SPEAKER_FRAMES_PER_PACKET * sizeof(int16_t))
#define SPEAKER_STEREO_BYTES (SPEAKER_FRAMES_PER_PACKET * 2 * sizeof(int16_t))

static const char *TAG = "glasses_transport";
static SemaphoreHandle_t s_audio_mutex;
static StreamBufferHandle_t s_speaker_buffer;
static EventGroupHandle_t s_speaker_events;
static portMUX_TYPE s_stats_lock = portMUX_INITIALIZER_UNLOCKED;
static char s_device_id[24];

typedef struct {
    uint64_t video_frames;
    uint64_t video_bytes;
    uint64_t video_errors;
    uint64_t microphone_packets;
    uint64_t microphone_bytes;
    uint64_t microphone_errors;
    uint64_t speaker_packets;
    uint64_t speaker_bytes;
    uint64_t speaker_errors;
    uint64_t reconnects;
    bool speaker_active;
} transport_stats_t;

static transport_stats_t s_stats;

static void stats_add(uint64_t *counter, uint64_t amount)
{
    portENTER_CRITICAL(&s_stats_lock);
    *counter += amount;
    portEXIT_CRITICAL(&s_stats_lock);
}

static transport_stats_t stats_snapshot(void)
{
    transport_stats_t copy;
    portENTER_CRITICAL(&s_stats_lock);
    copy = s_stats;
    portEXIT_CRITICAL(&s_stats_lock);
    return copy;
}

static bool payload_crc_valid(const glasses_packet_header_t *header, const void *payload)
{
    return !(header->flags & GLASSES_FLAG_CRC32) ||
           glasses_payload_crc32(payload, header->payload_length) == header->payload_crc32;
}

static int gateway_socket(uint16_t port)
{
    esp_netif_t *netif = esp_netif_get_handle_from_ifkey("WIFI_STA_DEF");
    if (!netif) {
        errno = ENETDOWN;
        return -1;
    }
    esp_netif_ip_info_t info = {0};
    if (esp_netif_get_ip_info(netif, &info) != ESP_OK || info.gw.addr == 0) {
        errno = ENETDOWN;
        return -1;
    }

    int sock = socket(AF_INET, SOCK_STREAM, IPPROTO_IP);
    if (sock < 0) {
        return -1;
    }
    int yes = 1;
    struct timeval timeout = {.tv_sec = 2, .tv_usec = 0};
    setsockopt(sock, SOL_SOCKET, SO_KEEPALIVE, &yes, sizeof(yes));
    setsockopt(sock, IPPROTO_TCP, TCP_NODELAY, &yes, sizeof(yes));
    setsockopt(sock, SOL_SOCKET, SO_SNDTIMEO, &timeout, sizeof(timeout));
    setsockopt(sock, SOL_SOCKET, SO_RCVTIMEO, &timeout, sizeof(timeout));

    struct sockaddr_in destination = {
        .sin_family = AF_INET,
        .sin_port = htons(port),
        .sin_addr.s_addr = info.gw.addr,
    };
    if (connect(sock, (struct sockaddr *)&destination, sizeof(destination)) != 0) {
        close(sock);
        return -1;
    }
    stats_add(&s_stats.reconnects, 1);
    ESP_LOGI(TAG, "connected to X5 gateway " IPSTR ":%u", IP2STR(&info.gw), port);
    return sock;
}

static int send_hello(int sock, const char *channel, uint32_t *sequence)
{
    char json[256];
    int length = snprintf(json, sizeof(json),
                          "{\"device_id\":\"%s\",\"channel\":\"%s\","
                          "\"protocol\":1,\"firmware\":\"glasses-transport-1\"}",
                          s_device_id, channel);
    return glasses_socket_send_packet(sock, GLASSES_MSG_HELLO, GLASSES_FLAG_CRC32,
                                      (*sequence)++, esp_timer_get_time(), json, length);
}

static void retry_delay(void)
{
    vTaskDelay(pdMS_TO_TICKS(1500));
}

static void control_task(void *argument)
{
    (void)argument;
    uint32_t sequence = 0;
    while (true) {
        int sock = gateway_socket(CONFIG_GLASSES_CONTROL_PORT);
        if (sock < 0) {
            retry_delay();
            continue;
        }
        if (send_hello(sock, "control", &sequence) != 0) {
            close(sock);
            retry_delay();
            continue;
        }
        int64_t next_heartbeat = 0;
        while (true) {
            int64_t now = esp_timer_get_time();
            if (now >= next_heartbeat) {
                wifi_ap_record_t ap = {0};
                int rssi = esp_wifi_sta_get_ap_info(&ap) == ESP_OK ? ap.rssi : 0;
                glasses_board_status_t board;
                glasses_board_get_status(&board);
                transport_stats_t stats = stats_snapshot();
                char json[640];
                int length = snprintf(
                    json, sizeof(json),
                    "{\"device_id\":\"%s\",\"uptime_ms\":%" PRIu64
                    ",\"rssi_dbm\":%d,\"free_heap\":%u,\"video_frames\":%" PRIu64
                    ",\"video_bytes\":%" PRIu64 ",\"video_errors\":%" PRIu64
                    ",\"mic_packets\":%" PRIu64 ",\"mic_errors\":%" PRIu64
                    ",\"speaker_packets\":%" PRIu64 ",\"speaker_errors\":%" PRIu64
                    ",\"speaker_active\":%s,\"speaker_pa\":%s}",
                    s_device_id, (uint64_t)(now / 1000), rssi,
                    (unsigned)esp_get_free_heap_size(), stats.video_frames,
                    stats.video_bytes, stats.video_errors, stats.microphone_packets,
                    stats.microphone_errors, stats.speaker_packets, stats.speaker_errors,
                    stats.speaker_active ? "true" : "false",
                    board.speaker_pa_enabled ? "true" : "false");
                if (glasses_socket_send_packet(sock, GLASSES_MSG_HEARTBEAT,
                                               GLASSES_FLAG_CRC32, sequence++, now,
                                               json, length) != 0) {
                    break;
                }
                next_heartbeat = now + 2000000;
            }

            fd_set read_set;
            FD_ZERO(&read_set);
            FD_SET(sock, &read_set);
            struct timeval wait = {.tv_sec = 0, .tv_usec = 200000};
            int ready = select(sock + 1, &read_set, NULL, NULL, &wait);
            if (ready < 0) {
                break;
            }
            if (ready > 0) {
                glasses_packet_header_t header;
                if (glasses_socket_recv_header(sock, &header) != 0 ||
                    header.payload_length > 1024) {
                    break;
                }
                uint8_t payload[1025];
                if (header.payload_length > 0 &&
                    glasses_socket_recv_all(sock, payload, header.payload_length) != 0) {
                    break;
                }
                payload[header.payload_length] = 0;
                if (!payload_crc_valid(&header, payload)) {
                    ESP_LOGW(TAG, "control payload CRC mismatch");
                    break;
                }
            }
        }
        close(sock);
        retry_delay();
    }
}

static void video_task(void *argument)
{
    (void)argument;
    uint32_t sequence = 0;
    const int64_t frame_interval_us = 1000000 / CONFIG_GLASSES_VIDEO_MAX_FPS;
    while (true) {
        int sock = gateway_socket(CONFIG_GLASSES_VIDEO_PORT);
        if (sock < 0) {
            retry_delay();
            continue;
        }
        if (send_hello(sock, "video", &sequence) != 0) {
            close(sock);
            retry_delay();
            continue;
        }
        int64_t next_frame = esp_timer_get_time();
        while (true) {
            int64_t now = esp_timer_get_time();
            if (now < next_frame) {
                vTaskDelay(pdMS_TO_TICKS((next_frame - now) / 1000));
            }
            camera_fb_t *frame = esp_camera_fb_get();
            if (!frame) {
                stats_add(&s_stats.video_errors, 1);
                vTaskDelay(pdMS_TO_TICKS(2));
                continue;
            }
            int result = glasses_socket_send_packet(
                sock, GLASSES_MSG_VIDEO_JPEG, GLASSES_FLAG_CRC32, sequence++,
                esp_timer_get_time(), frame->buf, frame->len);
            size_t length = frame->len;
            esp_camera_fb_return(frame);
            if (result != 0) {
                stats_add(&s_stats.video_errors, 1);
                break;
            }
            stats_add(&s_stats.video_frames, 1);
            stats_add(&s_stats.video_bytes, length);
            next_frame += frame_interval_us;
            now = esp_timer_get_time();
            if (next_frame < now) {
                next_frame = now;
            }
        }
        close(sock);
        retry_delay();
    }
}

static int16_t clamp_s16(int32_t value)
{
    if (value > INT16_MAX) return INT16_MAX;
    if (value < INT16_MIN) return INT16_MIN;
    return (int16_t)value;
}

static void microphone_task(void *argument)
{
    (void)argument;
    int32_t *raw = malloc(MIC_RAW_BYTES);
    int16_t *mono = malloc(MIC_PCM_BYTES);
    if (!raw || !mono) {
        ESP_LOGE(TAG, "microphone buffers could not be allocated");
        free(raw);
        free(mono);
        vTaskDelete(NULL);
        return;
    }
    uint32_t sequence = 0;
    while (true) {
        int sock = gateway_socket(CONFIG_GLASSES_MICROPHONE_PORT);
        if (sock < 0) {
            retry_delay();
            continue;
        }
        if (send_hello(sock, "microphone", &sequence) != 0) {
            close(sock);
            retry_delay();
            continue;
        }
        bool socket_ok = true;
        while (socket_ok) {
            EventBits_t bits = xEventGroupGetBits(s_speaker_events);
            if (bits & SPEAKER_REQUEST_BIT) {
                vTaskDelay(pdMS_TO_TICKS(20));
                continue;
            }
            if (xSemaphoreTake(s_audio_mutex, pdMS_TO_TICKS(500)) != pdTRUE) {
                continue;
            }
            if (xEventGroupGetBits(s_speaker_events) & SPEAKER_REQUEST_BIT) {
                xSemaphoreGive(s_audio_mutex);
                continue;
            }
            int result = glasses_board_microphone_open(16000, 32, 2,
                                                        CONFIG_GLASSES_MIC_GAIN_DB);
            if (result != ESP_CODEC_DEV_OK) {
                ESP_LOGE(TAG, "microphone open failed: %d", result);
                stats_add(&s_stats.microphone_errors, 1);
                xSemaphoreGive(s_audio_mutex);
                vTaskDelay(pdMS_TO_TICKS(500));
                continue;
            }
            while (!(xEventGroupGetBits(s_speaker_events) & SPEAKER_REQUEST_BIT)) {
                result = glasses_board_microphone_read(raw, MIC_RAW_BYTES);
                if (result != ESP_CODEC_DEV_OK) {
                    ESP_LOGE(TAG, "microphone read failed: %d", result);
                    stats_add(&s_stats.microphone_errors, 1);
                    socket_ok = false;
                    break;
                }
                for (size_t index = 0; index < MIC_FRAMES_PER_PACKET; ++index) {
                    int32_t sample = raw[index * 2 + CONFIG_GLASSES_MIC_CHANNEL];
                    mono[index] = clamp_s16(sample >> CONFIG_GLASSES_MIC_SHIFT);
                }
                if (glasses_socket_send_packet(
                        sock, GLASSES_MSG_MICROPHONE_PCM, GLASSES_FLAG_CRC32,
                        sequence++, esp_timer_get_time(), mono, MIC_PCM_BYTES) != 0) {
                    stats_add(&s_stats.microphone_errors, 1);
                    socket_ok = false;
                    break;
                }
                stats_add(&s_stats.microphone_packets, 1);
                stats_add(&s_stats.microphone_bytes, MIC_PCM_BYTES);
            }
            (void)glasses_board_microphone_close();
            xSemaphoreGive(s_audio_mutex);
        }
        close(sock);
        retry_delay();
    }
}

static void speaker_receive_task(void *argument)
{
    (void)argument;
    uint8_t *payload = malloc(CONFIG_GLASSES_SPEAKER_MAX_PACKET);
    if (!payload) {
        ESP_LOGE(TAG, "speaker receive buffer could not be allocated");
        vTaskDelete(NULL);
        return;
    }
    uint32_t sequence = 0;
    while (true) {
        int sock = gateway_socket(CONFIG_GLASSES_SPEAKER_PORT);
        if (sock < 0) {
            retry_delay();
            continue;
        }
        if (send_hello(sock, "speaker", &sequence) != 0) {
            close(sock);
            retry_delay();
            continue;
        }
        while (true) {
            fd_set read_set;
            FD_ZERO(&read_set);
            FD_SET(sock, &read_set);
            struct timeval wait = {.tv_sec = 1, .tv_usec = 0};
            int ready = select(sock + 1, &read_set, NULL, NULL, &wait);
            if (ready < 0) {
                break;
            }
            if (ready == 0) {
                continue;
            }
            glasses_packet_header_t header;
            if (glasses_socket_recv_header(sock, &header) != 0 ||
                header.type != GLASSES_MSG_SPEAKER_PCM ||
                header.payload_length > CONFIG_GLASSES_SPEAKER_MAX_PACKET) {
                break;
            }
            if (header.payload_length > 0 &&
                glasses_socket_recv_all(sock, payload, header.payload_length) != 0) {
                break;
            }
            if (!payload_crc_valid(&header, payload)) {
                ESP_LOGW(TAG, "speaker payload CRC mismatch");
                stats_add(&s_stats.speaker_errors, 1);
                continue;
            }
            if (header.payload_length > 0) {
                size_t written = xStreamBufferSend(s_speaker_buffer, payload,
                                                   header.payload_length,
                                                   pdMS_TO_TICKS(200));
                if (written != header.payload_length) {
                    ESP_LOGW(TAG, "speaker jitter buffer full; dropped %u bytes",
                             (unsigned)(header.payload_length - written));
                    stats_add(&s_stats.speaker_errors, 1);
                } else {
                    stats_add(&s_stats.speaker_packets, 1);
                    stats_add(&s_stats.speaker_bytes, written);
                }
            }
            if (xStreamBufferBytesAvailable(s_speaker_buffer) >=
                    CONFIG_GLASSES_SPEAKER_PREBUFFER_MS * 48 ||
                (header.flags & GLASSES_FLAG_END)) {
                xEventGroupSetBits(s_speaker_events, SPEAKER_REQUEST_BIT);
            }
            if (header.flags & GLASSES_FLAG_END) {
                xEventGroupSetBits(s_speaker_events, SPEAKER_END_BIT);
            }
        }
        close(sock);
        xEventGroupSetBits(s_speaker_events, SPEAKER_REQUEST_BIT | SPEAKER_END_BIT);
        retry_delay();
    }
}

static void speaker_playback_task(void *argument)
{
    (void)argument;
    int16_t *mono = malloc(SPEAKER_MONO_BYTES);
    int16_t *stereo = malloc(SPEAKER_STEREO_BYTES);
    if (!mono || !stereo) {
        ESP_LOGE(TAG, "speaker playback buffers could not be allocated");
        free(mono);
        free(stereo);
        vTaskDelete(NULL);
        return;
    }
    while (true) {
        xEventGroupWaitBits(s_speaker_events, SPEAKER_REQUEST_BIT,
                            pdFALSE, pdFALSE, portMAX_DELAY);
        if (xSemaphoreTake(s_audio_mutex, portMAX_DELAY) != pdTRUE) {
            continue;
        }
        int result = glasses_board_speaker_open(24000, 16, 2,
                                                 CONFIG_GLASSES_SPEAKER_VOLUME);
        if (result != ESP_CODEC_DEV_OK) {
            ESP_LOGE(TAG, "speaker open failed: %d", result);
            stats_add(&s_stats.speaker_errors, 1);
            xStreamBufferReset(s_speaker_buffer);
            xEventGroupClearBits(s_speaker_events, SPEAKER_REQUEST_BIT | SPEAKER_END_BIT);
            xSemaphoreGive(s_audio_mutex);
            continue;
        }
        (void)glasses_board_speaker_set_mute(false);
        (void)glasses_board_speaker_pa_enable(true);
        portENTER_CRITICAL(&s_stats_lock);
        s_stats.speaker_active = true;
        portEXIT_CRITICAL(&s_stats_lock);

        int empty_waits = 0;
        while (true) {
            size_t received = xStreamBufferReceive(s_speaker_buffer, mono,
                                                   SPEAKER_MONO_BYTES,
                                                   pdMS_TO_TICKS(100));
            if (received > 0) {
                size_t samples = received / sizeof(int16_t);
                for (size_t index = 0; index < samples; ++index) {
                    stereo[index * 2] = mono[index];
                    stereo[index * 2 + 1] = mono[index];
                }
                result = glasses_board_speaker_write(stereo,
                                                      samples * 2 * sizeof(int16_t));
                if (result != ESP_CODEC_DEV_OK) {
                    ESP_LOGE(TAG, "speaker write failed: %d", result);
                    stats_add(&s_stats.speaker_errors, 1);
                    break;
                }
                empty_waits = 0;
            } else {
                empty_waits++;
            }
            EventBits_t bits = xEventGroupGetBits(s_speaker_events);
            if ((bits & SPEAKER_END_BIT) &&
                xStreamBufferBytesAvailable(s_speaker_buffer) == 0) {
                break;
            }
            if (empty_waits >= 3) {
                ESP_LOGW(TAG, "speaker buffer underflow; stopping playback");
                stats_add(&s_stats.speaker_errors, 1);
                break;
            }
        }
        (void)glasses_board_speaker_set_mute(true);
        (void)glasses_board_speaker_close();
        portENTER_CRITICAL(&s_stats_lock);
        s_stats.speaker_active = false;
        portEXIT_CRITICAL(&s_stats_lock);
        xStreamBufferReset(s_speaker_buffer);
        xEventGroupClearBits(s_speaker_events, SPEAKER_REQUEST_BIT | SPEAKER_END_BIT);
        xSemaphoreGive(s_audio_mutex);
    }
}

esp_err_t glasses_transport_start(void)
{
    uint8_t mac[6];
    ESP_RETURN_ON_ERROR(esp_read_mac(mac, ESP_MAC_WIFI_STA), TAG, "read MAC failed");
    snprintf(s_device_id, sizeof(s_device_id), "esp32s3-%02x%02x%02x%02x%02x%02x",
             mac[0], mac[1], mac[2], mac[3], mac[4], mac[5]);

    s_audio_mutex = xSemaphoreCreateMutex();
    s_speaker_events = xEventGroupCreate();
    s_speaker_buffer = xStreamBufferCreate(CONFIG_GLASSES_SPEAKER_BUFFER_BYTES, 1);
    if (!s_audio_mutex || !s_speaker_events || !s_speaker_buffer) {
        return ESP_ERR_NO_MEM;
    }

    if (xTaskCreate(control_task, "glasses_ctrl", 6144, NULL, 8, NULL) != pdPASS ||
        xTaskCreate(video_task, "glasses_video", 6144, NULL, 7, NULL) != pdPASS ||
        xTaskCreate(microphone_task, "glasses_mic", 6144, NULL, 8, NULL) != pdPASS ||
        xTaskCreate(speaker_receive_task, "glasses_spk_rx", 5120, NULL, 9, NULL) != pdPASS ||
        xTaskCreate(speaker_playback_task, "glasses_spk_play", 5120, NULL, 10, NULL) != pdPASS) {
        return ESP_ERR_NO_MEM;
    }
    ESP_LOGI(TAG, "transport started for %s; X5 gateway ports %d/%d/%d/%d",
             s_device_id, CONFIG_GLASSES_CONTROL_PORT, CONFIG_GLASSES_VIDEO_PORT,
             CONFIG_GLASSES_MICROPHONE_PORT, CONFIG_GLASSES_SPEAKER_PORT);
    return ESP_OK;
}

#else

esp_err_t glasses_transport_start(void)
{
    return ESP_ERR_NOT_SUPPORTED;
}

#endif
