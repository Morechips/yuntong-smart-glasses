#include "sdkconfig.h"
#if CONFIG_GLASSES_IMU_SYNC_ENABLE
#include "glasses_imu_stream.h"
#include "glasses_sync_core.h"
#include "glasses_board.h"
#include "esp_app_desc.h"
#include "esp_heap_caps.h"
#include "esp_random.h"
#include "esp_system.h"
#include "esp_timer.h"
#include "freertos/FreeRTOS.h"
#include "freertos/task.h"
#include <inttypes.h>
#include <stdarg.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

static char s_boot[33];
static i2c_master_dev_handle_t s_imu;
static TaskHandle_t s_task;
static portMUX_TYPE s_lock=portMUX_INITIALIZER_UNLOCKED;
static sync_ring_t s_ring;
static uint64_t s_ok, s_failed, s_skipped, s_overruns, s_duration_sum;
static uint32_t s_duration_max, s_hist[5], s_stack_min;
static int64_t s_last_end;
/* IDF converts ms with pdMS_TO_TICKS (floor). 8ms at 100Hz becomes
 * zero ticks and can abort an otherwise healthy transaction immediately. */
static int imu_timeout_ms(void)
{
    return ((CONFIG_GLASSES_IMU_TIMEOUT_MS+portTICK_PERIOD_MS-1)/portTICK_PERIOD_MS)*portTICK_PERIOD_MS;
}

void glasses_sync_identity_init(void)
{
    if (s_boot[0]) return;
    uint32_t random[4];
    esp_fill_random(random, sizeof(random));
    snprintf(s_boot, sizeof(s_boot), "%08" PRIx32 "%08" PRIx32 "%08" PRIx32 "%08" PRIx32,
             random[0],random[1],random[2],random[3]);
}
const char *glasses_sync_boot_id(void) { return s_boot; }

static void sample_task(void *arg)
{
    const int64_t period=1000000 / CONFIG_GLASSES_IMU_POLL_HZ;
    int64_t next=esp_timer_get_time();
    for (;;) {
        sync_sample_t sample={0};
        uint8_t reg=0x34, bytes[12]={0};
        sample.read_start_us=esp_timer_get_time();
        sample.error=i2c_master_transmit_receive(s_imu, &reg, 1, bytes, sizeof(bytes),
                                                imu_timeout_ms());
        sample.read_end_us=esp_timer_get_time();
        if (sample.error==ESP_OK) {
            for (int i=0;i<6;i++) sample.raw_motion[i]=(int16_t)((uint16_t)bytes[2*i] | ((uint16_t)bytes[2*i+1]<<8));
        }
        const uint32_t duration=(uint32_t)(sample.read_end_us-sample.read_start_us);
        unsigned bucket=duration<2000?0:duration<5000?1:duration<10000?2:duration<20000?3:4;
        uint32_t stack=uxTaskGetStackHighWaterMark(NULL);
        portENTER_CRITICAL(&s_lock);
        sync_ring_push(&s_ring, sample);
        if (sample.error==ESP_OK) s_ok++; else s_failed++;
        s_duration_sum+=duration; s_hist[bucket]++;
        if (duration>s_duration_max) s_duration_max=duration;
        if (!s_stack_min || stack<s_stack_min) s_stack_min=stack;
        s_last_end=sample.read_end_us;
        portEXIT_CRITICAL(&s_lock);
        next+=period;
        int64_t now=esp_timer_get_time();
        uint64_t skipped=0;
        if (now>=next) { skipped=(now-next)/period+1; next+=skipped*period; }
        if (sample.error!=ESP_OK && next<now+100000) {
            skipped+=(now+100000-next+period-1)/period;
            next=now+100000;
        }
        portENTER_CRITICAL(&s_lock);
        s_skipped+=skipped; s_overruns+=duration>period;
        portEXIT_CRITICAL(&s_lock);
        /* No catch-up bursts; failures back off, network never owns this task. */
        const int64_t wait=next-esp_timer_get_time();
        if (wait>0) vTaskDelay((TickType_t)((wait+portTICK_PERIOD_MS*1000-1)/(portTICK_PERIOD_MS*1000)));
        else taskYIELD();
    }
}

esp_err_t glasses_imu_stream_start(void)
{
    if (s_task) return ESP_OK;
    const i2c_device_config_t config={.dev_addr_length=I2C_ADDR_BIT_LEN_7,
        .device_address=0x50, .scl_speed_hz=100000, .scl_wait_us=20000};
    esp_err_t result=i2c_master_bus_add_device(glasses_board_i2c_handle(), &config, &s_imu);
    if (result!=ESP_OK) return result;
    if (xTaskCreate(sample_task,"jy61p_sample",4096,NULL,4,&s_task)!=pdPASS) {
        i2c_master_bus_rm_device(s_imu); s_imu=NULL;
        return ESP_ERR_NO_MEM;
    }
    return ESP_OK;
}

