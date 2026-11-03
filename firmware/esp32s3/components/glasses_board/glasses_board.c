#include "glasses_board.h"

#include <string.h>

#include "button_gpio.h"
#include "custom_io_expander_ch32v003.h"
#include "driver/i2s_std.h"
#include "esp_check.h"
#include "esp_codec_dev_defaults.h"
#include "esp_io_expander.h"
#include "esp_log.h"
#include "freertos/FreeRTOS.h"
#include "freertos/task.h"
#include "iot_button.h"

#define GLASSES_IO_EXPANDER_SPEAKER_PA IO_EXPANDER_PIN_NUM_4
#define GLASSES_IO_EXPANDER_BAT_EN IO_EXPANDER_PIN_NUM_5

static const char *TAG = "glasses_board";

static i2c_master_bus_handle_t s_i2c_bus;
static esp_io_expander_handle_t s_io_expander;
static i2s_chan_handle_t s_i2s_tx;
static i2s_chan_handle_t s_i2s_rx;
static const audio_codec_data_if_t *s_audio_data_if;
static esp_codec_dev_handle_t s_microphone;
static esp_codec_dev_handle_t s_speaker;
static button_handle_t s_buttons[GLASSES_BUTTON_ID_COUNT];
static glasses_board_status_t s_status;

static void button_event_cb(void *button_handle, void *user_data)
{
    const glasses_button_id_t id = (glasses_button_id_t)(uintptr_t)user_data;
    const button_event_t event = iot_button_get_event(button_handle);
    ESP_LOGI(TAG, "button=%s event=%s",
             id == GLASSES_BUTTON_ID_BOOT ? "BOOT" : "PWR",
             iot_button_get_event_str(event));
}

static esp_err_t init_i2c(void)
{
    if (s_i2c_bus) {
        return ESP_OK;
    }

    const i2c_master_bus_config_t config = {
        .i2c_port = GLASSES_I2C_PORT,
        .sda_io_num = GLASSES_I2C_SDA,
        .scl_io_num = GLASSES_I2C_SCL,
        .clk_source = I2C_CLK_SRC_DEFAULT,
        .glitch_ignore_cnt = 7,
        .flags.enable_internal_pullup = true,
    };
    ESP_RETURN_ON_ERROR(i2c_new_master_bus(&config, &s_i2c_bus), TAG,
                        "shared I2C initialization failed");
    s_status.i2c_ready = true;
    return ESP_OK;
}

static esp_err_t init_io_expander(void)
{
    if (s_io_expander) {
        return ESP_OK;
    }

    ESP_RETURN_ON_ERROR(custom_io_expander_new_i2c_ch32v003(
                            s_i2c_bus, CUSTOM_IO_EXPANDER_I2C_CH32V003_ADDRESS,
                            &s_io_expander),
                        TAG, "CH32V003 initialization failed");
    /* Keep the battery path on when PWR is released. EXIO5 belongs to the
     * CH32 expander, not ESP GPIO5. Do this before camera/audio/network work. */
    ESP_RETURN_ON_ERROR(esp_io_expander_set_dir(
                            s_io_expander, GLASSES_IO_EXPANDER_BAT_EN,
                            IO_EXPANDER_OUTPUT),
                        TAG, "BAT_EN direction setup failed");
    ESP_RETURN_ON_ERROR(esp_io_expander_set_level(
                            s_io_expander, GLASSES_IO_EXPANDER_BAT_EN, 1),
                        TAG, "BAT_EN power-hold request failed");
    s_status.battery_hold_requested = true;
    ESP_LOGI(TAG, "battery power hold requested: CH32 EXIO5/BAT_EN=1");
    ESP_RETURN_ON_ERROR(esp_io_expander_set_dir(
                            s_io_expander, GLASSES_IO_EXPANDER_SPEAKER_PA,
                            IO_EXPANDER_OUTPUT),
                        TAG, "speaker PA direction setup failed");
    ESP_RETURN_ON_ERROR(glasses_board_speaker_pa_enable(false), TAG,
                        "speaker PA safe-state setup failed");
    s_status.io_expander_ready = true;
    return ESP_OK;
}

