adb shell "rm -rf /data/local/tmp/genie_qwen3_quality"
adb shell am force-stop com.qairt.qwen3htp
adb push E:\QualComm\ai-hub-compiles\qwen3_0_6b-geniex_qairt-w4a16-qualcomm_sa8775p_ctx512_v21 /data/local/tmp/genie_qwen3_quality
adb shell "chmod -R 777 /data/local/tmp/genie_qwen3_quality/*"
