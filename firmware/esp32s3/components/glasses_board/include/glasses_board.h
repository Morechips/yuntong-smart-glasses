#pragma once

#include <stdbool.h>
#include <stddef.h>
#include <stdint.h>

#include "button_types.h"
#include "driver/gpio.h"
#include "driver/i2c_master.h"
#include "esp_codec_dev.h"
#include "esp_err.h"

#ifdef __cplusplus
extern "C" {
#endif

#define GLASSES_I2C_PORT I2C_NUM_0
#define GLASSES_I2C_SCL GPIO_NUM_7
#define GLASSES_I2C_SDA GPIO_NUM_8

#define GLASSES_I2S_MCLK GPIO_NUM_10
#define GLASSES_I2S_BCLK GPIO_NUM_11
#define GLASSES_I2S_WS GPIO_NUM_12
#define GLASSES_I2S_DIN GPIO_NUM_13
#define GLASSES_I2S_DOUT GPIO_NUM_14

#define GLASSES_BUTTON_BOOT GPIO_NUM_0
#define GLASSES_BUTTON_PWR GPIO_NUM_15

#define GLASSES_CAMERA_XCLK GPIO_NUM_38
#define GLASSES_CAMERA_PCLK GPIO_NUM_41
#define GLASSES_CAMERA_VSYNC GPIO_NUM_17
#define GLASSES_CAMERA_HREF GPIO_NUM_18
#define GLASSES_CAMERA_D0 GPIO_NUM_45
#define GLASSES_CAMERA_D1 GPIO_NUM_47
#define GLASSES_CAMERA_D2 GPIO_NUM_48
#define GLASSES_CAMERA_D3 GPIO_NUM_46
#define GLASSES_CAMERA_D4 GPIO_NUM_42
#define GLASSES_CAMERA_D5 GPIO_NUM_40
#define GLASSES_CAMERA_D6 GPIO_NUM_39
#define GLASSES_CAMERA_D7 GPIO_NUM_21

typedef enum {
    GLASSES_BUTTON_ID_BOOT = 0,
    GLASSES_BUTTON_ID_PWR,
    GLASSES_BUTTON_ID_COUNT,
} glasses_button_id_t;

typedef struct {
    bool i2c_ready;
    bool io_expander_ready;
    /* Successful direction/high commands; not a measured battery rail. */
    bool battery_hold_requested;
    bool buttons_ready;
    bool audio_ready;
    bool audio_self_test_ok;
    bool speaker_pa_enabled;
} glasses_board_status_t;

esp_err_t glasses_board_init(void);
i2c_master_bus_handle_t glasses_board_i2c_handle(void);
button_handle_t glasses_board_button(glasses_button_id_t id);

esp_err_t glasses_board_audio_init(void);
esp_err_t glasses_board_audio_self_test(void);
esp_codec_dev_handle_t glasses_board_microphone(void);
esp_codec_dev_handle_t glasses_board_speaker(void);

int glasses_board_microphone_open(uint32_t sample_rate, uint8_t bits_per_sample,
                                  uint8_t channels, float gain_db);
int glasses_board_microphone_read(void *data, int length);
int glasses_board_microphone_close(void);

int glasses_board_speaker_open(uint32_t sample_rate, uint8_t bits_per_sample,
                               uint8_t channels, uint8_t volume);
int glasses_board_speaker_write(void *data, int length);
int glasses_board_speaker_close(void);
int glasses_board_speaker_set_mute(bool muted);
esp_err_t glasses_board_speaker_pa_enable(bool enabled);

void glasses_board_get_status(glasses_board_status_t *status);

#ifdef __cplusplus
}
#endif
