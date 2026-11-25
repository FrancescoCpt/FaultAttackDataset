#include "bodyguard_ml.h"
#include "stm32n6xx_hal.h"
#include <string.h>

static uint8_t ready;
static nn_preprocess_workspace_t preprocessing_workspace;
static int8_t quantized_input[NN_PREPROCESS_INPUT_FEATURES];

#if BODYGUARD_ML_BACKEND == BODYGUARD_ML_NPU
#include "network.h"
#include "network_atonbuf.h"
#include "ll_aton_runtime.h"
#include "ll_aton_rt_user_api.h"
#include "npu_cache.h"
LL_ATON_DECLARE_NAMED_NN_INSTANCE_AND_INTERFACE(Default);
static const LL_Buffer_InfoTypeDef *input_info;
static const LL_Buffer_InfoTypeDef *output_info;
_Static_assert(LL_ATON_DEFAULT_IN_1_SIZE_BYTES == NN_PREPROCESS_INPUT_FEATURES,
               "NPU input/preprocessing mismatch");
_Static_assert(LL_ATON_DEFAULT_OUT_1_SIZE_BYTES == 2, "Expected two classes");

static void clean_cpu_cache(void *buffer, uint32_t size)
{
    uintptr_t start = (uintptr_t)buffer & ~(uintptr_t)31;
    uintptr_t end = ((uintptr_t)buffer + size + 31) & ~(uintptr_t)31;
    SCB_CleanDCache_by_Addr((uint32_t *)start, (int32_t)(end - start));
    __DSB();
}
#else
#include "network_cpu.h"
#include "network_cpu_data.h"
static ai_handle cpu_network = AI_HANDLE_NULL;
AI_ALIGNED(32) static ai_u8 activations[AI_NETWORK_CPU_DATA_ACTIVATIONS_SIZE];
static ai_buffer *cpu_input;
static ai_buffer *cpu_output;
_Static_assert(AI_NETWORK_CPU_IN_1_SIZE_BYTES == NN_PREPROCESS_INPUT_FEATURES,
               "CPU input/preprocessing mismatch");
_Static_assert(AI_NETWORK_CPU_OUT_1_SIZE_BYTES == 2, "Expected two classes");
#endif

int32_t BodyguardML_Init(void)
{
    if (ready) { return BODYGUARD_ML_OK; }
#if BODYGUARD_ML_BACKEND == BODYGUARD_ML_NPU
    /* IC6 is already sourced from PLL1 by SystemClock_Config. AXISRAM5 is
     * reserved for the absolute addresses in the existing Neural-ART model. */
    __HAL_RCC_AXISRAM5_MEM_CLK_ENABLE();
    __HAL_RCC_AXISRAM5_MEM_CLK_SLEEP_ENABLE();
    __HAL_RCC_CACHEAXIRAM_MEM_CLK_ENABLE();
    __HAL_RCC_CACHEAXIRAM_MEM_CLK_SLEEP_ENABLE();
    __HAL_RCC_NPU_CLK_ENABLE();
    __HAL_RCC_NPU_CLK_SLEEP_ENABLE();
    __HAL_RCC_NPU_FORCE_RESET();
    __HAL_RCC_NPU_RELEASE_RESET();
    __HAL_RCC_RIFSC_CLK_ENABLE();
    /* STM32N657: NPU is RIMC master 1, RISC peripheral 106 (word 3, bit 10).
     * Same secure/privileged CID1 attributes as the ST NPU example, using
     * CMSIS registers because this Cube project does not include HAL RIF. */
    MODIFY_REG(RIFSC->RIMC_ATTRx[1],
               RIFSC_RIMC_ATTRx_MCID | RIFSC_RIMC_ATTRx_MSEC | RIFSC_RIMC_ATTRx_MPRIV,
               (1UL << RIFSC_RIMC_ATTRx_MCID_Pos) | RIFSC_RIMC_ATTRx_MSEC | RIFSC_RIMC_ATTRx_MPRIV);
    SET_BIT(RIFSC->RISC_SECCFGRx[3], 1UL << 10);
    SET_BIT(RIFSC->RISC_PRIVCFGRx[3], 1UL << 10);
    npu_cache_init();
    /* Internal SRAM model: bypass NPU cache. Generated SW/HW cache maintenance
     * still uses its initialized HAL handle. CPU D-cache remains supported. */
    npu_cache_disable();
    NetworkAtonbuf_Load();
    LL_ATON_RT_RuntimeInit();
    LL_ATON_RT_Init_Network(&NN_Instance_Default);
    input_info = LL_ATON_Input_Buffers_Info(&NN_Instance_Default);
    output_info = LL_ATON_Output_Buffers_Info(&NN_Instance_Default);
    if (!input_info || !output_info || !input_info[0].name || !output_info[0].name ||
        input_info[0].offset_end - input_info[0].offset_start != NN_PREPROCESS_INPUT_FEATURES ||
        output_info[0].offset_end - output_info[0].offset_start != 2) {
        return BODYGUARD_ML_INIT_ERROR;
    }
#else
    __HAL_RCC_CRC_CLK_ENABLE();
    const ai_handle buffers[] = { AI_HANDLE_PTR(activations) };
    ai_error error = ai_network_cpu_create_and_init(&cpu_network, buffers, NULL);
    if (error.type != AI_ERROR_NONE) { return BODYGUARD_ML_INIT_ERROR; }
    cpu_input = ai_network_cpu_inputs_get(cpu_network, NULL);
    cpu_output = ai_network_cpu_outputs_get(cpu_network, NULL);
    if (!cpu_input || !cpu_output || !cpu_input[0].data || !cpu_output[0].data) {
        return BODYGUARD_ML_INIT_ERROR;
    }
#endif
    ready = 1;
    return BODYGUARD_ML_OK;
}

