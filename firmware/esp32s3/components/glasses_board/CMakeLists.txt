idf_component_register(
    SRCS "glasses_board.c"
    INCLUDE_DIRS "include"
    REQUIRES
        esp_codec_dev
        button
        custom_io_expander_ch32v003
        esp_io_expander
        esp_driver_gpio
        esp_driver_i2c
        esp_driver_i2s
)
