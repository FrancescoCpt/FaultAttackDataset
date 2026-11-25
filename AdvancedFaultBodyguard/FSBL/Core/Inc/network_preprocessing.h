#ifndef NETWORK_PREPROCESSING_H
#define NETWORK_PREPROCESSING_H

#include <stdint.h>
#include <stddef.h>
#include "bodyguard_ml_config.h"

#ifdef __cplusplus
extern "C" {
#endif

#define NN_PREPROCESS_ADC_SAMPLES          BODYGUARD_ADC_SAMPLES
#define NN_PREPROCESS_BASELINE_SAMPLES       300u
#define NN_PREPROCESS_EVENT_SEARCH_START       0u
#define NN_PREPROCESS_EVENT_SEARCH_END      1200u
#define NN_PREPROCESS_EVENT_SMOOTH            31u
#define NN_PREPROCESS_CROP_PRE               600u
#define NN_PREPROCESS_CROP_POST             2600u
#define NN_PREPROCESS_CROP_SAMPLES          3200u
#define NN_PREPROCESS_INPUT_FEATURES        3199u

/* Quantizzazione dell'input letta da aton_conversion/network.c:
 * scale = 0.255109578371048, zero point = -1, dtype = int8.
 */
#define NN_PREPROCESS_INPUT_SCALE      0.255109578371048f
#define NN_PREPROCESS_INPUT_ZERO_POINT (-1)

typedef enum
{
    NN_PREPROCESS_OK = 0,
    NN_PREPROCESS_NULL_POINTER = -1,
    NN_PREPROCESS_BAD_IQR = -2
} nn_preprocess_status_t;

typedef struct
{
    uint16_t event_index;
    float baseline;
    float derivative_median;
    float derivative_q25;
    float derivative_q75;
    float derivative_iqr;
    float feature_min;
    float feature_max;
    uint32_t clipped_low;
    uint32_t clipped_high;
} nn_preprocess_info_t;

typedef struct
{
    float crop[NN_PREPROCESS_CROP_SAMPLES];
    float derivative[NN_PREPROCESS_INPUT_FEATURES];
    float sort_buffer[NN_PREPROCESS_INPUT_FEATURES];
} nn_preprocess_workspace_t;

/* Converte il buffer ADC grezzo a 8 bit nel tensore int8 atteso dalla rete.
 * Layout di output: NHWC flatten di shape [1, 1, 3199, 1]. Dato che H=C=1,
 * il buffer e' semplicemente una sequenza di 3199 int8.
 */
nn_preprocess_status_t nn_preprocess_adc_u8_to_int8(
    const uint8_t adc_buffer[NN_PREPROCESS_ADC_SAMPLES],
    int8_t network_input[NN_PREPROCESS_INPUT_FEATURES],
    nn_preprocess_workspace_t *workspace,
    nn_preprocess_info_t *info);

/* Variante diagnostica: produce le feature float prima della quantizzazione int8. */
nn_preprocess_status_t nn_preprocess_adc_u8_to_float(
    const uint8_t adc_buffer[NN_PREPROCESS_ADC_SAMPLES],
    float network_input_float[NN_PREPROCESS_INPUT_FEATURES],
    nn_preprocess_workspace_t *workspace,
    nn_preprocess_info_t *info);

#ifdef __cplusplus
}
#endif

#endif /* NETWORK_PREPROCESSING_H */
