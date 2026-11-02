idf_component_register(SRCS "main.cpp" "settings.cpp" "board_session.cpp" "pipeline.cpp" "sercmd.cpp" "video.cpp" "ui.cpp"
                       INCLUDE_DIRS "."
                       EMBED_FILES "ui_assets.dat"
                       REQUIRES m5unified jpeg_decode_enhanced esp_driver_jpeg esp_driver_ppa esp_mm esp_lcd hal
                                nvs_flash esp_driver_usb_serial_jtag vfs mbedtls esp_timer esp_driver_tsens)
