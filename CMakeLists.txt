if(CONFIG_ZMK_SCREEN_SCAN_MACRO)
    zephyr_library()
    zephyr_library_sources(src/screen_scan_macro.c)
endif()
