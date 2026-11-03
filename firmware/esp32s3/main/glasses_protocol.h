#pragma once

#include <stddef.h>
#include <stdint.h>

#include "esp_err.h"

#ifdef __cplusplus
extern "C" {
#endif

#define GLASSES_PROTOCOL_MAGIC 0x474c5331u
#define GLASSES_PROTOCOL_VERSION 1u
#define GLASSES_PROTOCOL_HEADER_SIZE 32u
#define GLASSES_PROTOCOL_MAX_PAYLOAD (2u * 1024u * 1024u)

#define GLASSES_FLAG_CRC32 (1u << 0)
#define GLASSES_FLAG_END (1u << 1)
#define GLASSES_FLAG_DISCONTINUITY (1u << 2)

typedef enum {
    GLASSES_MSG_HELLO = 0x0001,
    GLASSES_MSG_HEARTBEAT = 0x0002,
    GLASSES_MSG_STATUS = 0x0003,
    GLASSES_MSG_CONTROL = 0x0004,
    GLASSES_MSG_VIDEO_JPEG = 0x0100,
    GLASSES_MSG_MICROPHONE_PCM = 0x0200,
    GLASSES_MSG_SPEAKER_PCM = 0x0300,
    GLASSES_MSG_ACK = 0x0400,
    GLASSES_MSG_ERROR = 0x0401,
} glasses_message_type_t;

typedef struct {
    glasses_message_type_t type;
    uint32_t flags;
    uint32_t sequence;
    uint64_t timestamp_us;
    uint32_t payload_length;
    uint32_t payload_crc32;
} glasses_packet_header_t;

uint32_t glasses_payload_crc32(const void *payload, size_t length);
void glasses_protocol_encode_header(uint8_t output[GLASSES_PROTOCOL_HEADER_SIZE],
                                    const glasses_packet_header_t *header);
esp_err_t glasses_protocol_decode_header(const uint8_t input[GLASSES_PROTOCOL_HEADER_SIZE],
                                         glasses_packet_header_t *header);
int glasses_socket_send_all(int sock, const void *data, size_t length);
int glasses_socket_recv_all(int sock, void *data, size_t length);
int glasses_socket_send_packet(int sock, glasses_message_type_t type, uint32_t flags,
                               uint32_t sequence, uint64_t timestamp_us,
                               const void *payload, size_t payload_length);
int glasses_socket_recv_header(int sock, glasses_packet_header_t *header);

#ifdef __cplusplus
}
#endif
