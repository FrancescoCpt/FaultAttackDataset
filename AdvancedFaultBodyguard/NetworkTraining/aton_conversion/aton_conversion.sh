/Users/francescocaputo/STM32Cube/Repository/Packs/STMicroelectronics/X-CUBE-AI/10.2.0/Utilities/macarm/stedgeai generate --target stm32n6 --name network -m ../training_outputs/final_model_int8.tflite --st-neural-art n6-noextmem@user_neuralart.json

cp st_ai_output/network_ecblobs.h .
cp st_ai_output/network.c .
cp st_ai_output/network_atonbuf.AXISRAM5.raw ./network_data.AXISRAM5.bin

/Applications/STM32CubeIDE.app/Contents/Eclipse/plugins/com.st.stm32cube.ide.mcu.externaltools.gnu-tools-for-stm32.14.3.rel1.macos64_1.0.100.202602081740/tools/bin/arm-none-eabi-objcopy -I binary network_data.AXISRAM5.bin --change-addresses 0x342E0000 -O ihex ./network_data.hex

rm -r ./st_ai_output ./st_ai_ws