static void reset_result(bodyguard_ml_result_t *result)
{
    memset(result, 0, sizeof(*result));
    result->backend = BODYGUARD_ML_BACKEND;
    result->predicted_class = UINT32_MAX;
    result->status = BODYGUARD_ML_NOT_READY;
}

int32_t BodyguardML_RunInt8(const int8_t input[NN_PREPROCESS_INPUT_FEATURES], bodyguard_ml_result_t *result)
{
    if (!result) { return BODYGUARD_ML_BAD_ARGUMENT; }
    reset_result(result);
    if (!input) { return result->status = BODYGUARD_ML_BAD_ARGUMENT; }
    if (!ready) { return result->status; }
    uint32_t start = HAL_GetTick();
#if BODYGUARD_ML_BACKEND == BODYGUARD_ML_NPU
    LL_ATON_RT_Reset_Network(&NN_Instance_Default);
    void *destination = LL_Buffer_addr_start(input_info);
    memcpy(destination, input, NN_PREPROCESS_INPUT_FEATURES);
    clean_cpu_cache(destination, NN_PREPROCESS_INPUT_FEATURES);
    LL_ATON_RT_RetValues_t state;
    do {
        state = LL_ATON_RT_RunEpochBlock(&NN_Instance_Default);
        if (state != LL_ATON_RT_DONE && HAL_GetTick() - start >= BODYGUARD_ML_TIMEOUT_MS) {
            LL_ATON_RT_RuntimeDeInit();
            ready = 0; /* Fail closed; reset/reinitialize before another run. */
            result->inference_ms = HAL_GetTick() - start;
            return result->status = BODYGUARD_ML_TIMEOUT;
        }
        /* Poll with interrupts enabled so a missing NPU interrupt times out.
         * Generated epochs maintain coherency at each SW/HW boundary. */
    } while (state != LL_ATON_RT_DONE);
    memcpy(result->quantized_output, LL_Buffer_addr_start(output_info), 2);
#else
    memcpy(cpu_input[0].data, input, NN_PREPROCESS_INPUT_FEATURES);
    if (ai_network_cpu_run(cpu_network, cpu_input, cpu_output) != 1) {
        result->inference_ms = HAL_GetTick() - start;
        return result->status = BODYGUARD_ML_RUN_ERROR;
    }
    memcpy(result->quantized_output, cpu_output[0].data, 2);
#endif
    result->inference_ms = HAL_GetTick() - start;
    /* Both conversions of final_model_int8.tflite: scale 1/256, zero point -128. */
    for (uint32_t i = 0; i < 2; ++i) {
        result->probability[i] = ((int32_t)result->quantized_output[i] + 128) / 256.0f;
    }
    result->predicted_class = result->quantized_output[1] > result->quantized_output[0] ? 1u : 0u;
    return result->status = BODYGUARD_ML_OK;
}

int32_t BodyguardML_RunADC(const uint8_t adc[BODYGUARD_ADC_SAMPLES], bodyguard_ml_result_t *result)
{
    if (!result) { return BODYGUARD_ML_BAD_ARGUMENT; }
    reset_result(result);
    if (!adc) { return result->status = BODYGUARD_ML_BAD_ARGUMENT; }
    if (!ready) { return result->status; }
    nn_preprocess_info_t info;
    uint32_t start = HAL_GetTick();
    nn_preprocess_status_t status = nn_preprocess_adc_u8_to_int8(
        adc, quantized_input, &preprocessing_workspace, &info);
    uint32_t elapsed = HAL_GetTick() - start;
    if (status != NN_PREPROCESS_OK) { return result->status = BODYGUARD_ML_PREPROCESS_ERROR; }
    int32_t ret = BodyguardML_RunInt8(quantized_input, result);
    result->preprocess_ms = elapsed;
    result->preprocessing = info;
    return ret;
}
