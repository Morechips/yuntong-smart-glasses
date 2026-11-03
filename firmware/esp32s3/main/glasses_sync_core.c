#include "glasses_sync_core.h"
#include <inttypes.h>
#include <stdio.h>
#include <string.h>

void sync_ring_push(sync_ring_t *ring, sync_sample_t sample)
{
    sample.seq = ++ring->latest_seq;
    ring->samples[(sample.seq - 1) % SYNC_RING_CAPACITY] = sample;
    if (ring->count < SYNC_RING_CAPACITY) ring->count++;
    else ring->overwritten_total++;
}

sync_batch_t sync_ring_copy(const sync_ring_t *ring, uint64_t after, size_t limit,
                            sync_sample_t *out)
{
    sync_batch_t batch = {.latest_seq=ring->latest_seq, .next_after_seq=after,
                         .overwritten_total=ring->overwritten_total};
    if (limit > SYNC_BATCH_MAX) limit = SYNC_BATCH_MAX;
    if (!ring->count) { batch.cursor_ahead = after > 0; return batch; }
    batch.oldest_seq = ring->latest_seq - ring->count + 1;
    batch.cursor_ahead = after > ring->latest_seq;
    batch.cursor_overrun = after > 0 && after < batch.oldest_seq - 1;
    if (batch.cursor_ahead || after == ring->latest_seq) return batch;
    uint64_t start = after < batch.oldest_seq ? batch.oldest_seq : after + 1;
    for (uint64_t seq=start; seq<=ring->latest_seq && batch.count<limit; seq++) {
        out[batch.count++] = ring->samples[(seq - 1) % SYNC_RING_CAPACITY];
        batch.next_after_seq = seq;
    }
    return batch;
}

bool sync_parse_uint(const char *text, uint64_t *out)
{
    if (!text || !*text || strlen(text)>20) return false;
    uint64_t value=0;
    for (; *text; text++) {
        if (*text<'0' || *text>'9') return false;
        const unsigned digit=*text-'0';
        if (value > (UINT64_MAX-digit)/10) return false;
        value=value*10+digit;
    }
    *out=value;
    return true;
}

int sync_frame_header(char *out, size_t capacity, size_t bytes, const char *boot,
                      uint32_t stream, uint64_t seq, int64_t source, const char *status)
{
    int n=snprintf(out, capacity,
        "Content-Type: image/jpeg\r\nContent-Length: %u\r\n"
        "X-Device-Boot-Id: %s\r\nX-Stream-Id: %" PRIu32 "\r\n"
        "X-Frame-Seq: %" PRIu64 "\r\nX-Frame-Source-Us: %" PRId64 "\r\n"
        "X-Frame-Time-Kind: first-dma\r\nX-Frame-Time-Status: %s\r\n\r\n",
        (unsigned)bytes, boot, stream, seq, source, status);
    return n < 0 || (size_t)n >= capacity ? -1 : n;
}