static esp_err_t init_one_button(glasses_button_id_t id, gpio_num_t gpio_num)
{
    const button_config_t button_config = {0};
    const button_gpio_config_t gpio_config = {
        .gpio_num = gpio_num,
        .active_level = 0,
        .enable_power_save = false,
        .disable_pull = false,
    };

    ESP_RETURN_ON_ERROR(iot_button_new_gpio_device(
                            &button_config, &gpio_config, &s_buttons[id]),
                        TAG, "button GPIO%d initialization failed", gpio_num);
    ESP_RETURN_ON_ERROR(iot_button_register_cb(
                            s_buttons[id], BUTTON_PRESS_DOWN, NULL,
                            button_event_cb, (void *)(uintptr_t)id),
                        TAG, "button GPIO%d press callback failed", gpio_num);
    ESP_RETURN_ON_ERROR(iot_button_register_cb(
                            s_buttons[id], BUTTON_LONG_PRESS_START, NULL,
                            button_event_cb, (void *)(uintptr_t)id),
                        TAG, "button GPIO%d long-press callback failed", gpio_num);
    return ESP_OK;
}

static esp_err_t init_buttons(void)
{
    if (s_status.buttons_ready) {
        return ESP_OK;
    }
    ESP_RETURN_ON_ERROR(init_one_button(GLASSES_BUTTON_ID_BOOT,
                                        GLASSES_BUTTON_BOOT),
                        TAG, "BOOT button initialization failed");
    ESP_RETURN_ON_ERROR(init_one_button(GLASSES_BUTTON_ID_PWR,
                                        GLASSES_BUTTON_PWR),
                        TAG, "PWR button initialization failed");
    s_status.buttons_ready = true;
    return ESP_OK;
}

static esp_err_t init_i2s(void)
{
    if (s_audio_data_if) {
        return ESP_OK;
    }

    i2s_chan_config_t channel_config =
        I2S_CHANNEL_DEFAULT_CONFIG(I2S_NUM_0, I2S_ROLE_MASTER);
    channel_config.auto_clear = true;
    ESP_RETURN_ON_ERROR(i2s_new_channel(&channel_config, &s_i2s_tx, &s_i2s_rx),
                        TAG, "I2S channel creation failed");

    const i2s_std_config_t std_config = {
        .clk_cfg = I2S_STD_CLK_DEFAULT_CONFIG(16000),
        .slot_cfg = I2S_STD_PHILIPS_SLOT_DEFAULT_CONFIG(
            I2S_DATA_BIT_WIDTH_16BIT, I2S_SLOT_MODE_STEREO),
        .gpio_cfg = {
            .mclk = GLASSES_I2S_MCLK,
            .bclk = GLASSES_I2S_BCLK,
            .ws = GLASSES_I2S_WS,
            .dout = GLASSES_I2S_DOUT,
            .din = GLASSES_I2S_DIN,
            .invert_flags = {
                .mclk_inv = false,
                .bclk_inv = false,
                .ws_inv = false,
            },
        },
    };
    ESP_RETURN_ON_ERROR(i2s_channel_init_std_mode(s_i2s_tx, &std_config), TAG,
                        "I2S TX initialization failed");
    ESP_RETURN_ON_ERROR(i2s_channel_init_std_mode(s_i2s_rx, &std_config), TAG,
                        "I2S RX initialization failed");
    ESP_RETURN_ON_ERROR(i2s_channel_enable(s_i2s_tx), TAG,
                        "I2S TX clock startup failed");
    ESP_RETURN_ON_ERROR(i2s_channel_enable(s_i2s_rx), TAG,
                        "I2S RX startup failed");

    audio_codec_i2s_cfg_t codec_i2s_config = {
        .port = I2S_NUM_0,
        .rx_handle = s_i2s_rx,
        .tx_handle = s_i2s_tx,
    };
    s_audio_data_if = audio_codec_new_i2s_data(&codec_i2s_config);
    ESP_RETURN_ON_FALSE(s_audio_data_if, ESP_ERR_NO_MEM, TAG,
                        "audio I2S data interface creation failed");
    return ESP_OK;
}

