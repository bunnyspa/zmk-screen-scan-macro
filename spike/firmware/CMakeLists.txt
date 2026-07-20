if(CONFIG_ZMK_SCREEN_SCAN_MACRO_SPIKE)
    zephyr_library()
    zephyr_library_sources(src/ssm_spike.c)
endif()