static bool append(char *out, size_t cap, size_t *used, const char *format, ...)
{
    if (*used>=cap) return false;
    va_list args; va_start(args,format);
    int n=vsnprintf(out+*used,cap-*used,format,args); va_end(args);
    if (n<0 || (size_t)n>=cap-*used) return false;
    *used+=(size_t)n; return true;
}

int glasses_imu_state_json(char *out, size_t capacity)
{
    uint64_t ok,failed,skipped,overruns,sum,latest,overwritten; size_t count;
    uint32_t max,hist[5],stack; int64_t last;
    portENTER_CRITICAL(&s_lock);
    ok=s_ok;failed=s_failed;skipped=s_skipped;overruns=s_overruns;sum=s_duration_sum;
    max=s_duration_max; memcpy(hist,s_hist,sizeof(hist));stack=s_stack_min;last=s_last_end;
    latest=s_ring.latest_seq;overwritten=s_ring.overwritten_total;count=s_ring.count;
    portEXIT_CRITICAL(&s_lock);
    const esp_app_desc_t *app=esp_app_get_description();
    char sha[65];
    for (int i=0;i<32;i++) snprintf(sha+2*i,3,"%02x",app->app_elf_sha256[i]);
    int n=snprintf(out,capacity,
        "{\"schema\":\"esp-imu-v1\",\"boot_id\":\"%s\",\"clock\":\"esp-monotonic-us\","
        "\"firmware\":\"imu-sync-v1\",\"app_elf_sha256\":\"%s\",\"poll_hz_target\":%d,"
        "\"timeout_ms\":%d,\"failure_backoff_ms\":100,\"running\":%s,\"capacity\":100,\"buffered\":%u,"
        "\"latest_seq\":%" PRIu64 ",\"overwritten_total\":%" PRIu64 ",\"success\":%" PRIu64 ",\"failed\":%" PRIu64 ","
        "\"skipped_periods\":%" PRIu64 ",\"read_overruns\":%" PRIu64 ",\"duration_sum_us\":%" PRIu64 ","
        "\"duration_max_us\":%" PRIu32 ",\"duration_buckets_lt_2_5_10_20_ms_else\":[%u,%u,%u,%u,%u],"
        "\"last_read_end_us\":%" PRId64 ",\"sampler_stack_min_bytes\":%u,\"free_heap\":%u,\"minimum_free_heap\":%u,"
        "\"range_config_source\":\"not-verified-no-register-writes\",\"accel_range_g\":null,\"gyro_range_dps\":null,"
        "\"accel_g_per_lsb\":null,\"gyro_dps_per_lsb\":null,\"effective_sensor_update_hz\":null,"
        "\"sample_time_verified\":false,\"atomic_sample_verified\":false}",
        s_boot,sha,CONFIG_GLASSES_IMU_POLL_HZ,imu_timeout_ms(),s_task?"true":"false",(unsigned)count,
        latest,overwritten,ok,failed,skipped,overruns,sum,max,(unsigned)hist[0],(unsigned)hist[1],
        (unsigned)hist[2],(unsigned)hist[3],(unsigned)hist[4],last,(unsigned)stack,
        (unsigned)esp_get_free_heap_size(),(unsigned)esp_get_minimum_free_heap_size());
    return n<0 || (size_t)n>=capacity ? -1:n;
}

static bool sample_json(char *out, size_t cap, size_t *used, const sync_sample_t *s)
{
    if (!append(out,cap,used,"{\"seq\":%" PRIu64 ",\"read_start_us\":%" PRId64 ",\"read_end_us\":%" PRId64
        ",\"ok\":%s,\"error\":%" PRId32 ",\"sample_time_verified\":false,\"atomic_sample_verified\":false,\"raw_motion\":",
        s->seq,s->read_start_us,s->read_end_us,s->error==ESP_OK?"true":"false",s->error)) return false;
    if (s->error==ESP_OK) {
        if (!append(out,cap,used,"[%d,%d,%d,%d,%d,%d]",s->raw_motion[0],s->raw_motion[1],s->raw_motion[2],
            s->raw_motion[3],s->raw_motion[4],s->raw_motion[5])) return false;
    } else if (!append(out,cap,used,"null")) return false;
    return append(out,cap,used,"}");
}

