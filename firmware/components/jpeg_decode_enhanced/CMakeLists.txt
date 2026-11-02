idf_component_register(
    SRCS
        "src/jpeg_decode_enhanced.c"
    INCLUDE_DIRS "include"
    REQUIRES esp_driver_jpeg esp_driver_ppa
    PRIV_REQUIRES hal esp_mm esp_hw_support driver
)

# NOTE: depends on ESP-IDF internal headers (jpeg_private.h, private/*.h,
# esp_private/dma2d.h, hal/jpeg_ll.h). These live inside the esp_driver_jpeg
# and esp_hw_support components and are not exported as public include dirs,
# so we add them explicitly. jpeg_decode_enhanced.c carries an ESP_IDF_VERSION
# guard because the private struct layouts can change between IDF releases.
# [tab5_monitor J7] esp_hw_support/dma: dma2d_priv.h (who holds an RX channel) — included for IDF v5.5.4 only.
target_include_directories(${COMPONENT_LIB} PRIVATE
    "$ENV{IDF_PATH}/components/esp_driver_jpeg"
    "$ENV{IDF_PATH}/components/esp_driver_jpeg/private"
    "$ENV{IDF_PATH}/components/esp_hw_support/dma"
)