static esp_err_t init_speaker_codec(void)
{
    if (s_speaker) {
        return ESP_OK;
    }

    audio_codec_i2c_cfg_t i2c_config = {
        .port = GLASSES_I2C_PORT,
        .addr = ES8311_CODEC_DEFAULT_ADDR,
        .bus_handle = s_i2c_bus,
    };
    const audio_codec_ctrl_if_t *ctrl_if =
        audio_codec_new_i2c_ctrl(&i2c_config);
    ESP_RETURN_ON_FALSE(ctrl_if, ESP_ERR_NO_MEM, TAG,
                        "ES8311 I2C control interface creation failed");

    const audio_codec_gpio_if_t *gpio_if = audio_codec_new_gpio();
    ESP_RETURN_ON_FALSE(gpio_if, ESP_ERR_NO_MEM, TAG,
                        "codec GPIO interface creation failed");

    esp_codec_dev_hw_gain_t hardware_gain = {
        .pa_voltage = 5.0,
        .codec_dac_voltage = 3.3,
    };
    es8311_codec_cfg_t codec_config = {
        .ctrl_if = ctrl_if,
        .gpio_if = gpio_if,
        .codec_mode = ESP_CODEC_DEV_WORK_MODE_DAC,
        .pa_pin = GPIO_NUM_NC,
        .pa_reverted = false,
        .master_mode = false,
        .use_mclk = true,
        .digital_mic = false,
        .invert_mclk = false,
        .invert_sclk = false,
        .hw_gain = hardware_gain,
    };
    const audio_codec_if_t *codec_if = es8311_codec_new(&codec_config);
    ESP_RETURN_ON_FALSE(codec_if, ESP_ERR_NO_MEM, TAG,
                        "ES8311 codec interface creation failed");

    esp_codec_dev_cfg_t device_config = {
        .dev_type = ESP_CODEC_DEV_TYPE_OUT,
        .codec_if = codec_if,
        .data_if = s_audio_data_if,
    };
    s_speaker = esp_codec_dev_new(&device_config);
    ESP_RETURN_ON_FALSE(s_speaker, ESP_ERR_NO_MEM, TAG,
                        "ES8311 device creation failed");
    return ESP_OK;
}

static esp_err_t init_microphone_codec(void)
{
    if (s_microphone) {
        return ESP_OK;
    }

    audio_codec_i2c_cfg_t i2c_config = {
        .port = GLASSES_I2C_PORT,
        .addr = ES7210_CODEC_DEFAULT_ADDR,
        .bus_handle = s_i2c_bus,
    };
    const audio_codec_ctrl_if_t *ctrl_if =
        audio_codec_new_i2c_ctrl(&i2c_config);
    ESP_RETURN_ON_FALSE(ctrl_if, ESP_ERR_NO_MEM, TAG,
                        "ES7210 I2C control interface creation failed");

    es7210_codec_cfg_t codec_config = {
        .ctrl_if = ctrl_if,
        .master_mode = false,
        .mic_selected = ES7210_SEL_MIC1 | ES7210_SEL_MIC2,
    };
    const audio_codec_if_t *codec_if = es7210_codec_new(&codec_config);
    ESP_RETURN_ON_FALSE(codec_if, ESP_ERR_NO_MEM, TAG,
                        "ES7210 codec interface creation failed");

    esp_codec_dev_cfg_t device_config = {
        .dev_type = ESP_CODEC_DEV_TYPE_IN,
        .codec_if = codec_if,
        .data_if = s_audio_data_if,
    };
    s_microphone = esp_codec_dev_new(&device_config);
    ESP_RETURN_ON_FALSE(s_microphone, ESP_ERR_NO_MEM, TAG,
                        "ES7210 device creation failed");
    return ESP_OK;
}

