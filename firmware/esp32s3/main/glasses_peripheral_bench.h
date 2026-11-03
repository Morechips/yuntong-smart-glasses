#pragma once
#include "esp_err.h"

esp_err_t glasses_peripheral_bench_start(void);
esp_err_t glasses_motor_cycle_start(void);
/* Establish inactive motor levels before any Wi-Fi or peripheral wait. */
esp_err_t glasses_motor_outputs_off(void);