esp_err_t glasses_imu_batch_handler(httpd_req_t *req)
{
    uint64_t after=0,limit=16;
    const size_t length=httpd_req_get_url_query_len(req);
    if (length) {
        char query[128];
        if (length>=sizeof(query) || httpd_req_get_url_query_str(req,query,sizeof(query))!=ESP_OK)
            return httpd_resp_send_err(req,HTTPD_400_BAD_REQUEST,"Query too long");
        bool seen_after=false,seen_limit=false;
        char *save=NULL;
        for (char *part=strtok_r(query,"&",&save);part;part=strtok_r(NULL,"&",&save)) {
            char *eq=strchr(part,'='); uint64_t value;
            if (!eq) return httpd_resp_send_err(req,HTTPD_400_BAD_REQUEST,"Expected key=value");
            *eq++=0;
            if (!sync_parse_uint(eq,&value)) return httpd_resp_send_err(req,HTTPD_400_BAD_REQUEST,"Invalid integer");
            if (!strcmp(part,"after_seq") && !seen_after) { after=value;seen_after=true; }
            else if (!strcmp(part,"limit") && !seen_limit) { limit=value;seen_limit=true; }
            else return httpd_resp_send_err(req,HTTPD_400_BAD_REQUEST,"Unknown or duplicate key");
        }
    }
    if (!limit || limit>SYNC_BATCH_MAX) return httpd_resp_send_err(req,HTTPD_400_BAD_REQUEST,"limit=1..32 required");
    sync_sample_t *samples=calloc(SYNC_BATCH_MAX,sizeof(*samples));
    char *text=malloc(16384);
    if (!samples || !text) { free(samples);free(text);return httpd_resp_send_err(req,HTTPD_500_INTERNAL_SERVER_ERROR,"No memory"); }
    portENTER_CRITICAL(&s_lock);
    sync_batch_t batch=sync_ring_copy(&s_ring,after,(size_t)limit,samples);
    portEXIT_CRITICAL(&s_lock);
    size_t used=0;
    bool valid=append(text,16384,&used,
        "{\"schema\":\"esp-imu-v1\",\"boot_id\":\"%s\",\"clock\":\"esp-monotonic-us\","
        "\"oldest_seq\":%" PRIu64 ",\"latest_seq\":%" PRIu64 ",\"next_after_seq\":%" PRIu64 ","
        "\"cursor_overrun\":%s,\"cursor_ahead\":%s,\"overwritten_total\":%" PRIu64 ",\"samples\":[",
        s_boot,batch.oldest_seq,batch.latest_seq,batch.next_after_seq,
        batch.cursor_overrun?"true":"false",batch.cursor_ahead?"true":"false",batch.overwritten_total);
    for (size_t i=0;valid && i<batch.count;i++) {
        if (i) valid=append(text,16384,&used,",");
        if (valid) valid=sample_json(text,16384,&used,&samples[i]);
    }
    valid=valid && append(text,16384,&used,"]}");
    httpd_resp_set_type(req,"application/json");
    httpd_resp_set_hdr(req,"Cache-Control","no-store");
    esp_err_t result=valid?httpd_resp_send(req,text,used):httpd_resp_send_err(req,HTTPD_500_INTERNAL_SERVER_ERROR,"JSON overflow");
    free(text);free(samples);return result;
}

esp_err_t glasses_imu_cached_handler(httpd_req_t *req)
{
    sync_sample_t latest;
    portENTER_CRITICAL(&s_lock);
    uint64_t seq=s_ring.latest_seq;
    sync_batch_t batch=sync_ring_copy(&s_ring,seq?seq-1:0,1,&latest);
    portEXIT_CRITICAL(&s_lock);
    if (!batch.count) {
        httpd_resp_set_status(req,"503 Service Unavailable");
        return httpd_resp_sendstr(req,"Sampler not ready");
    }
    char text[768];size_t used=0;
    bool ok=append(text,sizeof(text),&used,"{\"cached\":true,\"boot_id\":\"%s\",\"age_us\":%" PRId64
        ",\"address\":80,\"angles_deg\":null,\"accel_g\":null,\"gyro_dps\":null,\"sample\":",s_boot,
        esp_timer_get_time()-latest.read_end_us);
    ok=ok && sample_json(text,sizeof(text),&used,&latest) && append(text,sizeof(text),&used,"}");
    httpd_resp_set_type(req,"application/json");
    httpd_resp_set_hdr(req,"Cache-Control","no-store");
    return ok?httpd_resp_send(req,text,used):ESP_FAIL;
}
#endif