esp_err_t glasses_board_init(void)
{
    ESP_RETURN_ON_ERROR(init_i2c(), TAG, "I2C setup failed");
    ESP_RETURN_ON_ERROR(init_io_expander(), TAG, "IO expander setup failed");
    ESP_RETURN_ON_ERROR(init_buttons(), TAG, "button setup failed");
    ESP_LOGI(TAG, "board base ready: shared I2C, CH32V003, BOOT/PWR buttons");
    return ESP_OK;
}

i2c_master_bus_handle_t glasses_board_i2c_handle(void)
{
    return s_i2c_bus;
}

button_handle_t glasses_board_button(glasses_button_id_t id)
{
    return id < GLASSES_BUTTON_ID_COUNT ? s_buttons[id] : NULL;
}

esp_err_t glasses_board_audio_init(void)
{
    ESP_RETURN_ON_ERROR(init_i2c(), TAG, "I2C setup failed");
    ESP_RETURN_ON_ERROR(init_io_expander(), TAG, "IO expander setup failed");
    ESP_RETURN_ON_ERROR(init_i2s(), TAG, "I2S setup failed");
    ESP_RETURN_ON_ERROR(init_speaker_codec(), TAG, "speaker codec setup failed");
    ESP_RETURN_ON_ERROR(init_microphone_codec(), TAG, "microphone codec setup failed");
    s_status.audio_ready = true;
    ESP_LOGI(TAG, "audio ready: ES7210 microphone, ES8311 speaker, PA disabled");
    return ESP_OK;
}

int glasses_board_microphone_open(uint32_t sample_rate, uint8_t bits_per_sample,
                                  uint8_t channels, float gain_db)
{
    if (!s_microphone) {
        return ESP_CODEC_DEV_WRONG_STATE;
    }
    esp_codec_dev_sample_info_t format = {
        .sample_rate = sample_rate,
        .channel = channels,
        .bits_per_sample = bits_per_sample,
        .mclk_multiple = 256,
    };
    int ret = esp_codec_dev_open(s_microphone, &format);
    if (ret == ESP_CODEC_DEV_OK) {
        ret = esp_codec_dev_set_in_gain(s_microphone, gain_db);
    }
    return ret;
}

int glasses_board_microphone_read(void *data, int length)
{
    return s_microphone ? esp_codec_dev_read(s_microphone, data, length)
                        : ESP_CODEC_DEV_WRONG_STATE;
}

int glasses_board_microphone_close(void)
{
    return s_microphone ? esp_codec_dev_close(s_microphone)
                        : ESP_CODEC_DEV_WRONG_STATE;
}

int glasses_board_speaker_open(uint32_t sample_rate, uint8_t bits_per_sample,
                               uint8_t channels, uint8_t volume)
{
    if (!s_speaker) {
        return ESP_CODEC_DEV_WRONG_STATE;
    }
    esp_codec_dev_sample_info_t format = {
        .sample_rate = sample_rate,
        .channel = channels,
        .bits_per_sample = bits_per_sample,
        .mclk_multiple = 256,
    };
    int ret = esp_codec_dev_open(s_speaker, &format);
    if (ret == ESP_CODEC_DEV_OK) {
        ret = esp_codec_dev_set_out_vol(s_speaker, volume);
    }
    return ret;
}

int glasses_board_speaker_write(void *data, int length)
{
    return s_speaker ? esp_codec_dev_write(s_speaker, data, length)
                     : ESP_CODEC_DEV_WRONG_STATE;
}

int glasses_board_speaker_close(void)
{
    (void)glasses_board_speaker_pa_enable(false);
    return s_speaker ? esp_codec_dev_close(s_speaker)
                     : ESP_CODEC_DEV_WRONG_STATE;
}

