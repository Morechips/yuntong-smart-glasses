#pragma once
#include <stdbool.h>
#include <string.h>

#define SPEAKER_VOLUME_DEFAULT 25
#define SPEAKER_VOLUME_MAX 70

/* Deliberately one field: reject typos/duplicates instead of silently ignoring. */
static inline bool speaker_volume_parse(const char *query, int *volume)
{
    if (!query || !*query) {
        *volume = SPEAKER_VOLUME_DEFAULT;
        return true;
    }
    if (strncmp(query, "volume=", 7) != 0) return false;
    const char *value = query + 7;
    const size_t count = strlen(value);
    if (count < 1 || count > 2) return false;
    int parsed = 0;
    for (size_t i = 0; i < count; ++i) {
        if (value[i] < '0' || value[i] > '9') return false;
        parsed = parsed * 10 + value[i] - '0';
    }
    if (parsed > SPEAKER_VOLUME_MAX) return false;
    *volume = parsed;
    return true;
}
