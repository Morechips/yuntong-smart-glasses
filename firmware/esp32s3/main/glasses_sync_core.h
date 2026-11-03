#pragma once
/* Portable, allocation-free core. Caller serializes ring access. */
#include <stdbool.h>
#include <stddef.h>
#include <stdint.h>

#define SYNC_RING_CAPACITY 100
#define SYNC_BATCH_MAX 32
typedef struct {
    uint64_t seq;
    int64_t read_start_us, read_end_us;
    int32_t error;
    int16_t raw_motion[6];
} sync_sample_t;
typedef struct {
    sync_sample_t samples[SYNC_RING_CAPACITY];
    uint64_t latest_seq, overwritten_total;
    size_t count;
} sync_ring_t;
typedef struct {
    uint64_t oldest_seq, latest_seq, next_after_seq, overwritten_total;
    bool cursor_overrun, cursor_ahead;
    size_t count;
} sync_batch_t;

void sync_ring_push(sync_ring_t *ring, sync_sample_t sample);
sync_batch_t sync_ring_copy(const sync_ring_t *ring, uint64_t after, size_t limit,
                            sync_sample_t *out);
bool sync_parse_uint(const char *text, uint64_t *out);
int sync_frame_header(char *out, size_t capacity, size_t jpeg_bytes,
                      const char *boot_id, uint32_t stream_id, uint64_t seq,
                      int64_t source_us, const char *time_status);