int glasses_board_speaker_set_mute(bool muted)
{
    return s_speaker ? esp_codec_dev_set_out_mute(s_speaker, muted)
                     : ESP_CODEC_DEV_WRONG_STATE;
}

esp_err_t glasses_board_speaker_pa_enable(bool enabled)
{
    ESP_RETURN_ON_FALSE(s_io_expander, ESP_ERR_INVALID_STATE, TAG,
                        "CH32V003 is not initialized");
    ESP_RETURN_ON_ERROR(esp_io_expander_set_level(
                            s_io_expander, GLASSES_IO_EXPANDER_SPEAKER_PA,
                            enabled ? 1 : 0),
                        TAG, "speaker PA state change failed");
    s_status.speaker_pa_enabled = enabled;
    return ESP_OK;
}

esp_err_t glasses_board_audio_self_test(void)
{
    ESP_RETURN_ON_FALSE(s_status.audio_ready, ESP_ERR_INVALID_STATE, TAG,
                        "audio is not initialized");

    int ret = glasses_board_microphone_open(16000, 32, 2, 42.0f);
    ESP_RETURN_ON_FALSE(ret == ESP_CODEC_DEV_OK, ESP_FAIL, TAG,
                        "ES7210 open failed: %d", ret);

    vTaskDelay(pdMS_TO_TICKS(100));

    uint8_t microphone_data[512];
    uint8_t minimum = UINT8_MAX;
    uint8_t maximum = 0;
    for (size_t attempt = 0; attempt < 8; ++attempt) {
        ret = glasses_board_microphone_read(microphone_data,
                                            sizeof(microphone_data));
        if (ret != ESP_CODEC_DEV_OK) {
            (void)glasses_board_microphone_close();
            ESP_LOGE(TAG, "ES7210 I2S read failed: %d", ret);
            return ESP_FAIL;
        }
        for (size_t i = 0; i < sizeof(microphone_data); ++i) {
            if (microphone_data[i] < minimum) {
                minimum = microphone_data[i];
            }
            if (microphone_data[i] > maximum) {
                maximum = microphone_data[i];
            }
        }
        if (minimum != maximum) {
            break;
        }
    }
    if (minimum == maximum) {
        (void)glasses_board_microphone_close();
        ESP_LOGE(TAG, "ES7210 I2S data is constant: 0x%02x", minimum);
        return ESP_FAIL;
    }

    ret = glasses_board_microphone_close();
    ESP_RETURN_ON_FALSE(ret == ESP_CODEC_DEV_OK, ESP_FAIL, TAG,
                        "ES7210 close failed: %d", ret);

    ret = glasses_board_speaker_open(24000, 16, 2, 60);
    ESP_RETURN_ON_FALSE(ret == ESP_CODEC_DEV_OK, ESP_FAIL, TAG,
                        "ES8311 open failed: %d", ret);
    ret = esp_codec_dev_set_out_mute(s_speaker, true);
    ESP_RETURN_ON_FALSE(ret == ESP_CODEC_DEV_OK, ESP_FAIL, TAG,
                        "ES8311 mute failed: %d", ret);
    ret = glasses_board_speaker_close();
    ESP_RETURN_ON_FALSE(ret == ESP_CODEC_DEV_OK, ESP_FAIL, TAG,
                        "ES8311 close failed: %d", ret);

    s_status.audio_self_test_ok = true;
    ESP_LOGI(TAG,
             "audio self-test passed: ES7210 I2S data range=0x%02x..0x%02x; "
             "ES8311 silent; PA disabled",
             minimum, maximum);
    return ESP_OK;
}

esp_codec_dev_handle_t glasses_board_microphone(void)
{
    return s_microphone;
}

esp_codec_dev_handle_t glasses_board_speaker(void)
{
    return s_speaker;
}

void glasses_board_get_status(glasses_board_status_t *status)
{
    if (status) {
        memcpy(status, &s_status, sizeof(*status));
    }
}
